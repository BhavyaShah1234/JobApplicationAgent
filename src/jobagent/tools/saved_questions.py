"""The job-application questions the agent asked the candidate, saved per application, and replayed on a rerun.

Only FORM questions are saved: the batches the filler sends through HITL ("<company> application needs these answers ...",
followed by a JSON list of {id, label, section, kind, help, options, format, ...}) and the original filler's single question
("<company> application asks: "..."). Bot checks, CAPTCHAs, passwords and submission approval never have that shape, and the
caller never saves secret or terminal-only requests.

`<job_dir>/application/questions.json` keeps one record per question IDENTITY: section, label, kind, help, options, format and
whether it is required,
stored losslessly. A question is open until it is answered, live or later on the dashboard. When the same application asks the
same question again (a rerun), `Hitl.ask` answers it from here without prompting the candidate, present or away; the reply has
the same shape as a live one, so the filler handles it exactly like a live answer. A question whose identity changed (other help
text, other options, another section or employer row) is a different question: it is asked (or, while away, deferred), never
answered with the old reply. The candidate's own `answers.md` is not touched.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

BATCH = re.compile(r"application needs these answers\b", re.I)
SINGLE = re.compile(r'application asks: "(?P<label>[^"]{1,250})"', re.I)
IDENTITY = ("section", "label", "kind", "help", "options", "format", "required")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def identity(q: dict) -> str:
    """A stable id for exactly this question: any change in its section, wording, kind, help, options, format or whether it is
    required is a new id."""
    blob = json.dumps({k: (bool(q.get(k)) if k == "required" else q.get(k) or ([] if k == "options" else "")) for k in IDENTITY},
                      sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()[:20]


def key_of(q: dict) -> str:
    """The saved record id of a question (its identity)."""
    return q.get("qid") or identity(q)


def parse(prompt: str) -> list[dict] | None:
    """The form questions in a HITL prompt (full text, every option, help and format kept), or None when the prompt is not a
    job-application question. Each question keeps the request's own dict as `raw`, to rebuild a smaller request."""
    head, _, rest = (prompt or "").partition("\n")
    if BATCH.search(head):
        try:
            items = json.loads(rest)
        except ValueError:
            return None
        if isinstance(items, list) and items and all(isinstance(q, dict) and "label" in q and "id" in q for q in items):
            out = []
            for q in items:
                row = {"id": q["id"], "label": str(q["label"]), "section": str(q.get("section") or ""),
                       "kind": str(q.get("kind") or ""), "required": bool(q.get("required")), "help": str(q.get("help") or ""),
                       "options": [str(o) for o in (q.get("options") or [])], "format": str(q.get("format") or ""), "raw": q}
                row["qid"] = identity(row)
                out.append(row)
            return out
        return None
    m = SINGLE.search(prompt or "")
    if m:
        row = {"id": "0", "label": m["label"], "section": "", "kind": "", "required": True, "help": "", "options": [],
               "format": "", "raw": None, "single": True}
        row["qid"] = identity(row)
        return [row]
    return None


def values(questions: list[dict], reply) -> dict[str, str]:
    """{qid: answer} from a HITL reply as the filler reads it (a JSON object of id -> answer, or plain text for one question).
    null / skip is not an answer; an explicit "" is (an optional field left blank on purpose); "rule: ..." replies are rules
    for every company (learned.yaml), not this application's answer. Text is kept exactly as typed."""
    if not isinstance(reply, str):
        reply = json.dumps(reply)
    if reply.strip().lower() == "skip":
        return {}
    try:
        parsed = json.loads(reply)
    except ValueError:
        parsed = None
    if not isinstance(parsed, dict):                   # plain text for one question: kept exactly as typed, never parsed to a number
        parsed = {str(questions[0]["id"]): reply} if len(questions) == 1 else {}
    out = {}
    for q in questions:
        v = parsed.get(str(q["id"]))
        if v is None:
            continue
        text = ("Yes" if v else "No") if isinstance(v, bool) else "; ".join(map(str, v)) if isinstance(v, list) else str(v)
        if not re.match(r"(?i)^\s*rule\s*:", text):
            out[key_of(q)] = text
    return out


def replies(questions: list[dict], reply) -> dict[str, str]:
    """Same as `values` (kept as the name the callers and tests use)."""
    return values(questions, reply)


def _file(job_dir: Path) -> Path:
    return Path(job_dir) / "application" / "questions.json"


@contextmanager
def _locked(job_dir: Path):
    d = Path(job_dir) / "application"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / ".questions.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def load(job_dir: Path) -> list[dict]:
    try:
        data = json.loads(_file(job_dir).read_text())
        return data.get("questions", []) if isinstance(data, dict) else []
    except (OSError, ValueError):
        return []


