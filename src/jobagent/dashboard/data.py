"""What the dashboard shows, read from the agent's own files: the job database (opened read-only), `state/logs/*.jsonl`, `state/hitl`
and each application's result file. Every function returns plain JSON-able data and never raises for a missing file."""

import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

MAX_LOG_BYTES = 2_000_000
STEM = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")
RUNNING_WITHIN_S = 120                                  # a run log written to in the last two minutes is "running"


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
           "questions": len(hitl_pending(settings)), "runs_active": sum(1 for r in runs(settings, 40) if r["running"])}
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
    """Recent runs from the structured logs, newest first: step, job, when, how it went, and whether it is still writing."""
    d = settings.state_dir / "logs"
    if not d.is_dir():
        return []
    files = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    now = datetime.now().timestamp()
    out = []
    for p in files:
        recs = _parse_log(p)
        if not recs:
            continue
        levels = [r.get("level") for r in recs]
        out.append({"id": p.stem, "run": recs[0].get("run", ""), "step": recs[0].get("step", ""), "job": recs[0].get("job", ""),
                    "started": recs[0].get("ts", ""), "last": recs[-1].get("ts", ""), "lines": len(recs),
                    "errors": levels.count("error"), "warnings": levels.count("warn"), "message": str(recs[-1].get("msg", ""))[:160],
                    "running": now - p.stat().st_mtime < RUNNING_WITHIN_S})
    return out


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
