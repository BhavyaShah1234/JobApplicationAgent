"""What the dashboard shows, read from the agent's own files: the job database (opened read-only), `state/logs/*.jsonl`, `state/hitl`
and each application's result file. Every function returns plain JSON-able data and never raises for a missing file."""

import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

MAX_LOG_BYTES = 2_000_000
STEM = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")
STALLED_AFTER_S = 15 * 60                               # a run with no closing record and no new line for this long is "stalled" (killed?)
IDLE_AFTER_S = 120                                      # ... and "waiting" (a long model call) after this long


def ro_conn(settings) -> sqlite3.Connection | None:
    """The job database, read-only (the dashboard never writes it); None when there is none yet."""
    path = settings.state_dir / "jobs.sqlite"
    if not path.exists():
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _rows(conn, sql: str, args=()) -> list[dict]:
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    except sqlite3.Error:
        return []


def _count(conn, sql: str) -> dict:
    return {r[0] or "": r[1] for r in conn.execute(sql)} if conn else {}


def overview(settings) -> dict:
    conn = ro_conn(settings)
    out = {"now": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "jobs": {}, "applications": {}, "outcomes": {}, "waiting": 0, "ready": 0,
           "questions": len(hitl_pending(settings)), "runs_active": sum(1 for r in runs(settings, 40) if r["running"]),
           "jobs_active": sum(1 for j in pipeline(settings) if j["active"])}
    if conn is None:
        return out
    try:
        out["jobs"] = _count(conn, "SELECT status, COUNT(*) FROM jobs GROUP BY status")
        out["applications"] = _count(conn, "SELECT state, COUNT(*) FROM applications GROUP BY state")
        out["outcomes"] = _count(conn, "SELECT stage, COUNT(*) FROM outcome_events WHERE needs_review = 0 GROUP BY stage")
        rows = review(settings, conn)
        out["waiting"], out["ready"] = len(rows), sum(1 for r in rows if r["ready"])
    except sqlite3.Error:
        pass
    finally:
        conn.close()
    return out


def queue(settings, limit: int = 300) -> list[dict]:
    conn = ro_conn(settings)
    if conn is None:
        return []
    try:
        keep = ("id", "company", "title", "location", "status", "notes", "role_id", "match_score", "claimed_by", "updated_at", "url")
        return [{k: (str(r[k])[:200] if isinstance(r.get(k), str) else r.get(k)) for k in keep if k in r}
                for r in _rows(conn, "SELECT * FROM jobs ORDER BY updated_at DESC, id DESC LIMIT ?", (limit,))]
    finally:
        conn.close()


def applications(settings, limit: int = 300) -> list[dict]:
    conn = ro_conn(settings)
    if conn is None:
        return []
    try:
        rows = _rows(conn, "SELECT a.id, a.company, a.title, a.url, a.portal, a.state, a.filled_at, a.decided_at, a.submitted_at, "
                           "(SELECT stage FROM outcome_events o WHERE o.application_id = a.id AND o.needs_review = 0 "
                           " ORDER BY received_at DESC LIMIT 1) AS latest_stage "
                           "FROM applications a ORDER BY COALESCE(a.submitted_at, a.decided_at, a.filled_at) DESC, a.id DESC LIMIT ?", (limit,))
        return rows
    finally:
        conn.close()


def review(settings, conn=None) -> list[dict]:
    """Applications filled to their review screen and waiting for the candidate (tools/review_queue.py), with the command that opens each."""
    from jobagent.tools import review_queue
    own = conn is None
    conn = conn or ro_conn(settings)
    if conn is None:
        return []
    try:
        return review_queue.waiting(conn, settings.outputs_dir.parent)
    except Exception:
        return []
    finally:
        if own:
            conn.close()


def hitl_pending(settings) -> list[dict]:
    """Questions the agent is waiting on. A secret is listed (so you know to answer it) but is answered only in a terminal."""
    from jobagent.tools.hitl import Hitl
    try:
        reqs = Hitl(settings.state_dir / "hitl", lambda m: None).list_pending()
    except OSError:
        return []
    return [{"id": r.id, "prompt": r.prompt, "secret": bool(r.secret), "default": r.default, "created": r.created,
             "terminal_command": f"jobagent hitl answer {r.id}" + ("" if r.secret else " '<answer>'")} for r in reqs]


