"""The job-application questions the agent asked the candidate, saved per application so they can be answered later.

Only FORM questions are saved: the batches the filler sends through HITL ("<company> application needs these answers ...",
followed by a JSON list of {id, label, ...}) and the original filler's single question ("<company> application asks: "...").
Bot checks, CAPTCHAs, passwords and submission approval never have that shape and are never saved (the caller also skips
secret and terminal-only requests).

`<job_dir>/application/questions.json` holds one record per question (keyed by its section, label and kind): open until it
is answered, live or later on the dashboard. Every answer is also written as a row of that application's own answer sheet
(`application/answers.md`, `| # | Question | Answer |`), which `apply` already reads first on every run, so a rerun of the
application fills it without asking. Rows the candidate wrote themselves are never rewritten; only rows this module added
(marked in questions.json) are updated.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

BATCH = re.compile(r"application needs these answers\b", re.I)
SINGLE = re.compile(r'application asks: "(?P<label>[^"]{1,250})"', re.I)
SHEET_HEAD = ("# Answers for this application (yours: edit freely). Rows added from the dashboard or a live answer are kept\n"
              "# in sync with application/questions.json; rows you write yourself are never changed.\n\n"
              "| # | Question | Answer |\n|---|---|---|\n")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[*✱⚠]", "", str(text or ""))).strip().lower()


def key_of(q: dict) -> str:
    return f"{_norm(q.get('section'))}|{_norm(q.get('label'))}|{q.get('kind') or ''}"


def parse(prompt: str) -> list[dict] | None:
    """The form questions in a HITL prompt, or None when the prompt is not a job-application question."""
    head, _, rest = (prompt or "").partition("\n")
    if BATCH.search(head):
        try:
            items = json.loads(rest)
        except ValueError:
            return None
        if isinstance(items, list) and items and all(isinstance(q, dict) and "label" in q and "id" in q for q in items):
            return [{"id": q["id"], "label": str(q["label"])[:300], "section": str(q.get("section") or "")[:120],
                     "kind": str(q.get("kind") or ""), "required": bool(q.get("required")),
                     "options": [str(o)[:120] for o in (q.get("options") or [])][:60]} for q in items]
        return None
    m = SINGLE.search(prompt or "")
    if m:
        return [{"id": "0", "label": m["label"], "section": "", "kind": "", "required": True, "options": []}]
    return None


def replies(questions: list[dict], reply: str) -> dict[str, str]:
    """{question key: answer text} from a HITL reply, as the filler reads it (a JSON object of id -> answer, or plain text for one
    question). Skipped (null), empty and "rule: ..." replies are not answers for this application."""
    reply = (reply or "").strip()
    if not reply or reply.lower() == "skip":
        return {}
    try:
        values = json.loads(reply)
    except ValueError:
        values = reply
    if not isinstance(values, dict):
        values = {str(questions[0]["id"]): values} if len(questions) == 1 else {}
    out = {}
    for q in questions:
        v = values.get(str(q["id"]))
        if v is None:
            continue
        text = ("Yes" if v else "No") if isinstance(v, bool) else "; ".join(map(str, v)) if isinstance(v, list) else str(v)
        if text.strip() and not re.match(r"(?i)^\s*rule\s*:", text):
            out[key_of(q)] = text.strip()
    return out


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
    tmp = f.with_name(f".questions.json.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"questions": rows}, ensure_ascii=False, indent=1))
    os.replace(tmp, f)


def record(job_dir: Path, questions: list[dict], answers: dict[str, str] | None = None, *, by: str = "live", asked: bool = True) -> list[dict]:
    """Remember these questions for the application (new ones open), and the answers given (written to answers.md too)."""
    answers = answers or {}
    with _locked(job_dir):
        rows = load(job_dir)
        index = {r["key"]: r for r in rows}
        for q in questions:
            k = key_of(q)
            row = index.get(k)
            if row is None:
                row = {"key": k, "label": q["label"], "section": q.get("section", ""), "kind": q.get("kind", ""),
                       "required": q.get("required", False), "options": q.get("options", []), "status": "open",
                       "answer": None, "first_asked": _now(), "times_asked": 0}
                rows.append(row)
                index[k] = row
            if asked:
                row["last_asked"] = _now()
                row["times_asked"] = int(row.get("times_asked") or 0) + 1
            if q.get("options"):
                row["options"] = q["options"]
        for k, text in answers.items():
            if k in index:
                index[k].update(status="answered", answer=text, answered_at=_now(), answered_by=by)
        _save(job_dir, rows)
        if answers:
            _write_sheet(job_dir, [index[k] for k in answers if k in index])
    return rows


def answer(job_dir: Path, key: str, text: str, *, by: str = "dashboard") -> dict:
    """The candidate's answer to one saved question of this application (from the dashboard)."""
    text = (text or "").strip()
    if not text or len(text) > 5000:
        raise ValueError("give an answer (at most 5000 characters)")
    with _locked(job_dir):
        rows = load(job_dir)
        row = next((r for r in rows if r["key"] == key), None)
        if row is None:
            raise LookupError("no such saved question for this application")
        row.update(status="answered", answer=text, answered_at=_now(), answered_by=by)
        _save(job_dir, rows)
        _write_sheet(job_dir, [row])
    return row


