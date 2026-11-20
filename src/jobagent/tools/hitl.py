"""Human-in-the-loop requests through a file queue in state/hitl/.

The agent writes a request and waits; the user answers from any terminal with
`jobagent hitl answer` (or `jobagent hitl done` after finishing something in noVNC).
Secret answers go straight into the vault; only a marker is written to the answer file.
"""

import json
import os
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

SECRET_MARKER = "<stored in vault>"


def process_namespace():
    try:
        return os.readlink('/proc/self/ns/pid')
    except OSError:
        return ''


def submission_prompt(prompt):
    """Recognize approval requests written before terminal_only was recorded."""
    return bool(re.search(r"answer\s+['\"]approve['\"]\s+to\s+submit", prompt, re.I))


class HitlTimeout(TimeoutError):
    pass


CHOICE_KINDS = {"select", "combobox", "radio", "buttons", "checkboxes"}


def question_spec(f, *, id=None, quick: list[str] | None = None) -> dict:
    """Describe one form field as a question for a screen.

    Args:
        f: A form field or a dict with the same keys (`id`, `kind`, `label`, `options`, `required`; `section`, `help`,
            `multiple`, `value`, `options_state` are optional).
        id: Override the question id (default: the field's own id).
        quick: Short replies worth a button of their own (`done`, `skip`, `keep`).

    Returns:
        `{"id", "label", "kind", "options", "required", "section", "help", "multiple", "options_complete", "current", "quick"}`.
        `kind` is one of text, textarea, email, tel, url, number, date, select, combobox, radio, checkboxes, checkbox, file, other.
    """
    def get(name, default=None):
        """A key of a dict or an attribute of a field object."""
        return f.get(name, default) if isinstance(f, dict) else getattr(f, name, default)
    kind = str(get("kind", "") or "text")
    kind = {"buttons": "radio", "spin": "number", "date_parts": "date"}.get(kind, kind)
    if kind not in {"text", "textarea", "email", "tel", "url", "number", "date", "select", "combobox", "radio", "checkboxes", "checkbox", "file"}:
        kind = "other"
    options = [str(o)[:200] for o in (get("options") or [])][:200]
    return {"id": str(id if id is not None else get("id", "")), "label": str(get("label", ""))[:400], "kind": kind,
            "options": options, "required": bool(get("required", False)), "section": str(get("section", "") or "")[:120],
            "help": str(get("help", "") or "")[:300], "multiple": bool(get("multiple", False)) or kind == "checkboxes",
            "options_complete": get("options_state", "complete") == "complete",
            "current": str(get("value", "") or "")[:200], "quick": list(quick or [])}


def wire_answer(spec: dict, value) -> str:
    """The text the agent understands for one answer.

    Args:
        spec: A question as built by `question_spec`.
        value: What the candidate chose or typed: text, a list for several choices, a bool for a single checkbox, or None to skip.

    Returns:
        The answer text; `skip` for None, `Yes`/`No` for a checkbox, `a; b` for several choices.

    Raises:
        ValueError: The value is not one of the offered options of a closed choice.
    """
    if value is None or value == "" or value == []:
        return "skip"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    items = [str(v).strip() for v in value] if isinstance(value, list) else [str(value).strip()]
    for word in spec.get("quick") or []:                      # a button word ("skip", "done", "keep") is always a valid reply
        if len(items) == 1 and items[0].casefold() == str(word).casefold():
            return str(word)
    options = spec.get("options") or []
    closed = spec.get("kind") in {"select", "radio", "checkboxes"} and options and spec.get("options_complete", True)
    if closed:
        by_fold = {o.casefold(): o for o in options}
        fixed = []
        for it in items:
            if it.casefold() not in by_fold:
                raise ValueError(f"{it[:60]!r} is not one of the options for {spec.get('label', '')[:60]!r}")
            fixed.append(by_fold[it.casefold()])
        items = fixed
    if not spec.get("multiple") and len(items) > 1:
        raise ValueError(f"only one choice is allowed for {spec.get('label', '')[:60]!r}")
    return "; ".join(items)