def _parse_log(path: Path) -> list[dict]:
    try:
        if path.stat().st_size > MAX_LOG_BYTES:
            raw = path.read_bytes()[-MAX_LOG_BYTES:].split(b"\n", 1)[-1].decode("utf-8", "replace")
        else:
            raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out = []
    for line in raw.splitlines():
        try:
            rec = json.loads(line)
            if isinstance(rec, dict):
                out.append(rec)
        except json.JSONDecodeError:
            continue
    return out


def runs(settings, limit: int = 60) -> list[dict]:
    """Recent runs from the structured logs, newest first. `step` is the command that was run (older logs: the first line's tag), `state` is
    finished (closing record written), running, waiting (no line for 2 minutes: usually a long model call) or stalled (no line for 15 minutes
    and never closed: the run was most likely killed)."""
    d = settings.state_dir / "logs"
    if not d.is_dir():
        return []
    files = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    now = datetime.now().timestamp()
    out = []
    for p in files:
        recs = _parse_log(p)
        body = [r for r in recs if r.get("level") not in ("end", "progress")]
        if not body:
            continue
        levels = [r.get("level") for r in body]
        quiet_s = now - p.stat().st_mtime
        state = ("finished" if any(r.get("level") == "end" for r in recs) else
                 "running" if quiet_s < IDLE_AFTER_S else "waiting" if quiet_s < STALLED_AFTER_S else "stalled")
        out.append({"id": p.stem, "run": body[0].get("run", ""), "step": body[0].get("cmd") or body[0].get("step", ""), "job": body[0].get("job", ""),
                    "started": body[0].get("ts", ""), "last": body[-1].get("ts", ""), "lines": len(body), "errors": levels.count("error"),
                    "warnings": levels.count("warn"), "message": str(body[-1].get("msg", ""))[:160], "state": state,
                    "running": state in ("running", "waiting"), "quiet_s": int(quiet_s)})
    return out


STAGES = [("scrape", "Read posting"), ("research", "Research company"), ("documents", "Resume & cover letter"), ("apply", "Fill the form"),
          ("submit", "Submit")]
DONE_STATES = ("done", "skipped")


def _log_state(path: Path, recs: list[dict], now: float) -> str:
    quiet_s = now - path.stat().st_mtime
    return ("finished" if any(r.get("level") == "end" for r in recs) else
            "running" if quiet_s < IDLE_AFTER_S else "waiting" if quiet_s < STALLED_AFTER_S else "stalled")