SHEET_LIMIT = 6000          # apply reads only the first 6000 characters of answers.md (steps/apply.py context)


def _cell(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).strip()


def sheet_block(row: dict, rows: list[dict], owner_labels: set[str]) -> str:
    """Why this answer must NOT become a label-only answers.md row ('' = safe). A sheet row has no section, so the filler would
    apply it to every field with that label: an employer's or school's row, or a repeated caption such as City or Start Date,
    would be filled with the wrong answer. Those answers stay in questions.json until there is a typed, section-bound transport."""
    from jobagent.filling.facts import entity_scope, loose_entity_scope
    label, answer = str(row.get("label") or ""), str(row.get("answer") or "")
    if entity_scope(row.get("section") or "") or loose_entity_scope(row.get("section") or ""):
        return "it belongs to one employer, school or reference row"
    if len(_norm(label).split()) < 4 and not label.strip().endswith("?"):
        return "its label is a short caption that other sections can repeat"
    if sum(1 for r in rows if _norm(r.get("label")) == _norm(label)) > 1:
        return "the same label appears in more than one section of this application"
    if _norm(label) in owner_labels:
        return "your own row in answers.md already answers it (yours wins)"
    if "\n" in answer.strip() or "|" in answer or "|" in label:
        return "a multi-line answer or a '|' cannot be written as one sheet row"
    return ""


def _write_sheet(job_dir: Path, rows: list[dict]) -> None:
    """Add or update this module's rows in answers.md; the candidate's own rows are left exactly as they are. An answer that a
    label-only row could misapply (see `sheet_block`), or that would fall past what apply reads, is not written: its record
    says why in `sheet`."""
    sheet = Path(job_dir) / "application" / "answers.md"
    text = sheet.read_text() if sheet.exists() else SHEET_HEAD
    lines = text.splitlines()
    every = load(job_dir)
    ours = {r.get("sheet_row") for r in every if r.get("sheet_row")}
    owner_labels = set()
    for ln in lines:
        c = [x.strip() for x in ln.strip().strip("|").split("|")]
        if len(c) >= 3 and c[0].isdigit() and int(c[0]) not in ours:
            owner_labels.add(_norm(c[1]))
    numbers = [int(c[0]) for c in (([x.strip() for x in ln.strip().strip("|").split("|")]) for ln in lines) if len(c) >= 3 and c[0].isdigit()]
    nxt = max(numbers, default=0) + 1
    for row in rows:
        why = sheet_block(row, every, owner_labels)
        if why:
            row["sheet"] = f"not reused automatically: {why}"
            continue
        label, value = _cell(row["label"]), _cell(row["answer"])
        n = row.get("sheet_row")
        at = next((i for i, ln in enumerate(lines) if n and re.match(rf"^\|\s*{n}\s*\|", ln.strip())), None)
        line = f"| {n or nxt} | {label} | {value} |"
        if at is not None:
            candidate = lines[:at] + [line] + lines[at + 1:]
        else:
            candidate = lines + ([] if any(ln.strip().startswith("|") for ln in lines) else SHEET_HEAD.splitlines()[-2:]) + [line]
        if len("\n".join(candidate[:candidate.index(line) + 1])) > SHEET_LIMIT:
            row["sheet"] = "not reused automatically: answers.md is too long (the agent reads its first 6000 characters)"
            continue
        lines = candidate
        row["sheet"] = "written"
        if at is None:
            row["sheet_row"] = nxt
            nxt += 1
    tmp = sheet.with_name(f".answers.md.{os.getpid()}.tmp")
    tmp.write_text("\n".join(lines) + "\n")
    os.replace(tmp, sheet)
    # sheet_row numbers and the sheet outcome were set above: persist them so the next answer updates the same row
    saved = load(job_dir)
    by_key = {r["key"]: r for r in rows}
    for r in saved:
        if r["key"] in by_key:
            for k in ("sheet_row", "sheet"):
                if by_key[r["key"]].get(k):
                    r[k] = by_key[r["key"]][k]
    _save(job_dir, saved)


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
            if r.get("status") == "open":
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
