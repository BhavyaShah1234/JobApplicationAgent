"""Per-company pacing for the queue (step 29): do not send one employer a stream of applications.

A recruiter who sees six near-identical applications from one candidate in a week reads them as spam; the useful number is a handful
per company per window. `excluded` names the companies that already have `max_per_company` applications started or sent within
`days`; the queue skips their jobs until the window moves on. An explicit `jobagent run URL` is never blocked (the candidate asked
for that posting), only warned about. `max_per_company = 0` turns pacing off."""

from datetime import UTC, datetime, timedelta

from jobagent.tools.jobqueue import employer_key

# applications that count: the form was filled for review, approved, or sent (a skipped / failed / closed one does not)
COUNTED = ("filled", "approved", "submission_in_progress", "submission_unknown", "submitted", "confirmed")


def _floor(days: int, now: datetime | None) -> str:
    return ((now or datetime.now(UTC)) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def recent_counts(conn, days: int, now: datetime | None = None) -> dict[str, int]:
    """{employer key: applications started or sent in the last `days` days}. The employer is the one the QUEUE knows (the job's
    company_slug) when the application is linked to a job, so a name the scraper or the submit step wrote differently still counts
    against the same bucket."""
    floor = _floor(days, now)
    marks = ",".join("?" * len(COUNTED))
    out: dict[str, int] = {}
    for r in conn.execute(f"SELECT a.company AS company, j.company_slug AS queued_as, COALESCE(a.submitted_at, a.decided_at, a.filled_at) AS at "
                          f"FROM applications a LEFT JOIN jobs j ON j.id = a.job_id WHERE a.state IN ({marks})", COUNTED):
        name = r["queued_as"] or r["company"]
        if r["at"] and r["at"] >= floor and name:
            out[employer_key(name)] = out.get(employer_key(name), 0) + 1
    return out


def active_counts(conn, days: int = 7, now: datetime | None = None) -> dict[str, int]:
    """{employer key: jobs a runner is working on right now}. A job holds its slot until an application for it is INCLUDED in
    `recent_counts` (same states, same window); an older or uncounted application does not release it."""
    floor = _floor(days, now)
    marks = ",".join("?" * len(COUNTED))
    out: dict[str, int] = {}
    for r in conn.execute(f"SELECT j.company_slug AS s FROM jobs j WHERE j.status = 'running' AND j.claimed_by IS NOT NULL AND NOT EXISTS "
                          f"(SELECT 1 FROM applications a WHERE a.job_id = j.id AND a.state IN ({marks}) "
                          f"AND COALESCE(a.submitted_at, a.decided_at, a.filled_at) >= ?)", (*COUNTED, floor)):
        if r["s"]:
            out[employer_key(r["s"])] = out.get(employer_key(r["s"]), 0) + 1
    return out


def excluded(conn, max_per_company: int, days: int, now: datetime | None = None, active: bool = False) -> dict[str, int]:
    """{company slug: count} of the companies at or over the limit (empty when pacing is off). `active` also counts the jobs being
    worked right now (the claim uses it, inside its transaction)."""
    if max_per_company <= 0:
        return {}
    counts = dict(recent_counts(conn, days, now))
    if active:
        for s, n in active_counts(conn, days, now).items():
            counts[s] = counts.get(s, 0) + n
    return {s: n for s, n in counts.items() if n >= max_per_company}