def pipeline(settings, hours: float = 72, limit: int = 30) -> list[dict]:
    """One entry per job the agent has worked on recently: which stages its plan has, where each stands, which step inside the current stage
    (the iteration, e.g. "ATS round 2 of 3"), how far along it is overall, and a short trail of what happened. Built from the `progress` records
    in the run logs, so every command that touches a job (run, tailor, apply, ...) contributes to the same entry."""
    d = settings.state_dir / "logs"
    if not d.is_dir():
        return []
    now = datetime.now().timestamp()
    files = [p for p in sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:150] if now - p.stat().st_mtime < hours * 3600]
    parsed = [(p, _parse_log(p)) for p in files]
    url_job: dict[str, str] = {}                         # a record that names both its url and its job links the two names of one job
    for _, recs in parsed:
        for r in recs:
            if r.get("level") == "progress" and r.get("url") and r.get("job"):
                url_job[r["url"]] = r["job"]
    jobs: dict[str, dict] = {}
    for p, recs in parsed:
        state = _log_state(p, recs, now)
        for r in recs:
            if r.get("level") != "progress" or not r.get("stage"):
                continue
            key = r.get("job") or url_job.get(r.get("url", ""), "") or f"url:{r.get('url', '')}"
            j = jobs.setdefault(key, {"job": r.get("job", ""), "url": "", "title": "", "company": "", "plan": [], "stages": {}, "trail": [],
                                      "first": r["ts"], "last": r["ts"], "active": False})
            for k in ("job", "url", "title", "company"):
                if r.get(k):
                    j[k] = r[k]
            if r.get("plan"):
                j["plan"] = r["plan"]
            j["active"] = j["active"] or state in ("running", "waiting")
            j["first"], j["last"] = min(j["first"], r["ts"]), max(j["last"], r["ts"])
            j["trail"].append({"ts": r["ts"], "stage": r["stage"], "status": r.get("status", ""), "label": r.get("label", ""), "i": r.get("i"),
                               "n": r.get("n"), "detail": r.get("detail", "")})
    labels = dict(STAGES)
    out = []
    for j in jobs.values():
        j["trail"].sort(key=lambda e: e["ts"])
        for e in j["trail"]:
            j["stages"][e["stage"]] = e                  # the latest record of each stage wins
        plan = [s for s in (j["plan"] or [k for k, _ in STAGES if k in j["stages"]]) if s in labels]
        for s in list(j["stages"]):
            if s not in plan and s in labels:
                plan.append(s)
        plan.sort(key=lambda s: [k for k, _ in STAGES].index(s))
        stages = []
        for s in plan:
            e = j["stages"].get(s)
            st = e["status"] if e else "pending"
            if st == "running" and not j["active"]:
                st = "stalled"                           # nothing is writing any more and it never said it finished
            stages.append({"id": s, "label": labels[s], "status": st, "step": (e or {}).get("label", ""), "i": (e or {}).get("i"),
                           "n": (e or {}).get("n"), "detail": (e or {}).get("detail", ""), "ts": (e or {}).get("ts", "")})
        done = sum(1 for s in stages if s["status"] in DONE_STATES)
        cur = next((s for s in stages if s["status"] in ("running", "stalled", "failed", "blocked")), None)
        frac = 0.0
        if cur and cur["status"] == "running":
            frac = min(cur["i"] / cur["n"], 0.95) if cur.get("i") and cur.get("n") else 0.4
        bad = next((s for s in stages if s["status"] in ("failed", "blocked", "stalled")), None)
        overall = ("failed" if bad and bad["status"] == "failed" else "blocked" if bad and bad["status"] == "blocked" else "stalled" if bad else
                   "done" if stages and done == len(stages) else "running" if j["active"] else "waiting")
        started = datetime.fromisoformat(j["first"].replace("Z", "+00:00")).timestamp()
        ended = now if j["active"] else datetime.fromisoformat(j["last"].replace("Z", "+00:00")).timestamp()
        out.append({"job": j["job"], "url": j["url"], "title": j["title"] or j["job"] or j["url"], "company": j["company"], "stages": stages,
                    "current": cur["id"] if cur else None, "percent": round(100 * (done + frac) / len(stages)) if stages else 0, "state": overall,
                    "active": j["active"], "started": j["first"], "updated": j["last"], "elapsed_s": int(max(0, ended - started)),
                    "trail": j["trail"][-14:]})
    out.sort(key=lambda x: x["updated"], reverse=True)
    out.sort(key=lambda x: not x["active"])              # active jobs first, each group newest first
    return out[:limit]


def run_log(settings, stem: str, tail: int = 300) -> list[dict] | None:
    """The last `tail` records of one run's log; None for a name that is not a log file here (no path can escape the logs folder)."""
    if not STEM.match(stem or ""):
        return None
    p = settings.state_dir / "logs" / f"{stem}.jsonl"
    if not p.is_file():
        return None
    return [{"ts": r.get("ts", ""), "level": r.get("level", ""), "step": r.get("step", ""), "msg": str(r.get("msg", ""))[:2000]}
            for r in _parse_log(p)[-tail:]]


def outcomes(settings, limit: int = 40) -> dict:
    conn = ro_conn(settings)
    if conn is None:
        return {"counts": {}, "recent": []}
    try:
        return {"counts": _count(conn, "SELECT stage, COUNT(*) FROM outcome_events WHERE needs_review = 0 GROUP BY stage"),
                "recent": _rows(conn, "SELECT o.stage, o.received_at, o.subject, a.company, a.title FROM outcome_events o "
                                      "JOIN applications a ON a.id = o.application_id WHERE o.needs_review = 0 "
                                      "ORDER BY o.received_at DESC LIMIT ?", (limit,))}
    finally:
        conn.close()
