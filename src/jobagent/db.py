"""SQLite store at state/jobs.sqlite. Tables are added as workflow steps need them."""

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS verification_log (
    id          INTEGER PRIMARY KEY,
    message_id  TEXT NOT NULL,
    sender      TEXT,
    domain      TEXT,
    purpose     TEXT NOT NULL,      -- otp | link | password_reset | self_test | stale_sweep
    used_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE TABLE IF NOT EXISTS jobs (
    id           INTEGER PRIMARY KEY,
    url          TEXT NOT NULL UNIQUE,
    job_id       TEXT,
    company      TEXT,
    title        TEXT,
    location     TEXT,
    portal       TEXT,
    jd_json      TEXT,
    jd_text      TEXT,                              -- the posting text as written (jd.txt)
    status       TEXT NOT NULL DEFAULT 'queued',   -- queued | scraped | applied | failed | skipped | needs_review
    discovered_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
-- Step 10: every ATS score of a resume version (an online checker, or one the user ran by hand), per round.
CREATE TABLE IF NOT EXISTS ats_runs (
    id          INTEGER PRIMARY KEY,
    job_dir     TEXT NOT NULL,      -- outputs/<company>/<job-id>
    site        TEXT NOT NULL,      -- local | jobalytics | autoapplymax | jobscan | skillsyncer | resymatch | ...
    round       INTEGER NOT NULL,
    score       REAL,               -- 0-100 as the site reports it (sites are not comparable to each other)
    missing     TEXT,               -- JSON list of missing keywords, most important first
    raw         TEXT,               -- JSON: the site's breakdown / notes
    resume_sha  TEXT,               -- sha256 of the scored resume.pdf
    scored_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT
);
-- Sites the agent itself signed up on / logged into / applied at. The inbox is shared with the
-- user's own sign-ups, so only mail from these domains is ever read for codes or trashed.
CREATE TABLE IF NOT EXISTS agent_domains (
    domain      TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,      -- portal | ats | overleaf | linkedin | other
    first_seen  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    last_seen   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
"""

MIGRATIONS = [("jobs", "jd_text", "TEXT")]

# Registrable domains under multi-label public suffixes we are likely to meet.
_TWO_LABEL_SUFFIXES = {"co.uk", "co.in", "com.au", "co.jp", "com.br", "co.nz", "com.sg", "ac.uk", "org.uk"}

# ATS vendors send mail from different domains than the ones their job pages live on.
ATS_SENDER_FAMILIES = [
    {"myworkdayjobs.com", "myworkday.com", "workday.com"},
    {"greenhouse.io", "greenhouse-mail.io"},
    {"lever.co", "hire.lever.co"},
    {"ashbyhq.com", "ashby.io"},
    {"icims.com"},
    {"smartrecruiters.com"},
    {"successfactors.com", "sapsf.com"},
    {"taleo.net", "oracle.com"},
    {"jobvite.com"},
]


def registrable_domain(host_or_url: str) -> str:
    """'https://acme.wd5.myworkdayjobs.com/x' → 'myworkdayjobs.com'; 'no-reply@mail.foo.co.uk' → 'foo.co.uk'."""
    from urllib.parse import urlparse

    host = host_or_url.strip().lower()
    if "://" in host:
        host = urlparse(host).netloc
    host = host.rsplit("@", 1)[-1].strip("<>. ").split(":")[0]
    labels = [l for l in host.split(".") if l]
    if len(labels) >= 3 and ".".join(labels[-2:]) in _TWO_LABEL_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def connect(path: Path | None = None) -> sqlite3.Connection:
    if path is None:
        from jobagent.config import get_settings

        path = get_settings().state_dir / "jobs.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)  # parallel workers wait for each other's writes
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    for table, column, decl in MIGRATIONS:  # columns added after a table already existed
        if column not in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    return conn


def record_agent_domain(conn: sqlite3.Connection, host_or_url: str, kind: str = "portal") -> set[str]:
    """Remember a site the agent used (plus its ATS sender family). Returns the domains recorded."""
    domain = registrable_domain(host_or_url)
    family = next((f for f in ATS_SENDER_FAMILIES if domain in f), {domain})
    kind = "ats" if len(family) > 1 or domain in {d for f in ATS_SENDER_FAMILIES for d in f} else kind
    with conn:
        for d in family:
            conn.execute(
                "INSERT INTO agent_domains (domain, kind) VALUES (?, ?) "
                "ON CONFLICT(domain) DO UPDATE SET last_seen = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')",
                (d, kind),
            )
    return set(family)


def agent_domains(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT domain FROM agent_domains")}


def log_verification(conn: sqlite3.Connection, message_id: str, sender: str, purpose: str) -> None:
    domain = sender.rsplit("@", 1)[-1].strip(">").lower() if "@" in sender else ""
    with conn:
        conn.execute(
            "INSERT INTO verification_log (message_id, sender, domain, purpose) VALUES (?, ?, ?, ?)",
            (message_id, sender, domain, purpose),
        )
