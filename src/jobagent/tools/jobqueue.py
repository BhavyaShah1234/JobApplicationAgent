"""The discovery queue: listings found on careers sites, in `jobs` (state/jobs.sqlite).

Status flow: `queued` / `needs_review` / `skipped` (with the reason in `notes`) when a listing is found; the runner
moves a queued job to `running` and then to `scraped` / `failed` / `closed` / `blocked` / `applied`. A job already past
`queued` is never overwritten by a later discovery, and a listing is the same job by canonical URL or by
(company, requisition id).

**Claims and leases (F13).** A runner takes a job with `claim`, one atomic statement that also records WHO took it
(`claimed_by`, "host:pid:random") and until when (`lease_until`). Two runners can never take the same job. The owner keeps
the lease alive with `renew` while it works, and only the owner can record the job's outcome (`finish`). `requeue_stale`
puts a `running` job back only when its lease has EXPIRED or its owner is a process on this machine that no longer exists;
it never touches work a live runner is doing.
"""

import os
import re
import socket
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from jobagent.tools.locations import Decision
from jobagent.tools.matching import Match

TRACKING = re.compile(r"(?i)^(utm_.*|gclid|fbclid|mc_[a-z]+|ref|referrer|source|src|trk|trackingid|gh_src|lever-source.*)$")
OPEN = ("queued", "needs_review", "skipped")  # statuses a new discovery may still change
def _lease_default() -> int:
    """queue_lease_seconds from config/queue.yaml (default 900); a broken config must not stop the queue module importing."""
    try:
        from jobagent.config import get_settings
        return get_settings().queue_lease_seconds
    except Exception:
        return 900


LEASE_S = _lease_default()                     # a claim lasts this long unless its owner renews it


@dataclass
class Listing:
    """One posting seen on a careers site: what discovery records before deciding whether to queue it."""
    company: str
    title: str
    url: str
    locations: list[str] = field(default_factory=list)
    posted: str = ""
    req_id: str = ""
    snippet: str = ""
    portal: str = ""


def canonical_url(url: str) -> str:
    """The same posting however it was linked: lowercase host, no fragment, no tracking parameters, no trailing slash.
    A parameter that identifies the job (gh_jid, jid, id, req) stays."""
    p = urlsplit(url.strip())
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not TRACKING.match(k)]
    path = re.sub(r"/+$", "", p.path) or "/"
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), path, urlencode(sorted(q)), ""))


def slug(company: str) -> str:
    """A lower-case, hyphenated key for a company name (`Acme Corp` -> `acme-corp`); `unknown` for an empty one."""
    return re.sub(r"[^a-z0-9]+", "-", company.lower()).strip("-") or "unknown"


CORP_WORDS = {"inc", "incorporated", "corp", "corporation", "co", "company", "llc", "ltd", "limited", "plc", "gmbh", "lp", "llp"}


def employer_key(name: str) -> str:
    """One key per employer for pacing and batch scope: the slug without corporate suffixes ("Acme", "Acme Inc." and "Acme Corporation"
    are one employer), so a display-name change between discovery and submission never opens a second capacity bucket."""
    parts = [x for x in slug(re.sub(r"(?<=\w)\.(?=\w)|\.(?=\s|$)", "", name or "")).split("-") if x]      # "L.L.C." -> "LLC", "Inc." -> "Inc"
    while len(parts) > 1 and parts[-1] in CORP_WORDS:
        parts.pop()
    return "-".join(parts) or "unknown"


def combine(location: Decision | None, match: Match) -> tuple[str, str]:
    """(status, reason) from the location verdict and the role match."""
    if location is not None and location.verdict == "non_us":
        return "skipped", f"location: {location.reason}"
    if match.verdict == "skipped":
        return "skipped", f"role: {match.reason}"
    if (location is not None and location.verdict == "ambiguous") or match.verdict == "needs_review":
        why = []
        if location is not None and location.verdict == "ambiguous":
            why.append(f"location: {location.reason}")
        if match.verdict == "needs_review":
            why.append(f"role: {match.reason} ({match.score})")
        return "needs_review", "; ".join(why)
    return "queued", f"{match.stage} match to {match.role_id} ({match.score})"