def compose_reply(req: "Request", body: dict) -> str:
    """Build the reply the waiting agent expects from what the dashboard sent.

    Args:
        req: The pending request.
        body: `{"value": text}` for a plain prompt, or `{"answers": {question id: value}}` for a structured one.

    Returns:
        The answer string (text format) or a JSON object string (json format).

    Raises:
        ValueError: An answer is not acceptable (unknown question, option not offered, secret asked).
    """
    if not req.questions:
        value = body.get("value")
        if not isinstance(value, str) or len(value) > 5000:
            raise ValueError("give the question id and a text answer (at most 5000 characters)")
        return value
    answers = body.get("answers")
    if not isinstance(answers, dict) or len(json.dumps(answers)) > 20000:
        raise ValueError("give the answers as an object mapping each question id to its answer")
    specs = {str(q["id"]): q for q in req.questions}
    stray = [k for k in answers if str(k) not in specs]
    if stray:
        raise ValueError(f"unknown question id {str(stray[0])[:20]!r}")
    if req.reply_format == "json":
        out = {}
        for qid, spec in specs.items():
            if qid in answers and answers[qid] not in (None, "", []):
                text = wire_answer(spec, answers[qid])
                out[qid] = None if text == "skip" else text
            else:
                out[qid] = None
        return json.dumps(out, ensure_ascii=False)
    if len(specs) != 1:
        raise ValueError("a text reply can answer one question only")
    qid, spec = next(iter(specs.items()))
    rule = body.get("rule")
    if isinstance(rule, str) and rule.strip():
        return "rule: " + rule.strip()[:1000]
    return wire_answer(spec, answers.get(qid))


