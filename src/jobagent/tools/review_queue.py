"""Applications waiting for the candidate's decision (step 29).

An application the agent has filled to its review screen is `filled`: nothing is sent until the candidate approves THAT application
with the typed `approve`. With many applications in flight the candidate needs one list of what is waiting, how ready each is, and the
exact command that opens its review. This only reads; it never approves, never submits."""

import json
from datetime import UTC, datetime
from pathlib import Path

from jobagent.tools.jobqueue import slug


def _age_hours(ts: str | None, now: datetime) -> float | None:
    if not ts:
        return None
    try:
        return round((now - datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)).total_seconds() / 3600, 1)
    except ValueError:
        return None


def _readiness(res) -> tuple[bool, list[str]]:
    """(ready, blockers) from an application result, strictly: a result that is not an object, a readiness that is not exactly True, or
    blockers that are not a list of strings make the application NOT ready, with the reason, instead of failing the whole list."""
    if not isinstance(res, dict):
        return False, ["no result recorded for this application" if res is None else "result file is not valid (not an object)"]
    blockers = res.get("blockers")
    ok_blockers = isinstance(blockers, list) and all(isinstance(b, str) for b in blockers)
    out = list(blockers) if ok_blockers else []
    if blockers is not None and not ok_blockers:
        out.append("result file is not valid (blockers is not a list of text)")
    flag = res.get("ready_to_submit")
    if flag is not True and not out:
        out.append("result file does not say ready_to_submit: true" if flag is None else "result file is not valid (ready_to_submit is not true/false)")
    return flag is True and not out, out


def waiting(conn, root: Path | None = None, now: datetime | None = None) -> list[dict]:
    """Every `filled` (not sent, not skipped) application, oldest first: {id, company, title, job_dir, ready, blockers, age_hours,
    review_command}. `ready` and `blockers` come from the application's own result file (`application/application.json`); an
    application without one is listed as not ready ("no result recorded")."""
    now = now or datetime.now(UTC)
    root = root or Path("/app")
    out = []
    for a in conn.execute("SELECT * FROM applications WHERE state = 'filled' ORDER BY COALESCE(filled_at, '') , id"):
        d = Path(a["job_dir"])
        d = d if d.is_absolute() else root / d
        try:
            res = json.loads((d / "application" / "application.json").read_text())
        except (OSError, ValueError):
            res = None
        ready, blockers = _readiness(res)
        from jobagent.log import host_command
        out.append({"id": a["id"], "company": a["company"], "title": a["title"], "job_dir": a["job_dir"],
                    "ready": ready, "blockers": blockers, "age_hours": _age_hours(a["filled_at"], now),
                    # typed approval needs a terminal; every argument is quoted, so a path with spaces stays one argument
                    "review_command": host_command(["jobagent", "submit", str(a["job_dir"]), slug(a["company"] or "")], interactive=True)})
    return out
