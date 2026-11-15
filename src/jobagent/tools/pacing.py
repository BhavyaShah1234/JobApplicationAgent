"""Per-company pacing for the queue (step 29): do not send one employer a stream of applications.

A recruiter who sees six near-identical applications from one candidate in a week reads them as spam; the useful number is a handful
per company per window. `excluded` names the companies that already have `max_per_company` applications started or sent within
`days`; the queue skips their jobs until the window moves on. An explicit `jobagent run URL` is never blocked (the candidate asked
for that posting), only warned about. `max_per_company = 0` turns pacing off."""

from datetime import UTC, datetime, timedelta

from jobagent.tools.jobqueue import slug

# applications that count: the form was filled for review, approved, or sent (a skipped / failed / closed one does not)
COUNTED = ("filled", "approved", "submission_in_progress", "submission_unknown", "submitted", "confirmed")


def recent_counts(conn, days: int, now: datetime | None = None) -> dict[str, int]:
    """{company slug: applications started or sent in the last `days` days}."""
    now = now or datetime.now(UTC)
    floor = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    marks = ",".join("?" * len(COUNTED))
    out: dict[str, int] = {}
    for r in conn.execute(f"SELECT company, COALESCE(submitted_at, decided_at, filled_at) AS at FROM applications "
                          f"WHERE state IN ({marks})", COUNTED):
        if r["at"] and r["at"] >= floor and r["company"]:
            out[slug(r["company"])] = out.get(slug(r["company"]), 0) + 1
    return out


def active_counts(conn) -> dict[str, int]:
    """{company slug: jobs a runner is working on right now that have no counted application yet}: each holds a slot until its
    application is recorded (then `recent_counts` takes over, so nothing is counted twice)."""
    marks = ",".join("?" * len(COUNTED))
    out: dict[str, int] = {}
    for r in conn.execute(f"SELECT j.company_slug AS s FROM jobs j WHERE j.status = 'running' AND j.claimed_by IS NOT NULL AND NOT EXISTS "
                          f"(SELECT 1 FROM applications a WHERE a.job_id = j.id AND a.state IN ({marks}))", COUNTED):
        if r["s"]:
            out[r["s"]] = out.get(r["s"], 0) + 1
    return out


def excluded(conn, max_per_company: int, days: int, now: datetime | None = None, active: bool = False) -> dict[str, int]:
    """{company slug: count} of the companies at or over the limit (empty when pacing is off). `active` also counts the jobs being
    worked right now (the claim uses it, inside its transaction)."""
    if max_per_company <= 0:
        return {}
    counts = dict(recent_counts(conn, days, now))
    if active:
        for s, n in active_counts(conn).items():
            counts[s] = counts.get(s, 0) + n
    return {s: n for s, n in counts.items() if n >= max_per_company}