@dataclass
class Request:
    id: str
    prompt: str
    secret: bool = False
    default: str | None = None
    # For secrets: where `jobagent hitl answer` stores the value, e.g. ["gmail", "app_password"].
    vault_target: list[str] | None = None
    created: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))
    # Process waiting for the answer; requests of dead processes (killed runs) are pruned.
    pid: int = field(default_factory=os.getpid)
    pid_namespace: str = ''
    terminal_only: bool = False
    context: dict = field(default_factory=dict)
    # What is asked, in a form a screen can draw: [{id, label, kind, options, required, section, help, multiple}] (see `question_spec`).
    # Empty for a plain prompt (a request written by older code, or one that only wants "done").
    questions: list = field(default_factory=list)
    # How the answer travels back: "text" = one answer string; "json" = an object mapping each question id to its answer.
    reply_format: str = "text"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class Hitl:
    def __init__(self, root: Path, notify: Callable[[str], None] = print):
        self.pending, self.answers = root / "pending", root / "answers"
        self.pending.mkdir(parents=True, exist_ok=True)
        self.answers.mkdir(parents=True, exist_ok=True)
        self.notify = notify

    # --- agent side -------------------------------------------------------------
    def ask(
        self,
        prompt: str,
        *,
        secret: bool = False,
        default: str | None = None,
        vault_target: list[str] | None = None,
        done_when: Callable[[], bool] | None = None,
        timeout_s: float | None = None,
        poll_s: float = 2.0,
        terminal_only: bool = False,
        questions: list[dict] | None = None,
        reply_format: str = "text",
        context: dict | None = None,
    ) -> str:
        """Block until the user answers (or `done_when()` turns true). Returns the answer ("" if auto-resolved).

        Args:
            prompt: The question as text (always shown, and answerable from a terminal).
            secret: The answer is a secret (stored in the vault, never typed into the dashboard).
            default: What an empty answer means.
            vault_target: `[name, field]` where a secret goes.
            done_when: Returns true when the question no longer needs an answer.
            timeout_s: Give up after this long (`HitlTimeout`).
            poll_s: How often to look for the answer.
            terminal_only: Only a terminal may answer (submission approval).
            questions: What is asked, drawable on a screen (see `question_spec`): the dashboard shows its type and options.
            reply_format: `text` (one answer string) or `json` (an object mapping question ids to answers).
            context: Extra facts for the screen (company, role); merged over the run's own.

        Returns:
            The answer text.
        """
        from jobagent.progress import metadata
        context = {**metadata(), **{k: v for k, v in (context or {}).items() if v}}
        req = Request(id=uuid.uuid4().hex[:8], prompt=prompt, secret=secret, default=default, vault_target=vault_target,
                      terminal_only=terminal_only or submission_prompt(prompt), context=context,
                      pid_namespace=process_namespace(), questions=list(questions or []), reply_format=reply_format)
        _write_atomic(self.pending / f"{req.id}.json", json.dumps(asdict(req)))
        from jobagent.log import host_command, progress, _current
        logger = self.notify if callable(getattr(self.notify, 'progress', None)) else _current.get()
        stage = context.get('stage')
        if stage:
            progress(logger, stage, 'waiting', 'Waiting for your answer', question_id=req.id)
        # the form you can paste on the host; a secret is typed at a hidden prompt, so that command needs stdin and a terminal
        hint = host_command(["jobagent", "hitl", "answer", req.id] + ([] if secret else ["<answer>"]), interactive=secret)
        try:
            self.notify(f"[HITL {req.id}] {prompt}" + (f" (default: {default})" if default else "") + f"\n  → answer with: {hint}")
        except Exception as exc:                          # the question stays answerable even when showing it failed
            import sys
            print(f"hitl: could not show question {req.id} ({type(exc).__name__}: {exc}); answer with: {hint}", file=sys.stderr)
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        try:
            while True:
                answer_file = self.answers / f"{req.id}.json"
                if answer_file.exists():
                    value = json.loads(answer_file.read_text())["value"]
                    answer_file.unlink()
                    return default if value == "" and default is not None else value
                if done_when is not None and done_when():
                    return ""
                if deadline is not None and time.monotonic() > deadline:
                    raise HitlTimeout(f"no answer to HITL {req.id} within {timeout_s}s: {prompt}")
                time.sleep(poll_s)
        finally:
            (self.pending / f"{req.id}.json").unlink(missing_ok=True)
            if stage:
                progress(logger, stage, 'running', 'Resuming after question', question_id=req.id)

    # --- user side --------------------------------------------------------------
    def list_pending(self) -> list[Request]:
        reqs = []
        for p in self.pending.glob("*.json"):
            try:
                req = Request(**json.loads(p.read_text()))
            except (json.JSONDecodeError, TypeError, FileNotFoundError):
                continue  # being written or removed right now; seen on the next poll
            if req.pid_namespace and req.pid_namespace != process_namespace():
                reqs.append(req)  # the filesystem queue can be answered across PID namespaces
                continue
            if _alive(req.pid):
                reqs.append(req)
            else:  # the asking run was killed before it could clean up
                p.unlink(missing_ok=True)
        return sorted(reqs, key=lambda r: r.created)

    def answer(self, req_id: str | None, value: str) -> Request:
        reqs = self.list_pending()
        if not reqs:
            raise LookupError("no pending HITL requests")
        req = next((r for r in reqs if r.id == req_id), None) if req_id else reqs[0]
        if req is None:
            raise LookupError(f"no pending HITL request {req_id!r}")
        if req.secret and req.vault_target:
            from jobagent.tools.vault import open_vault

            name, fld = req.vault_target
            open_vault().set(name, **{fld: value})
            value = SECRET_MARKER
        _write_atomic(self.answers / f"{req.id}.json", json.dumps({"value": value}))
        return req


def _write_atomic(path: Path, text: str) -> None:
    """Write via a temp file + rename so a concurrent reader never sees a half-written request/answer."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def open_hitl(notify: Callable[[str], None] = print) -> Hitl:
    from jobagent.config import get_settings

    return Hitl(get_settings().state_dir / "hitl", notify)
