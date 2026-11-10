"""The discovery queue: listings found on careers sites, in `jobs` (state/jobs.sqlite).

Status flow: `queued` / `needs_review` / `skipped` (with the reason in `notes`) when a listing is found; the runner
moves a queued job to `running` and then to `scraped` / `failed` / `closed` / `blocked` / `applied`. A job already past
`queued` is never overwritten by a later discovery, and a listing is the same job by canonical URL or by
(company, requisition id). A runner killed mid-job leaves `running` rows; `requeue_stale` puts them back.
"""

import re
import sqlite3
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from jobagent.tools.locations import Decision
from jobagent.tools.matching import Match

TRACKING = re.compile(r"(?i)^(utm_.*|gclid|fbclid|mc_[a-z]+|ref|referrer|source|src|trk|trackingid|gh_src|lever-source.*)$")
OPEN = ("queued", "needs_review", "skipped")  # statuses a new discovery may still change


@dataclass
class Listing:
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
    return re.sub(r"[^a-z0-9]+", "-", company.lower()).strip("-") or "unknown"


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
    sql, args = "SELECT * FROM jobs WHERE status = ?", [status]
    if company:
        sql += " AND company_slug = ?"
        args.append(slug(company))
    sql += " ORDER BY COALESCE(match_score, 0) DESC, id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql, args).fetchall()


def mark(conn: sqlite3.Connection, url: str, status: str, note: str = "") -> None:
    with conn:
        conn.execute("UPDATE jobs SET status = ?, notes = COALESCE(NULLIF(?, ''), notes), "
                     "updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE url = ?", (status, note, url))


def requeue_stale(conn: sqlite3.Connection) -> int:
    """A runner that was killed leaves its job `running`: put those back in the queue."""
    with conn:
        cur = conn.execute("UPDATE jobs SET status = 'queued', notes = 'requeued after an interrupted run', "
                           "updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE status = 'running'")
    return cur.rowcount


def summary(conn: sqlite3.Connection, company: str | None = None) -> dict[str, int]:
    sql, args = "SELECT status, COUNT(*) AS n FROM jobs WHERE source IS NOT NULL", []
    if company:
        sql += " AND company_slug = ?"
        args.append(slug(company))
    return {r["status"]: r["n"] for r in conn.execute(sql + " GROUP BY status", args)}