def enqueue(conn: sqlite3.Connection, item: Listing, match: Match, location: Decision | None = None,
            source: str = "discovery") -> str:
    """Write the listing and return what happened: queued | needs_review | skipped | duplicate | unchanged."""
    url, company_slug = canonical_url(item.url), slug(item.company)
    status, why = combine(location, match)
    if item.req_id:
        # a skipped twin (say, the same requisition in a non-US office) must not hide this posting
        twin = conn.execute("SELECT url FROM jobs WHERE company_slug = ? AND req_id = ? AND url != ? "
                            "AND status != 'skipped'", (company_slug, item.req_id, url)).fetchone()
        if twin:
            return "duplicate"
    if conn.execute("SELECT 1 FROM queue_removed WHERE url = ?", (url,)).fetchone():
        return "unchanged"                                  # the candidate deleted this posting in the dashboard
    row = conn.execute("SELECT status FROM jobs WHERE url = ?", (url,)).fetchone()
    if row and row["status"] not in OPEN:
        return "unchanged"
    with conn:
        conn.execute(
            "INSERT INTO jobs (url, company, title, location, portal, status, notes, company_slug, req_id, posted_at, "
            "role_id, match_stage, match_score, source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(url) DO UPDATE SET company=excluded.company, title=excluded.title, "
            "location=excluded.location, portal=COALESCE(NULLIF(excluded.portal,''), portal), status=excluded.status, "
            "notes=excluded.notes, req_id=excluded.req_id, posted_at=excluded.posted_at, role_id=excluded.role_id, "
            "match_stage=excluded.match_stage, match_score=excluded.match_score, "
            "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')",
            (url, item.company, item.title, "; ".join(item.locations), item.portal, status, why, company_slug,
             item.req_id or None, item.posted or None, match.role_id or None, match.stage, match.score, source))
    return status


def pending(conn: sqlite3.Connection, status: str = "queued", company: str | None = None,
            limit: int | None = None) -> list[sqlite3.Row]:
    """Queued jobs, best match first.

    Args:
        conn: Open job database.
        status: Which status to list (default `queued`).
        company: Only this company, when given.
        limit: At most this many rows.

    Returns:
        Matching `jobs` rows in the order the queue takes them: match score, then id; jobs deferred while the candidate was
        away come last, in the order they were deferred.
    """
    sql, args = "SELECT * FROM jobs WHERE status = ?", [status]
    if company:
        sql += " AND company_slug = ?"
        args.append(slug(company))
    sql += " ORDER BY " + QUEUE_ORDER
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql, args).fetchall()


# deferred jobs (waiting for the candidate) after every other queued job, oldest deferral first
QUEUE_ORDER = "(deferred_at IS NOT NULL), deferred_at, COALESCE(match_score, 0) DESC, id"