def _save(job_dir: Path, rows: list[dict]) -> None:
    f = _file(job_dir)
    fd, tmp = tempfile.mkstemp(dir=f.parent, prefix=".questions.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as out:
            out.write(json.dumps({"questions": rows}, ensure_ascii=False, indent=1))
        os.replace(tmp, f)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def record(job_dir: Path, questions: list[dict], answers: dict[str, str] | None = None, *, by: str = "live",
           asked: bool = True) -> list[dict]:
    """Remember these questions for the application (new identities open) and the answers given ({qid: text}, kept as typed)."""
    twice = ambiguous(questions)
    answers = {k: v for k, v in (answers or {}).items() if k not in twice}
    with _locked(job_dir):
        rows = load(job_dir)
        index = {r["qid"]: r for r in rows if r.get("qid")}
        for q in questions:
            qid = key_of(q)
            row = index.get(qid)
            if row is None:
                row = {"qid": qid, **{k: q.get(k) for k in ("label", "section", "kind", "help", "options", "format", "required")}}
                row["options"] = row["options"] or []
                row.update(status="open", answer=None, first_asked=_now(), times_asked=0)
                rows.append(row)
                index[qid] = row
            if asked:
                row["last_asked"] = _now()
                row["times_asked"] = int(row.get("times_asked") or 0) + 1
        for qid, text in answers.items():
            if qid in index:
                index[qid].update(status="answered", answer=text, answered_at=_now(), answered_by=by)
        _save(job_dir, rows)
    return rows


def ambiguous(questions: list[dict]) -> set[str]:
    """Identities that occur more than once in ONE request: two fields the page did not tell apart (for example two employer
    rows whose section the page does not name). Their answers are never saved or replayed: they are always asked."""
    seen, twice = set(), set()
    for q in questions:
        k = key_of(q)
        (twice if k in seen else seen).add(k)
    return twice


def saved_answers(job_dir: Path, questions: list[dict]) -> dict[str, str]:
    """{qid: answer} for the questions of this request that this application already answered (exact identity only, and never
    for a question the request does not tell apart from another)."""
    known = {r["qid"]: r for r in load(job_dir) if r.get("status") == "answered" and r.get("answer") is not None}
    twice = ambiguous(questions)
    return {key_of(q): known[key_of(q)]["answer"] for q in questions if key_of(q) in known and key_of(q) not in twice}


def reply_for(questions: list[dict], answers: dict[str, str]) -> str:
    """The reply a candidate would have typed: a JSON object of the request's ids -> answers (plain text for the original
    filler's single-question prompt)."""
    if len(questions) == 1 and questions[0].get("single"):
        return answers.get(key_of(questions[0]), "")
    return json.dumps({str(q["id"]): answers[key_of(q)] for q in questions if key_of(q) in answers}, ensure_ascii=False)


def smaller_prompt(prompt: str, remaining: list[dict]) -> str:
    """The same request asking only the questions not answered yet."""
    return prompt.partition("\n")[0] + "\n" + json.dumps([q["raw"] for q in remaining], ensure_ascii=False)


def answer(job_dir: Path, qid: str, text: str, *, by: str = "dashboard") -> dict:
    """The candidate's answer to one saved question of this application (from the dashboard). Blank only for optional ones."""
    if not isinstance(text, str) or len(text) > 5000:
        raise ValueError("give a text answer (at most 5000 characters)")
    with _locked(job_dir):
        rows = load(job_dir)
        row = next((r for r in rows if r.get("qid") == qid), None)
        if row is None:
            raise LookupError("no such saved question for this application")
        if not text.strip() and row.get("required"):
            raise ValueError("this question is required: give an answer")
        row.update(status="answered", answer=text, answered_at=_now(), answered_by=by)
        _save(job_dir, rows)
    return row


def open_questions(outputs_dir: Path, limit: int = 500) -> list[dict]:
    """Every saved, unanswered question across applications (for the dashboard), with the application it belongs to."""
    out = []
    for f in sorted(Path(outputs_dir).glob("*/*/application/questions.json")):
        job_dir = f.parent.parent
        try:
            jd = json.loads((job_dir / "jd.json").read_text())
        except (OSError, ValueError):
            jd = {}
        for r in load(job_dir):
            if r.get("status") == "open" and r.get("qid"):
                out.append({**r, "job": f"{job_dir.parent.name}/{job_dir.name}", "company": jd.get("company") or job_dir.parent.name,
                            "title": jd.get("title") or ""})
                if len(out) >= limit:
                    return out
    return out


def job_dir_for(outputs_dir: Path, job: str) -> Path:
    """`<company>/<job id>` from the dashboard to a folder inside outputs (nothing else)."""
    if not re.fullmatch(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+", job or "") or ".." in job:
        raise ValueError("bad application id")
    d = (Path(outputs_dir) / job).resolve()
    if Path(outputs_dir).resolve() not in d.parents or not d.is_dir():
        raise LookupError("no such application")
    return d
