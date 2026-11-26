"""Whether the candidate is available to answer the agent's questions (the dashboard's "I'm available / I'm away" toggle).

Available (the default, and what a missing or unreadable flag means): the agent asks and waits, as it always has.
Away: the agent does not ask. `Hitl.ask` records a DEFERRAL for the job it is working on and raises `hitl.CandidateAway`
(a `HitlTimeout`, so every existing handler stops the question as it stops an unanswered one); the
queue runner puts that job back at the end of the queue (`jobs.deferred_at`) and goes on with the next one. Deferred jobs
are not taken while the candidate is away and are resumed, in the order they were deferred, once the toggle is on again.

State lives in `state/hitl/`: `presence.json` (the flag) and `deferrals.jsonl` (one line per deferral: the job's url, when,
and the question that was not asked). Both are written atomically; readers never see half a file.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path


def _root(state_dir: Path | None = None) -> Path:
    if state_dir is None:
        from jobagent.config import get_settings
        state_dir = get_settings().state_dir
    return Path(state_dir) / "hitl"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def status(state_dir: Path | None = None) -> dict:
    """{available: bool, changed_at: str|None, changed_by: str|None}. Missing or unreadable flag = available."""
    try:
        data = json.loads((_root(state_dir) / "presence.json").read_text())
        return {"available": bool(data.get("available", True)), "changed_at": data.get("changed_at"),
                "changed_by": data.get("changed_by")}
    except (OSError, ValueError, AttributeError):
        return {"available": True, "changed_at": None, "changed_by": None}


def available(state_dir: Path | None = None) -> bool:
    return status(state_dir)["available"]


def set_available(value: bool, by: str = "dashboard", state_dir: Path | None = None) -> dict:
    root = _root(state_dir)
    root.mkdir(parents=True, exist_ok=True)
    data = {"available": bool(value), "changed_at": _now(), "changed_by": by}
    fd, tmp = tempfile.mkstemp(dir=root, prefix=".presence.", suffix=".tmp")   # one private temp file per write (dashboard threads)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(data))
        os.replace(tmp, root / "presence.json")
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return data


def record_deferral(context: dict, prompt: str, state_dir: Path | None = None) -> dict:
    """Note that the job being worked on (the progress context's `url` and `job_dir`) stopped on a question while the candidate
    was away (one appended JSON line)."""
    root = _root(state_dir)
    root.mkdir(parents=True, exist_ok=True)
    row = {"url": str(context.get("url") or ""), "job_dir": str(context.get("job_dir") or ""), "at": _now(),
           "question": (prompt or "")[:300]}
    with open(root / "deferrals.jsonl", "a", encoding="utf-8") as f:   # one short line per write: appends do not interleave
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def cursor(state_dir: Path | None = None) -> int:
    """Where the deferral log ends now. The queue runner takes it when it claims a job: only deferrals written after it belong to
    that attempt (an older one of the same job, even in the same second, never does)."""
    try:
        return (_root(state_dir) / "deferrals.jsonl").stat().st_size
    except OSError:
        return 0


def deferred_since(urls, job_dirs, since: int, state_dir: Path | None = None) -> dict | None:
    """The latest deferral written after byte offset `since` of the deferral log (see `cursor`) whose url is one of `urls` or
    whose job folder is one of `job_dirs` (the queue's url and the posting's own source url can differ), or None."""
    urls = {u for u in urls if u}
    dirs = {str(Path(d).resolve()) for d in job_dirs if d}
    try:
        with open(_root(state_dir) / "deferrals.jsonl", "rb") as f:
            f.seek(max(int(since), 0))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return None
    hit = None
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        mine = row.get("url") in urls or (row.get("job_dir") and str(Path(row["job_dir"]).resolve()) in dirs)
        if mine:
            hit = row
    return hit
