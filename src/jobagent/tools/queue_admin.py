"""Edit the job queue by hand from the dashboard: delete postings that are waiting.

The queue is the `jobs` table. Only a posting that nobody is working on and that has no application on record can be deleted
(`queued`, `needs_review`, `skipped`, `failed`, `blocked`); a `running`, `scraped`, `applied` or `closed` posting is a record or is in use and
is refused with the reason. A deleted address is remembered in `queue_removed`, so the next discovery run does not put it back.
"""

import sqlite3

DELETE_FROM = frozenset({"queued", "needs_review", "skipped", "failed", "blocked"})
MAX_IDS = 500


def delete(conn: sqlite3.Connection, ids: list[int]) -> dict:
    """Remove waiting postings and remember their addresses.

    Args:
        conn: Open job database (`db.connect`).
        ids: Job ids to delete (at most 500).

    Returns:
        `{"deleted": n, "refused": [{"id": ..., "why": ...}]}`: a posting that is running, was already worked on or has an application is refused, not removed.

    Raises:
        ValueError: `ids` is not a non-empty list of integers.
    """
    if not isinstance(ids, list) or not ids or len(ids) > MAX_IDS or not all(type(i) is int for i in ids):
        raise ValueError(f"give a list of 1 to {MAX_IDS} job ids")
    deleted, refused = 0, []
    with conn:
        for jid in dict.fromkeys(ids):
            row = conn.execute("SELECT status, url, company, title FROM jobs WHERE id = ?", (jid,)).fetchone()
            if row is None:
                refused.append({"id": jid, "why": "no such posting"})
            elif row["status"] not in DELETE_FROM:
                refused.append({"id": jid, "why": f"a {row['status'].replace('_', ' ')} posting is not deleted (it is in use or a record)"})
            elif conn.execute("SELECT 1 FROM applications WHERE job_id = ? OR url = ? LIMIT 1", (jid, row["url"])).fetchone():
                refused.append({"id": jid, "why": "it has an application on record"})
            else:
                conn.execute("INSERT OR REPLACE INTO queue_removed (url, company, title) VALUES (?, ?, ?)", (row["url"], row["company"], row["title"]))
                conn.execute("DELETE FROM jobs WHERE id = ?", (jid,))
                deleted += 1
    return {"deleted": deleted, "refused": refused}
