"""SQLite store at state/jobs.sqlite. Tables are added as workflow steps need them."""

import json
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
                                                    -- | closed | already_applied | blocked (tools/availability.py)
                                                    -- | running (the queue runner is on it) (tools/jobqueue.py)
    notes        TEXT,                              -- why: e.g. 'closed: no longer accepting applications'
    discovered_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
-- Dashboard: postings the candidate deleted from the queue. Discovery skips these addresses, so a deleted posting does not come back on the next run.
CREATE TABLE IF NOT EXISTS queue_removed (
    url         TEXT PRIMARY KEY,
    company     TEXT,
    title       TEXT,
    removed_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
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
-- Step 13: each application's way from a filled form to a confirmed submission.
CREATE TABLE IF NOT EXISTS applications (
    id              INTEGER PRIMARY KEY,
    job_id          INTEGER REFERENCES jobs(id) ON DELETE RESTRICT,
    job_dir         TEXT NOT NULL UNIQUE,   -- outputs/<company>/<job-id>
    company         TEXT,
    title           TEXT,
    url             TEXT,
    portal          TEXT,
    state           TEXT NOT NULL,          -- filled | approved | submission_in_progress | submission_unknown | submitted
                                            -- | confirmed | skipped | failed; the four "sent" states are never rewritten backwards
    filled_at       TEXT,
    decided_at      TEXT,                   -- the user's approve / skip
    submitted_at    TEXT,
    confirmation_id TEXT,                   -- Message-ID of the confirmation mail
    confirmation    TEXT,                   -- its sender and subject
    confirmed_at    TEXT,
    notes           TEXT
);
-- F04: one row per attempt to send an application. The row is written (and committed) BEFORE the Submit click, so a crash
-- at any point leaves a trace; an attempt that ends without proof of receipt stays `unknown` until portal or mail evidence,
-- or the candidate, settles it. Only after that can a new attempt start.
CREATE TABLE IF NOT EXISTS submission_attempts (
    id           INTEGER PRIMARY KEY,
    application_id INTEGER REFERENCES applications(id) ON DELETE RESTRICT,
    job_dir      TEXT NOT NULL,
    attempt_no   INTEGER NOT NULL,
    state        TEXT NOT NULL,   -- in_progress | unknown | submitted | confirmed | refused | not_sent
    payload_sha  TEXT,            -- hash of the reviewed field values and document hashes, frozen before the click
    payload_path TEXT,            -- the frozen payload (application/submission-<n>.json)
    approved_at  TEXT,
    started_at   TEXT NOT NULL,   -- the attempt was reserved
    action_at    TEXT,            -- committed just before the click was started: it was attempted at or after this
    clicked_at   TEXT,            -- the click call returned: captured at the action, never at settlement
    finished_at  TEXT,
    evidence     TEXT,            -- what proved the outcome (thank-you page text, confirmation mail, the candidate's word)
    notes        TEXT,
    UNIQUE (job_dir, attempt_no)
);
-- at most one attempt per application can be open (in progress or of unknown outcome) at a time
CREATE UNIQUE INDEX IF NOT EXISTS one_open_attempt ON submission_attempts (job_dir) WHERE state IN ('in_progress', 'unknown');
-- Sites the agent itself signed up on / logged into / applied at. The inbox is shared with the
-- user's own sign-ups, so only mail from these domains is ever read for codes or trashed.
CREATE TABLE IF NOT EXISTS agent_domains (
    domain      TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,      -- portal | ats | overleaf | linkedin | other
    first_seen  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    last_seen   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
-- F08: the inbox is shared with the user's own sign-ups, and a domain the agent once used does not make every mail from it the
-- agent's. An action is one thing the agent did that expects mail (a sign-up, a login, a device check): its site, the exact
-- address it used (a plus-addressed `name+tag@...` gives a routing key the user's own mail never carries), its time window,
-- and, once a wait matches a mail, that mail's Message-ID. Cleanup is allowed only for mail an action owns.
CREATE TABLE IF NOT EXISTS agent_actions (
    id                  INTEGER PRIMARY KEY,
    domain              TEXT NOT NULL,
    kind                TEXT NOT NULL,
    address             TEXT,
    started_at          TEXT NOT NULL,
    expires_at          TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'open',      -- open | consumed
    candidate_message_id TEXT,                              -- selected by a wait: NOT ownership
    consumed_message_id TEXT,                              -- the caller saw the site accept it: ownership
    consumed_at         TEXT,
    evidence            TEXT
);
-- Step 15: what came back for each submitted application. One row per message (Message-ID unique), one application
-- can have several over weeks. The mail's own Date is the event time; only a short excerpt is kept, never a body.
CREATE TABLE IF NOT EXISTS outcome_events (
    id             INTEGER PRIMARY KEY,
    application_id INTEGER NOT NULL REFERENCES applications(id),
    stage          TEXT NOT NULL,     -- confirmation | assessment | recruiter_screen | interview | offer | rejection | withdrawn
    message_id     TEXT NOT NULL UNIQUE,
    thread_id      TEXT,
    sender         TEXT,
    subject        TEXT,
    excerpt        TEXT,              -- at most 200 characters
    received_at    TEXT NOT NULL,     -- UTC, the mail's Date
    classifier     TEXT NOT NULL,     -- rule | model | human
    confidence     REAL,
    needs_review   INTEGER NOT NULL DEFAULT 0,   -- 1: not counted until the candidate approves it
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX IF NOT EXISTS outcome_events_app ON outcome_events(application_id, received_at);
-- What the history says (jobagent reflect): advisory text with the applications it rests on. A lesson the data stops
-- supporting is retired, not deleted.
CREATE TABLE IF NOT EXISTS lessons (
    id          INTEGER PRIMARY KEY,
    key         TEXT NOT NULL,         -- what it is about: 'site:hireflow', 'keyword:langgraph@swe'
    scope       TEXT NOT NULL,         -- global | role family id
    text        TEXT NOT NULL,
    evidence    TEXT NOT NULL,         -- JSON: application ids, counts, the statistic
    snapshot    TEXT NOT NULL,         -- hash of the labelled data it was computed from
    status      TEXT NOT NULL DEFAULT 'active',   -- active | retired
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    retired_at  TEXT
);
-- F10/F11: every mailbox message the outcome sync has looked at, by DURABLE identity (the folder's UIDVALIDITY and the message's
-- UID, never the Message-ID header, which can be missing or repeated). `state`: unrelated (read, not about an application:
-- terminal), stored (an outcome event was written: terminal), retry (the classifier failed or was unavailable: tried again on
-- every sync, with `attempts` and `last_error`), undecided (read many times and still could not be classified: waits for the
-- candidate). The sync cursor advances only after the messages it covers are recorded here. Only metadata is kept.
CREATE TABLE IF NOT EXISTS mail_ledger (
    id            INTEGER PRIMARY KEY,
    uidvalidity   TEXT NOT NULL DEFAULT '',
    mailbox_uid   TEXT NOT NULL,
    header_message_id TEXT,
    thread_id     TEXT,
    sender_domain TEXT,
    sender_addr   TEXT,              -- the full sender address (lower-case): part of the evidence that a relocated message is the same one
    subject_sig   TEXT,              -- SHA-256 of the subject (the subject itself is not kept)
    content_sig   TEXT,              -- fingerprint of what the message said, captured when its body was first read
    header_date   TEXT,              -- the message's own (claimed) Date, normalized to UTC; NULL if missing or unparsable
    delivered_at  TEXT,              -- when the mailbox received it (INTERNALDATE): the delivery time, not a claim
    state         TEXT NOT NULL,     -- unrelated | stored | retry | undecided
    reason        TEXT,
    candidates    TEXT,              -- JSON [{application_id, score, evidence}] kept for a retry
    inherited     INTEGER NOT NULL DEFAULT 0,
    attempts      INTEGER NOT NULL DEFAULT 0,
    last_error    TEXT,
    first_seen    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (uidvalidity, mailbox_uid)
);
-- F10: every application an outcome event could belong to, with the evidence, so an ambiguity can be reviewed and reassigned.
CREATE TABLE IF NOT EXISTS outcome_candidates (
    event_id       INTEGER NOT NULL REFERENCES outcome_events(id),
    application_id INTEGER NOT NULL REFERENCES applications(id),
    score          INTEGER NOT NULL DEFAULT 0,
    evidence       TEXT,
    PRIMARY KEY (event_id, application_id)
);
-- F23: a consumed look of the fixed-look learning protocol, frozen: the cohort and outcomes it was decided on, its parameters and its
-- decision. Later calls for that look replay this record; changed data never re-spends the look.
CREATE TABLE IF NOT EXISTS learning_looks (
    look      INTEGER PRIMARY KEY,
    n         INTEGER NOT NULL,       -- the look's declared size (resolved applications)
    taken_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    cohort    TEXT NOT NULL,          -- JSON [[application id, positive 0/1, weight], ...]
    params    TEXT NOT NULL,          -- JSON
    result    TEXT NOT NULL           -- JSON: the sites and skills decided
);
-- F25: two addresses of one posting (the apply link vs the posting's own address): both find the same job.
CREATE TABLE IF NOT EXISTS url_aliases (
    alias     TEXT NOT NULL,
    canonical TEXT NOT NULL,
    PRIMARY KEY (alias, canonical)
);
-- F10: who decided which application an event belongs to, and when.
CREATE TABLE IF NOT EXISTS outcome_audit (
    id         INTEGER PRIMARY KEY,
    event_id   INTEGER NOT NULL,
    at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    action     TEXT NOT NULL,        -- assign | approve | reject | label | propagate
    old_application_id INTEGER,
    new_application_id INTEGER,
    detail     TEXT
);
CREATE VIEW IF NOT EXISTS application_status AS
SELECT a.id AS application_id, a.company, a.title, a.state, a.submitted_at, COUNT(e.id) AS events,
       MAX(e.received_at) AS last_event_at,
       (SELECT x.stage FROM outcome_events x WHERE x.application_id = a.id AND x.needs_review = 0
         ORDER BY x.received_at DESC, x.id DESC LIMIT 1) AS current_stage,
       (SELECT x.stage FROM outcome_events x WHERE x.application_id = a.id AND x.needs_review = 0
         ORDER BY CASE x.stage WHEN 'offer' THEN 6 WHEN 'interview' THEN 5 WHEN 'recruiter_screen' THEN 4
                  WHEN 'assessment' THEN 3 WHEN 'confirmation' THEN 2 WHEN 'withdrawn' THEN 1 ELSE 0 END DESC,
                  x.received_at LIMIT 1) AS furthest_stage
FROM applications a LEFT JOIN outcome_events e ON e.application_id = a.id AND e.needs_review = 0
GROUP BY a.id;
"""

MIGRATIONS = [("applications", "job_id", "INTEGER REFERENCES jobs(id) ON DELETE RESTRICT"),
              ("submission_attempts", "application_id", "INTEGER REFERENCES applications(id) ON DELETE RESTRICT"),
              ("mail_ledger", "content_sig", "TEXT"),
              ("mail_ledger", "sender_addr", "TEXT"), ("mail_ledger", "subject_sig", "TEXT"),
              ("outcome_events", "header_message_id", "TEXT"), ("outcome_events", "mailbox_uid", "TEXT"),
              ("outcome_events", "uidvalidity", "TEXT"), ("outcome_events", "delivered_at", "TEXT"),
              ("outcome_events", "ambiguous", "INTEGER NOT NULL DEFAULT 0"), ("outcome_events", "content_sig", "TEXT"),
              ("outcome_events", "inherited_from", "INTEGER"), ("outcome_events", "manual", "INTEGER NOT NULL DEFAULT 0"),    # F10: more than one application fits (or inherited from such)
              ("agent_actions", "candidate_message_id", "TEXT"), ("agent_actions", "evidence", "TEXT"),
              ("jobs", "jd_text", "TEXT"), ("jobs", "notes", "TEXT"),
              # step 14 (discovery): what the listing was and why it was queued, skipped or sent to review
              ("jobs", "company_slug", "TEXT"), ("jobs", "req_id", "TEXT"), ("jobs", "posted_at", "TEXT"),
              ("jobs", "role_id", "TEXT"), ("jobs", "match_stage", "TEXT"), ("jobs", "match_score", "REAL"),
              ("jobs", "source", "TEXT"),
              ("jobs", "claimed_by", "TEXT"), ("jobs", "lease_until", "TEXT"),      # F13: who is working on a running job, until when
              ("jobs", "deferred_at", "TEXT"),      # set while a job waits for the candidate (dashboard toggle "away"): queued last
              # step 15: what the submitted resume was (frozen at submission): hash, checker scores, role family, skills
              ("applications", "resume_sha", "TEXT"), ("applications", "ats_average", "REAL"),
              ("applications", "ats_scores", "TEXT"), ("applications", "role_id", "TEXT"),
              ("applications", "keywords", "TEXT"),
              # F02: the audit row records what the cleanup did, written after the attempt
              ("submission_attempts", "action_at", "TEXT"),
              # F06: hashes of the submitted artifacts (not derived from score history), the job identity, and how sure we are
              ("applications", "resume_pdf_sha256", "TEXT"), ("applications", "cover_letter_sha256", "TEXT"),
              ("applications", "jd_fingerprint", "TEXT"), ("applications", "snapshot_status", "TEXT"), ("applications", "snapshot_detail", "TEXT"), ("applications", "ats_sites", "TEXT"), ("verification_log", "status", "TEXT NOT NULL DEFAULT 'completed'"), ("verification_log", "detail", "TEXT")]

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
    {"amazon.jobs", "amazon.dev"},  # amazon.jobs codes come from Amazon's sign-in service (idprism-auth.amazon.dev)
]


def registrable_domain(host_or_url: str) -> str:
    """'https://acme.wd5.myworkdayjobs.com/x' -> 'myworkdayjobs.com'; 'no-reply@mail.foo.co.uk' -> 'foo.co.uk';
    'hr@unrelated.co.za' -> 'unrelated.co.za'. Public-suffix aware (tools/psl.py), so distinct tenants of a shared suffix
    (co.za, github.io, ...) are distinct identities. A host that is itself a public suffix is returned unchanged."""
    from urllib.parse import urlparse

    from jobagent.tools import psl

    host = host_or_url.strip().lower()
    if "://" in host:
        host = urlparse(host).netloc
    host = host.rsplit("@", 1)[-1].strip("<>. ").split(":")[0]
    return psl.registrable_domain(host)


def job_key(job_dir) -> str:
    """The identity of a job folder: `outputs/<company>/<job-id>`, however the folder was spelled (relative, absolute on the
    host, `/app/...` in the container, with `./` or a trailing slash). Everything keyed by a job folder (the application, its
    submission attempts, its checker scores) uses this key, so one job is one record. A path with no `outputs` directory in it
    is kept as normalised."""
    import os

    p = os.path.normpath(str(job_dir)).replace(os.sep, "/")
    parts = p.split("/")
    if "outputs" in parts:
        return "/".join(parts[len(parts) - 1 - parts[::-1].index("outputs"):])
    return p


def job_key_aliases(job_dir) -> tuple[str, ...]:
    """Every spelling a row for this job may carry (rows written before keys were canonical): the key, the container's absolute
    form, and the path as given."""
    key = job_key(job_dir)
    return tuple(dict.fromkeys((key, f"/app/{key}", str(job_dir), str(job_dir).rstrip("/"))))


def canonicalize_job_keys(conn: sqlite3.Connection) -> int:
    """One-time, idempotent: rewrite the job folder of applications, submission attempts and checker scores to its canonical key.
    A row whose canonical key is already taken by another application is left as it is (never merged: two records of one
    job are for the candidate to look at) and counted in `kv` under 'job_key_conflicts'. Returns the rows rewritten."""
    if conn.execute("SELECT 1 FROM kv WHERE key = 'job_key_v1'").fetchone():
        return 0
    changed, conflicts = 0, []
    with conn:
        for r in conn.execute("SELECT id, job_dir FROM applications").fetchall():
            key = job_key(r["job_dir"])
            if key == r["job_dir"]:
                continue
            if conn.execute("SELECT 1 FROM applications WHERE job_dir = ? AND id != ?", (key, r["id"])).fetchone():
                conflicts.append(r["job_dir"])
                continue
            conn.execute("UPDATE applications SET job_dir = ? WHERE id = ?", (key, r["id"]))
            conn.execute("UPDATE submission_attempts SET job_dir = ? WHERE job_dir = ?", (key, r["job_dir"]))
            changed += 1
        for table in ("ats_runs",):
            for (d,) in conn.execute(f"SELECT DISTINCT job_dir FROM {table}").fetchall():
                if job_key(d) != d:
                    conn.execute(f"UPDATE {table} SET job_dir = ? WHERE job_dir = ?", (job_key(d), d))
                    changed += 1
        conn.execute("INSERT OR REPLACE INTO kv (key, value) VALUES ('job_key_v1', ?)", (str(len(conflicts)),))
        if conflicts:
            conn.execute("INSERT OR REPLACE INTO kv (key, value) VALUES ('job_key_conflicts', ?)", (json.dumps(conflicts),))
    return changed


def record_url_alias(conn: sqlite3.Connection, alias: str, canonical: str) -> None:
    """Remember that two addresses are one posting (the apply link differs from the posting's own address)."""
    from jobagent.tools.jobqueue import canonical_url

    a, c = canonical_url(alias), canonical_url(canonical)
    if a and c and a != c:
        with conn:
            conn.execute("INSERT OR IGNORE INTO url_aliases (alias, canonical) VALUES (?, ?)", (a, c))


def job_for_urls(conn: sqlite3.Connection, *urls: str):
    """The `jobs` row for a posting however it was addressed: the address as given, its canonical form, and every recorded alias
    in either direction. None if the posting was never queued or read."""
    from jobagent.tools.jobqueue import canonical_url

    seen = {u for x in urls if x for u in (x, canonical_url(x))}
    frontier = set(seen)
    while frontier:                                    # follow aliases both ways until nothing new turns up
        marks = ",".join("?" * len(frontier))
        found = {r[0] for r in conn.execute(f"SELECT canonical FROM url_aliases WHERE alias IN ({marks})", tuple(frontier))} | \
                {r[0] for r in conn.execute(f"SELECT alias FROM url_aliases WHERE canonical IN ({marks})", tuple(frontier))}
        frontier = found - seen
        seen |= found
    marks = ",".join("?" * len(seen))
    return conn.execute(f"SELECT * FROM jobs WHERE url IN ({marks}) ORDER BY (role_id IS NULL), id LIMIT 1", tuple(seen)).fetchone()


def connect(path: Path | None = None) -> sqlite3.Connection:
    if path is None:
        from jobagent.config import get_settings

        path = get_settings().state_dir / "jobs.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)  # parallel workers wait for each other's writes
    conn.row_factory = sqlite3.Row
    conn.create_function("jobagent_job_key", 1, job_key, deterministic=True)
    try:
        conn.executescript(SCHEMA)
        missing=[(table,column,decl) for table,column,decl in MIGRATIONS
                 if column not in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}]
        if missing:
            # Only upgrades need a write lock. Recheck after acquiring it: another
            # worker may have added these columns while this connection waited.
            conn.execute("BEGIN IMMEDIATE")
            for table,column,decl in missing:
                if column not in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
            conn.commit()
        canonicalize_job_keys(conn)
        migrate_relationships(conn)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn
    except BaseException:
        conn.rollback()
        conn.close()
        raise


def ensure_job(conn: sqlite3.Connection, jd: dict, key: str) -> int:
    """Return the stable posting row, including source/apply aliases; never invent an external posting URL."""
    urls = [u for u in (jd.get("source_url"), jd.get("apply_url"), jd.get("url")) if u]
    row = job_for_urls(conn, *urls) if urls else None
    if row is not None:
        return row["id"]
    from jobagent.tools.jobqueue import canonical_url
    url = canonical_url(urls[0]) if urls else "jobagent:legacy:" + key
    conn.execute("INSERT INTO jobs (url, company, title, status) VALUES (?, ?, ?, ?) ON CONFLICT(url) DO NOTHING",
                 (url, jd.get("company"), jd.get("title"), "scraped" if urls else "identity_only"))
    return conn.execute("SELECT id FROM jobs WHERE url = ?", (url,)).fetchone()[0]


def migrate_relationships(conn: sqlite3.Connection) -> None:
    """Versioned, lossless linkage of existing surrogate IDs; legacy path columns remain compatible attributes.

    Unknown legacy postings get non-queueable identity rows. Orphan attempt history gets an uncertain application instead
    of being discarded or made retryable. Triggers keep old clients' path-only inserts linked during migration rollout.
    """
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
    if conn.execute("SELECT 1 FROM schema_migrations WHERE version='relationships_v1'").fetchone():
        return
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        if conn.execute("SELECT 1 FROM schema_migrations WHERE version='relationships_v1'").fetchone():
            conn.commit()
            return
        for row in conn.execute("SELECT DISTINCT job_dir FROM submission_attempts WHERE application_id IS NULL").fetchall():
            conn.execute("INSERT INTO applications (job_dir,state,notes) VALUES (?, 'submission_unknown', ?) ON CONFLICT(job_dir) DO NOTHING",
                         (row[0], "legacy attempt history: reconcile before another submission"))
        for row in conn.execute("SELECT * FROM applications WHERE job_id IS NULL").fetchall():
            ident = ensure_job(conn, dict(row), row["job_dir"])
            conn.execute("UPDATE applications SET job_id=? WHERE id=?", (ident, row["id"]))
        conn.execute("UPDATE submission_attempts SET application_id=(SELECT id FROM applications a WHERE a.job_dir=submission_attempts.job_dir) "
                     "WHERE application_id IS NULL")
        conn.execute("CREATE INDEX IF NOT EXISTS application_job_parent ON applications(job_id)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS attempt_application_number ON submission_attempts(application_id,attempt_no)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_open_application_attempt ON submission_attempts(application_id) "
                     "WHERE state IN ('in_progress','unknown')")
        conn.execute("""CREATE TRIGGER IF NOT EXISTS link_legacy_application AFTER INSERT ON applications WHEN NEW.job_id IS NULL BEGIN
            INSERT INTO jobs(url,company,title,status) VALUES(COALESCE(NULLIF(NEW.url,''),'jobagent:legacy:'||jobagent_job_key(NEW.job_dir)),NEW.company,NEW.title,'identity_only')
                ON CONFLICT(url) DO NOTHING;
            UPDATE applications SET job_dir=jobagent_job_key(NEW.job_dir),job_id=(SELECT id FROM jobs WHERE url=COALESCE(NULLIF(NEW.url,''),'jobagent:legacy:'||jobagent_job_key(NEW.job_dir))) WHERE id=NEW.id;
            END""")
        conn.execute("""CREATE TRIGGER IF NOT EXISTS link_legacy_attempt AFTER INSERT ON submission_attempts WHEN NEW.application_id IS NULL BEGIN
            INSERT INTO applications(job_dir,state,notes) VALUES(jobagent_job_key(NEW.job_dir),'submission_unknown','legacy attempt: reconcile before resubmitting')
                ON CONFLICT(job_dir) DO NOTHING;
            UPDATE submission_attempts SET job_dir=jobagent_job_key(NEW.job_dir),application_id=(SELECT id FROM applications WHERE job_dir=jobagent_job_key(NEW.job_dir)) WHERE id=NEW.id;
            END""")
        conn.execute("""CREATE TRIGGER IF NOT EXISTS validate_attempt_parent BEFORE INSERT ON submission_attempts
            WHEN NEW.application_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM applications WHERE id=NEW.application_id AND job_dir=NEW.job_dir)
            BEGIN SELECT RAISE(ABORT,'attempt belongs to a different application'); END""")
        conn.execute("""CREATE TRIGGER IF NOT EXISTS validate_attempt_parent_update BEFORE UPDATE OF application_id,job_dir ON submission_attempts
            WHEN NEW.application_id IS NULL OR NOT EXISTS(SELECT 1 FROM applications WHERE id=NEW.application_id AND job_dir=NEW.job_dir)
            BEGIN SELECT RAISE(ABORT,'attempt belongs to a different application'); END""")
        conn.execute("""CREATE TRIGGER IF NOT EXISTS require_application_parent BEFORE UPDATE OF job_id ON applications
            WHEN NEW.job_id IS NULL BEGIN SELECT RAISE(ABORT,'application must retain its posting identity'); END""")
        conn.execute("INSERT INTO schema_migrations(version) VALUES ('relationships_v1')")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def agent_domain_family(host_or_url: str, kind: str = "portal") -> tuple[set[str], str]:
    """(the domains a site's mail may come from, their kind): the site's registrable domain plus its ATS sender family.
    Pure: it reads nothing and writes nothing, so a dry run can ask it what it would record."""
    domain = registrable_domain(host_or_url)
    family = next((f for f in ATS_SENDER_FAMILIES if domain in f), {domain})
    kind = "ats" if len(family) > 1 or domain in {d for f in ATS_SENDER_FAMILIES for d in f} else kind
    return set(family), kind


def record_agent_domain(conn: sqlite3.Connection, host_or_url: str, kind: str = "portal") -> set[str]:
    """Remember a site the agent used (plus its ATS sender family). Returns the domains recorded."""
    family, kind = agent_domain_family(host_or_url, kind)
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


def log_verification(conn: sqlite3.Connection, message_id: str, sender: str, purpose: str,
                     status: str = "completed", detail: str = "") -> None:
    """One audit row per cleanup attempt: `completed` only if the mail really reached Trash, else `failed` (+ why)."""
    domain = sender.rsplit("@", 1)[-1].strip(">").lower() if "@" in sender else ""
    with conn:
        conn.execute(
            "INSERT INTO verification_log (message_id, sender, domain, purpose, status, detail) VALUES (?, ?, ?, ?, ?, ?)",
            (message_id, sender, domain, purpose, status, detail),
        )