def _iso(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def worker_id() -> str:
    """Who this runner is: unique per process, readable in the table, and carrying the pid so a dead local owner is detectable."""
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


def claim(conn: sqlite3.Connection, worker: str, company: str | None = None, lease_s: int = LEASE_S,
          now: datetime | None = None, exclude: tuple[str, ...] | list[str] = (), companies: tuple[str, ...] | None = None,
          pace: tuple[int, int] | None = None, include_deferred: bool = True) -> sqlite3.Row | None:
    """Atomically take the best queued job for `worker`: choose it and mark it running in ONE write transaction, so two
    runners cannot take the same job. None when nothing is queued. `companies` (slugs) limits the choice to those companies;
    `pace` = (max applications per company, window days) is applied INSIDE the transaction, counting the applications already
    started and the jobs other runners are working right now, so two runners cannot both take the last slot of one company.
    Jobs deferred while the candidate was away are taken last, and not at all when `include_deferred` is False (still away)."""
    now = now or datetime.now(UTC)
    if conn.in_transaction:
        conn.commit()                                      # BEGIN IMMEDIATE cannot nest inside the caller's open transaction
    conn.create_function("employer_key", 1, lambda s: employer_key(s or ""), deterministic=True)
    sql = "SELECT id FROM jobs WHERE status = 'queued'"
    if not include_deferred:
        sql += " AND deferred_at IS NULL"
    args: list = []
    if company:
        sql += " AND employer_key(COALESCE(NULLIF(company_slug, ''), company, '')) = ?"
        args.append(employer_key(company))
    if exclude:                                        # companies at their pacing limit (tools/pacing.py)
        sql += f" AND employer_key(COALESCE(NULLIF(company_slug, ''), company, '')) NOT IN ({','.join('?' * len(exclude))})"
        args += [employer_key(x) for x in exclude]
    if companies is not None:
        sql += f" AND employer_key(COALESCE(NULLIF(company_slug, ''), company, '')) IN ({','.join('?' * len(companies)) or 'NULL'})"
        args += [employer_key(x) for x in companies]
    conn.execute("BEGIN IMMEDIATE")                       # takes the write lock before reading: the choice cannot go stale
    try:
        if pace:
            from jobagent.tools import pacing
            full = tuple(pacing.excluded(conn, pace[0], pace[1], now, active=True))
            if full:
                sql += f" AND employer_key(COALESCE(NULLIF(company_slug, ''), company, '')) NOT IN ({','.join('?' * len(full))})"
                args += list(full)
        sql += " ORDER BY " + QUEUE_ORDER + " LIMIT 1"
        row = conn.execute(sql, args).fetchone()
        if row is None:
            conn.execute("COMMIT")
            return None
        conn.execute("UPDATE jobs SET status = 'running', claimed_by = ?, lease_until = ?, updated_at = ? WHERE id = ? AND status = 'queued'",
                     (worker, _iso(now + timedelta(seconds=lease_s)), _iso(now), row["id"]))
        got = conn.execute("SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone()
        conn.execute("COMMIT")
        return got
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def renew(conn: sqlite3.Connection, url: str, worker: str, lease_s: int = LEASE_S, now: datetime | None = None) -> bool:
    """Extend the lease. False when the job is no longer this worker's (its lease expired and it was given to another)."""
    now = now or datetime.now(UTC)
    with conn:
        cur = conn.execute("UPDATE jobs SET lease_until = ?, updated_at = ? WHERE url = ? AND status = 'running' AND claimed_by = ?",
                           (_iso(now + timedelta(seconds=lease_s)), _iso(now), url, worker))
    return cur.rowcount == 1


def finish(conn: sqlite3.Connection, url: str, worker: str, status: str, note: str = "") -> bool:
    """Record the outcome of a claimed job, only if `worker` still holds the claim; the claim is then released. False when
    the job was taken from this worker (the outcome of a stale runner never overwrites the new owner's)."""
    with conn:
        cur = conn.execute("UPDATE jobs SET status = ?, notes = COALESCE(NULLIF(?, ''), notes), claimed_by = NULL, lease_until = NULL, "
                           "deferred_at = NULL, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE url = ? AND claimed_by = ?",
                           (status, note, url, worker))
    return cur.rowcount == 1


def defer(conn: sqlite3.Connection, url: str, worker: str, note: str = "", now: datetime | None = None) -> bool:
    """Put a claimed job back in the queue at the END because it needs the candidate, who is away; only if `worker` still holds
    the claim. It is resumed (from its saved checkpoint) when the candidate is available again."""
    now = now or datetime.now(UTC)
    with conn:
        cur = conn.execute("UPDATE jobs SET status = 'queued', deferred_at = ?, notes = COALESCE(NULLIF(?, ''), notes), "
                           "claimed_by = NULL, lease_until = NULL, updated_at = ? WHERE url = ? AND claimed_by = ?",
                           (_iso(now), note, _iso(now), url, worker))
    return cur.rowcount == 1


def deferred_count(conn: sqlite3.Connection) -> int:
    """How many queued jobs are waiting for the candidate."""
    return conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'queued' AND deferred_at IS NOT NULL").fetchone()[0]


def mark(conn: sqlite3.Connection, url: str, status: str, note: str = "") -> None:
    """Set a job's status (discovery, the `queue` commands, a step recording what it found). Leaving `running` also
    releases the claim."""
    with conn:
        conn.execute("UPDATE jobs SET status = ?, notes = COALESCE(NULLIF(?, ''), notes), "
                     "claimed_by = CASE WHEN ? = 'running' THEN claimed_by ELSE NULL END, "
                     "lease_until = CASE WHEN ? = 'running' THEN lease_until ELSE NULL END, "
                     "updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE url = ?", (status, note, status, status, url))


def _owner_is_dead(owner: str | None) -> bool:
    """True only when the owner is a process on THIS machine that no longer exists. Unknown or remote owners are never
    declared dead from here: their lease has to run out."""
    try:
        host, pid, _ = (owner or "").split(":")
        if host != socket.gethostname():
            return False
        os.kill(int(pid), 0)
        return False
    except ProcessLookupError:
        return True
    except (ValueError, PermissionError, OSError):
        return False


def requeue_stale(conn: sqlite3.Connection, now: datetime | None = None, lease_s: int = LEASE_S) -> int:
    """Put back the `running` jobs whose runner is gone: the lease expired, or the owner is a dead local process. A row from
    before leases existed (no owner, no lease) is recovered once it has not been touched for a full lease. Live work is
    never reset."""
    now = now or datetime.now(UTC)
    n = 0
    with conn:
        for r in conn.execute("SELECT id, claimed_by, lease_until, updated_at FROM jobs WHERE status = 'running'").fetchall():
            if r["lease_until"]:
                gone = r["lease_until"] < _iso(now) or _owner_is_dead(r["claimed_by"])
            else:
                gone = _owner_is_dead(r["claimed_by"]) or (r["updated_at"] or "") < _iso(now - timedelta(seconds=lease_s))
            if gone:
                conn.execute("UPDATE jobs SET status = 'queued', claimed_by = NULL, lease_until = NULL, "
                             "notes = 'requeued after an interrupted run', updated_at = ? WHERE id = ? AND status = 'running'",
                             (_iso(now), r["id"]))
                n += 1
        # a step moved the job past `running` (scraped...) and its runner then died: the work stays where it got to, the claim is dropped
        conn.execute("UPDATE jobs SET claimed_by = NULL, lease_until = NULL WHERE status != 'running' AND claimed_by IS NOT NULL "
                     "AND lease_until IS NOT NULL AND lease_until < ?", (_iso(now),))
    return n


def summary(conn: sqlite3.Connection, company: str | None = None) -> dict[str, int]:
    """How many discovered jobs there are in each status.

    Args:
        conn: Open job database.
        company: Only this company, when given.

    Returns:
        `{status: count}`.
    """
    sql, args = "SELECT status, COUNT(*) AS n FROM jobs WHERE source IS NOT NULL", []
    if company:
        sql += " AND company_slug = ?"
        args.append(slug(company))
    return {r["status"]: r["n"] for r in conn.execute(sql + " GROUP BY status", args)}
