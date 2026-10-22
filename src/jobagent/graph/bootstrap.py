"""Global default action (the SystemMessage): make sure the harness and accounts are ready before a trigger.

A LangGraph graph of idempotent nodes — harness → gmail → browser → linkedin → overleaf. Each node checks
first and only acts when something is missing, recording what it changed. Optional accounts (LinkedIn,
Overleaf, the browser's Google session) only produce warnings. Progress is checkpointed in
state/checkpoints.sqlite: a run that crashes mid-way is resumed from the node that didn't finish.
"""

import operator
import os
import sqlite3
import uuid
from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage, SystemMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from jobagent import db
from jobagent.config import get_settings

NODES = ["harness", "gmail", "browser", "linkedin", "overleaf"]
THREAD_KEY = "bootstrap_thread"
# Test hook: raise right after this node finished (used by the crash/resume check).
CRASH_ENV = "JOBAGENT_BOOTSTRAP_CRASH_AFTER"


class BootstrapState(TypedDict):
    ran: Annotated[list[str], operator.add]          # nodes executed (across resumes)
    changed: Annotated[list[str], operator.add]      # actions that modified something
    warnings: Annotated[list[str], operator.add]
    status: Annotated[dict, operator.or_]            # node -> short status
    messages: Annotated[list[AnyMessage], add_messages]


class BootstrapCrash(RuntimeError):
    pass


_log = print  # set per run


def _done(node: str, status: str, changed: list[str] | None = None, warnings: list[str] | None = None) -> dict:
    _log(f"bootstrap: {node}: {status}")
    if os.environ.get(CRASH_ENV) == node:
        raise BootstrapCrash(f"simulated crash after {node}")
    return {"ran": [node], "changed": changed or [], "warnings": warnings or [], "status": {node: status}}


# --- nodes ----------------------------------------------------------------------------------------

def harness(_: BootstrapState) -> dict:
    from jobagent.tools.capabilities import HEADER
    from jobagent.tools.vault import open_vault

    s = get_settings()
    for d in (s.data_dir, s.state_dir, s.outputs_dir):
        probe = d / ".write-probe"
        probe.write_text("ok")
        probe.unlink()
    open_vault().names()  # raises clearly if the vault is unreadable
    changed = []
    if not s.missing_capabilities_path.exists():
        s.missing_capabilities_path.write_text(HEADER)
        changed.append("created missing-agent-capabilities.txt")
    return _done("harness", "ok", changed)


def gmail(_: BootstrapState) -> dict:
    from jobagent.steps import gmail as gmail_step
    from jobagent.tools.inbox import Inbox
    from jobagent.tools.vault import open_vault

    entry = open_vault().get("gmail") or {}
    if entry.get("status") == "ready" and entry.get("app_password"):
        with Inbox(entry["email"], entry["app_password"]) as inbox:
            inbox.imap()  # login works
        return _done("gmail", f"ok ({entry['email']}, IMAP login)")
    if not entry.get("email"):
        raise RuntimeError("no agent email configured: run `jobagent step gmail --existing <address>` once")
    gmail_step.setup(existing_email=entry["email"], log=_log)
    return _done("gmail", f"set up ({entry['email']})", [f"gmail set up for {entry['email']}"])


def browser(_: BootstrapState) -> dict:
    from jobagent.steps.browser import google_session_state
    from jobagent.tools.browser import clear_stale_lock
    from jobagent.tools.vault import open_vault

    s = get_settings()
    changed = []
    if clear_stale_lock(s.chrome_profile_dir) != "none":
        changed.append("removed a stale Chrome profile lock")
    state = google_session_state(s)
    vault = open_vault()
    if (vault.get("gmail") or {}).get("browser_session") != state:
        vault.set("gmail", browser_session=state)
    if state != "ok":
        return _done("browser", "Google session signed out", changed,
                     ["the browser's Google session is signed out: run `jobagent browser google-login` (by hand, once)"])
    return _done("browser", "ok (Gmail opens signed in)", changed)


def linkedin(_: BootstrapState) -> dict:
    from jobagent.tools.vault import open_vault

    if (open_vault().get("linkedin") or {}).get("status") == "ready":
        return _done("linkedin", "ok")
    return _done("linkedin", "not set up (skipped)", warnings=["LinkedIn isn't set up (optional; step 3 was skipped)"])


def overleaf(_: BootstrapState) -> dict:
    from jobagent.steps import overleaf as overleaf_step
    from jobagent.tools import overleaf as ol
    from jobagent.tools.browser import persistent_context

    with persistent_context() as ctx:
        if ol.logged_in(ctx.pages[0]):
            return _done("overleaf", "ok (logged in)")
    try:
        overleaf_step.setup(log=_log)
        return _done("overleaf", "logged in", ["logged in to Overleaf"])
    except Exception as e:  # optional: never block the agent
        return _done("overleaf", "unavailable", warnings=[f"Overleaf unavailable ({type(e).__name__}: {e})"])


def summarize(state: BootstrapState) -> dict:
    lines = [f"{n}: {state['status'].get(n, '?')}" for n in NODES]
    if state["warnings"]:
        lines += [f"warning: {w}" for w in state["warnings"]]
    return {"messages": [SystemMessage("Harness bootstrap\n" + "\n".join(lines))]}


def build(checkpointer) -> object:
    g = StateGraph(BootstrapState)
    for name, fn in (("harness", harness), ("gmail", gmail), ("browser", browser),
                     ("linkedin", linkedin), ("overleaf", overleaf), ("summarize", summarize)):
        g.add_node(name, fn)
    g.add_edge(START, "harness")
    for a, b in zip(NODES, NODES[1:] + ["summarize"]):
        g.add_edge(a, b)
    g.add_edge("summarize", END)
    return g.compile(checkpointer=checkpointer)


def run(log=print) -> dict:
    """Run (or resume) the bootstrap. Returns the final state plus `resumed` and `ran_now`."""
    global _log
    _log = log
    s = get_settings()
    kv = db.connect()
    row = kv.execute("SELECT value FROM kv WHERE key = ?", (THREAD_KEY,)).fetchone()
    resumed = row is not None
    thread_id = row[0] if resumed else f"bootstrap-{uuid.uuid4().hex[:12]}"
    if not resumed:
        with kv:
            kv.execute("INSERT INTO kv (key, value) VALUES (?, ?)", (THREAD_KEY, thread_id))
    conn = sqlite3.connect(s.state_dir / "checkpoints.sqlite", check_same_thread=False)
    try:
        graph = build(SqliteSaver(conn))
        config = {"configurable": {"thread_id": thread_id}}
        before = list((graph.get_state(config).values or {}).get("ran", [])) if resumed else []
        if resumed:
            log(f"bootstrap: resuming {thread_id} after {', '.join(before) or 'nothing'}")
            final = graph.invoke(None, config)
        else:
            final = graph.invoke({"ran": [], "changed": [], "warnings": [], "status": {}, "messages": []}, config)
    finally:
        conn.close()
    with kv:  # finished: the next run starts fresh
        kv.execute("DELETE FROM kv WHERE key = ?", (THREAD_KEY,))
    final = dict(final)
    final["resumed"] = resumed
    final["ran_now"] = final["ran"][len(before):]
    return final
