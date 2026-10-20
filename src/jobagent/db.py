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
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    if path is None:
        from jobagent.config import get_settings

        path = get_settings().state_dir / "jobs.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def log_verification(conn: sqlite3.Connection, message_id: str, sender: str, purpose: str) -> None:
    domain = sender.rsplit("@", 1)[-1].strip(">").lower() if "@" in sender else ""
    with conn:
        conn.execute(
            "INSERT INTO verification_log (message_id, sender, domain, purpose) VALUES (?, ?, ?, ?)",
            (message_id, sender, domain, purpose),
        )
