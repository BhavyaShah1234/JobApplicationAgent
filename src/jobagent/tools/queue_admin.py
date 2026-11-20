"""Edit the job queue by hand (dashboard): add job links and companies, set postings aside or back, change their order.

The queue is the `jobs` table. The runner (`tools/queue_runner.py`) takes the `queued` row with the highest `priority`, then
the best match score. Everything here goes through one write transaction per call, and never touches a row a runner is
working on (`running`) or one that was already applied to.

A row the candidate changed is marked `user_edited`: a later discovery run no longer overwrites its status or fields
(`tools/jobqueue.enqueue`), so an application the candidate set aside stays aside and a link they added stays queued.
Companies are kept in `watchlist`: the wish "work on this company". `jobagent run <company>` finds its openings.
"""

import concurrent.futures
import ipaddress
import re
import socket
import sqlite3
from urllib.parse import urlsplit

from jobagent.tools import jobqueue as Q

REQUEUE_FROM = {"skipped", "needs_review", "failed", "blocked", "scraped", "queued"}   # never: running, applied, already_applied, closed
DELETE_FROM = {"queued", "needs_review", "skipped", "failed", "blocked"}
TEXT_LIMIT = 200
PATH_BOARDS = ("greenhouse.io", "lever.co", "ashbyhq.com", "smartrecruiters.com", "workable.com", "jobvite.com", "gem.com", "rippling.com", "dover.com",
               "pinpointhq.com", "jazzhr.com")                        # company = first path segment (apply.workable.com/<company>/j/ID)
SUBDOMAIN_BOARDS = ("myworkdayjobs.com", "recruitee.com", "breezy.hr", "bamboohr.com", "teamtailor.com", "applytojob.com", "icims.com")   # company = subdomain
ATS_HOSTS = PATH_BOARDS + SUBDOMAIN_BOARDS


DNS_TIMEOUT_S = 5.0
_DNS = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="dns-check")


def _resolve(host: str, port: int) -> list[str]:
    """Addresses a name points at, with a time limit (a stuck resolver must not hold a dashboard request).

    Args:
        host: Name to look up.
        port: Port the browser would use.

    Returns:
        The addresses; empty when the name does not exist.

    Raises:
        ValueError: The lookup did not answer within `DNS_TIMEOUT_S` (the link is refused rather than trusted).
    """
    for attempt in (1, 2):                                       # one stalled answer from the resolver is common; two in a row is a real problem
        future = _DNS.submit(socket.getaddrinfo, host, port, 0, socket.SOCK_STREAM)
        try:
            return [i[4][0].split("%")[0] for i in future.result(timeout=DNS_TIMEOUT_S)]
        except concurrent.futures.TimeoutError:
            continue
        except OSError:
            return []
    raise ValueError("could not check where that address points (DNS did not answer twice); try again in a moment")


def check_public_url(url: str) -> None:
    """Refuse a link the agent's browser must never be sent to: another machine's network, this machine, or a cloud metadata address.

    Args:
        url: What the candidate typed into the dashboard.

    Raises:
        ValueError: Not http(s), has credentials, or its host is (or resolves to) a loopback, private, link-local or otherwise non-public address.
    """
    p = urlsplit(url.strip())
    host = (p.hostname or "").lower().rstrip(".")
    if p.scheme not in {"http", "https"} or not host or p.username or p.password:
        raise ValueError("only plain http(s) job links without a login in them are accepted")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".lan", ".home", ".corp")):
        raise ValueError("that address is on this machine or a private network")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is None and re.fullmatch(r"[0-9a-fx.]+", host):             # legacy IPv4 spellings a browser accepts: 0x7f.1, 2130706433, 0177.0.0.1
        try:
            literal = ipaddress.ip_address(socket.inet_aton(host))
        except OSError:
            literal = None
    if literal is not None:
        if not literal.is_global:
            raise ValueError("that address is on this machine or a private network")
        return
    addresses = _resolve(host, p.port or (443 if p.scheme == "https" else 80))      # [] = unresolvable: the browser fails on it too, nothing internal is reached
    if any(not ipaddress.ip_address(a).is_global for a in addresses):
        raise ValueError("that name points at this machine or a private network")


def board_of(url: str) -> tuple[str, str]:
    """Which job board address a posting belongs to, and the company name that address names.

    Args:
        url: A job posting address.

    Returns:
        `(board, company)`: the board address (`https://apply.workable.com/huggingface` for a board with the company in the path, the host itself
        for one with the company in a subdomain or an unknown host) and a readable company guess (`huggingface`).
    """
    p = urlsplit(url)
    host = (p.hostname or "").lower().removeprefix("www.")
    parts = [x for x in p.path.split("/") if x]
    root = f"{p.scheme}://{p.netloc.lower()}"
    if host.endswith(PATH_BOARDS) and parts:
        seg = parts[1] if host.endswith("dover.com") and parts[0] == "apply" and len(parts) > 1 else parts[0]
        return (root + "/" + seg if seg == parts[0] else root + "/apply/" + seg), seg
    labels = host.split(".")
    if host.endswith(SUBDOMAIN_BOARDS) and len(labels) >= 3:
        return root, labels[0].removeprefix("careers-").removeprefix("jobs-")
    if len(labels) >= 2:
        return root, labels[-3] if len(labels) >= 3 and labels[-2] in {"co", "com", "org"} else labels[-2]
    return root, "unknown"


def guess_company(url: str) -> str:
    """A readable company name from a job link, until the scrape finds the real one (`boards.greenhouse.io/acme/jobs/1` -> `acme`).

    Args:
        url: A job posting address.

    Returns:
        The company named by the job board address, else the host's main label; `unknown` when neither exists.
    """
    return board_of(url)[1]


def known_company(conn: sqlite3.Connection, url: str) -> str:
    """The company name already used for postings on the same job board, so a pasted link joins the same pacing bucket as its siblings.

    Args:
        conn: Open job database.
        url: A job posting address.

    Returns:
        The company of the first earlier posting under the same board address (the job board address: `host/company` or the host),
        or an empty string when there is none.
    """
    board = board_of(url)[0]
    like = board.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%"
    row = conn.execute("SELECT company FROM jobs WHERE url LIKE ? ESCAPE '\\' AND COALESCE(company, '') != '' AND source IS NOT 'dashboard' ORDER BY id LIMIT 1", (like,)).fetchone()
    return row["company"] if row else ""


def sync_company_slug(conn: sqlite3.Connection, url: str, company: str) -> None:
    """After a scrape names the real company, move a dashboard-added posting into that company's pacing bucket (its slug was a guess from the link).

    Args:
        conn: Open job database.
        url: The posting address as stored.
        company: The company the posting names.
    """
    if company and company.strip():
        with conn:
            conn.execute("UPDATE jobs SET company_slug = ? WHERE url IN (?, ?) AND source = 'dashboard'", (Q.slug(company), url, Q.canonical_url(url)))


def _clean(value, limit: int = TEXT_LIMIT) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _top(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COALESCE(MAX(priority), 0) FROM jobs WHERE status = 'queued'").fetchone()[0]


def _bottom(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COALESCE(MIN(priority), 0) FROM jobs WHERE status = 'queued'").fetchone()[0]


def _order_ids(conn: sqlite3.Connection) -> list[int]:
    """Ids of the queued jobs in the order the runner takes them."""
    return [r[0] for r in conn.execute("SELECT id FROM jobs WHERE status = 'queued' "
                                       "ORDER BY COALESCE(priority, 0) DESC, COALESCE(match_score, 0) DESC, id")]


def add_url(conn: sqlite3.Connection, url: str, *, company: str = "", where: str = "normal") -> dict:
    """Put one job link in the queue.

    Args:
        conn: Open job database.
        url: The posting address (http or https).
        company: Known company name; guessed from the link when empty.
        where: `top` (next to run), `normal` (by match score) or `bottom` (last).

    Returns:
        `{"input", "result", "note", "id"}`; result is added, requeued, already_queued, running, already_applied or closed.
    """
    if where not in {"top", "normal", "bottom"}:
        raise ValueError("where must be top, normal or bottom")
    canon = Q.canonical_url(url)
    try:
        check_public_url(canon)
    except ValueError as exc:
        return {"input": url, "result": "refused", "note": str(exc)}
    with conn:
        conn.execute("BEGIN IMMEDIATE") if not conn.in_transaction else None
        row = conn.execute("SELECT id, status, company FROM jobs WHERE url = ?", (canon,)).fetchone()
        prio = {"top": _top(conn) + 1, "bottom": _bottom(conn) - 1, "normal": 0}[where]
        if row is None:
            name = _clean(company) or known_company(conn, canon) or guess_company(canon)
            cur = conn.execute("INSERT INTO jobs (url, company, title, status, notes, company_slug, source, user_edited, priority) "
                               "VALUES (?,?,?,?,?,?,?,?,?)", (canon, name, "", "queued", "added by you in the dashboard",
                                                              Q.slug(name), "dashboard", 1, prio))
            return {"input": url, "result": "added", "note": f"queued as {name}", "id": cur.lastrowid}
        status = row["status"]
        if status == "running":
            return {"input": url, "result": "running", "note": "a run is working on it right now", "id": row["id"]}
        if status in {"applied", "already_applied"}:
            return {"input": url, "result": "already_applied", "note": f"already {status.replace('_', ' ')}; not queued again", "id": row["id"]}
        if status == "closed":
            return {"input": url, "result": "closed", "note": "the posting was closed", "id": row["id"]}
        if status == "queued" and where == "normal":
            return {"input": url, "result": "already_queued", "note": "already in the queue", "id": row["id"]}
        conn.execute("UPDATE jobs SET status = 'queued', user_edited = 1, priority = ?, notes = ?, "
                     "updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id = ?",
                     (prio, "put in the queue by you in the dashboard", row["id"]))
        return {"input": url, "result": "requeued" if status != "queued" else "moved", "note": f"was {status}", "id": row["id"]}


def add_company(conn: sqlite3.Connection, name: str, *, max_jobs: int = 5, note: str = "") -> dict:
    """Remember a company to work on (its openings are found by `jobagent run <company>`).

    Args:
        conn: Open job database.
        name: The company's name.
        max_jobs: At most this many openings per run (1-20).
        note: Free text for the candidate.

    Returns:
        `{"input", "result", "note", "id"}`; result is added or exists.
    """
    name = _clean(name, 120)
    if not name or name.startswith("-") or "/" in name or "://" in name or "@" in name or re.match(r"(?i)^[a-z][a-z0-9+.-]{1,14}:\S", name):
        return {"input": name, "result": "refused", "note": "not a company name"}
    if not 1 <= int(max_jobs) <= 20:
        raise ValueError("max_jobs must be between 1 and 20")
    key = Q.employer_key(name)
    with conn:
        row = conn.execute("SELECT id FROM watchlist WHERE company_key = ?", (key,)).fetchone()
        if row:
            conn.execute("UPDATE watchlist SET enabled = 1 WHERE id = ?", (row["id"],))
            return {"input": name, "result": "exists", "note": "already on the list (switched on)", "id": row["id"]}
        cur = conn.execute("INSERT INTO watchlist (company, company_key, note, max_jobs) VALUES (?,?,?,?)",
                           (name, key, _clean(note), int(max_jobs)))
        return {"input": name, "result": "added", "note": "added to the company list", "id": cur.lastrowid}


def add(conn: sqlite3.Connection, text: str, *, where: str = "normal") -> list[dict]:
    """Add whatever the candidate pasted: job links go to the queue, company names to the company list.

    Args:
        conn: Open job database.
        text: One input per line (a comma-separated line of company names is split).
        where: Queue position for new job links: top, normal or bottom.

    Returns:
        One result dict per input (see `add_url` and `add_company`), plus warnings.
    """
    from jobagent.graph.router import route
    lines = [x.strip() for x in str(text or "").splitlines() if x.strip()]
    if not lines or len(lines) > 100:
        raise ValueError("give between 1 and 100 lines")
    out: list = []
    pending: list[tuple[int, object]] = []                    # (position in `out`, job link item): added after the loop, so "top" keeps the typed order
    for line in lines:
        if len(line) > 1000 or line.startswith("-"):
            out.append({"input": line[:60], "result": "refused", "note": "not an acceptable input"})
            continue
        routed = route(line)
        out += [{"input": line[:80], "result": "warning", "note": w} for w in routed.warnings]
        if routed.kind == "file":
            out.append({"input": line[:80], "result": "refused", "note": "a file: use Start a run to give a CSV or XLSX file"})
            continue
        for item in routed.items:
            if item.kind == "url":
                pending.append((len(out), item))
                out.append(None)
            else:
                out.append(add_company(conn, item.value))
    for index, item in (reversed(pending) if where == "top" else pending):   # each "top" insert goes above the last, so go from the last typed to the first
        out[index] = add_url(conn, item.value, company=item.company, where=where)
    return out


def reorder(conn: sqlite3.Connection, ids: list[int]) -> int:
    """Make the queued jobs run in this order; queued jobs not listed follow, in their existing order.

    Args:
        conn: Open job database.
        ids: Job ids, first to run first. Ids that are not queued are ignored.

    Returns:
        How many queued jobs now have an explicit position.
    """
    if not isinstance(ids, list) or not all(type(i) is int for i in ids) or len(ids) > 5000:
        raise ValueError("ids must be a list of job ids")
    with conn:
        conn.execute("BEGIN IMMEDIATE") if not conn.in_transaction else None
        current = _order_ids(conn)
        wanted = [i for i in dict.fromkeys(ids) if i in set(current)]
        order = wanted + [i for i in current if i not in set(wanted)]
        n = len(order)
        for pos, jid in enumerate(order):
            conn.execute("UPDATE jobs SET priority = ?, user_edited = 1 WHERE id = ? AND status = 'queued'", (n - pos, jid))
    return n


def move(conn: sqlite3.Connection, job_id: int, to: str) -> int:
    """Move one queued job: `top`, `bottom`, `up` or `down`.

    Args:
        conn: Open job database.
        job_id: The job to move.
        to: Where.

    Returns:
        Its new 1-based position.

    Raises:
        LookupError: The job is not queued.
        ValueError: Unknown direction.
    """
    if to not in {"top", "bottom", "up", "down"}:
        raise ValueError("to must be top, bottom, up or down")
    order = _order_ids(conn)
    if job_id not in order:
        raise LookupError("only a queued job can be moved")
    i = order.index(job_id)
    order.pop(i)
    j = {"top": 0, "bottom": len(order), "up": max(i - 1, 0), "down": min(i + 1, len(order))}[to]
    order.insert(j, job_id)
    reorder(conn, order)
    return j + 1


def set_state(conn: sqlite3.Connection, ids: list[int], action: str) -> dict:
    """Put postings back in the queue (`queue`) or set them aside (`skip`).

    Args:
        conn: Open job database.
        ids: Job ids.
        action: `queue` or `skip`.

    Returns:
        `{"changed": n, "refused": [{"id", "why"}]}`; a running or already applied posting is refused.
    """
    if action not in {"queue", "skip"}:
        raise ValueError("action must be queue or skip")
    if not isinstance(ids, list) or not ids or not all(type(i) is int for i in ids) or len(ids) > 500:
        raise ValueError("give a list of job ids")
    changed, refused = 0, []
    with conn:
        conn.execute("BEGIN IMMEDIATE") if not conn.in_transaction else None
        for jid in ids:
            row = conn.execute("SELECT status FROM jobs WHERE id = ?", (jid,)).fetchone()
            if row is None:
                refused.append({"id": jid, "why": "no such posting"})
            elif action == "queue" and row["status"] not in REQUEUE_FROM:
                refused.append({"id": jid, "why": f"a {row['status'].replace('_', ' ')} posting cannot be queued"})
            elif action == "skip" and row["status"] not in DELETE_FROM | {"scraped"}:
                refused.append({"id": jid, "why": f"a {row['status'].replace('_', ' ')} posting cannot be set aside"})
            else:
                if action == "queue":
                    conn.execute("UPDATE jobs SET status = 'queued', user_edited = 1, priority = ?, notes = ?, "
                                 "updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id = ?",
                                 (_bottom(conn) - 1 if row["status"] != "queued" else 0, "put in the queue by you", jid))
                else:
                    conn.execute("UPDATE jobs SET status = 'skipped', user_edited = 1, notes = ?, "
                                 "updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id = ?", ("set aside by you", jid))
                changed += 1
    return {"changed": changed, "refused": refused}


def edit(conn: sqlite3.Connection, job_id: int, **fields) -> dict:
    """Change a posting's company, title, location or note.

    Args:
        conn: Open job database.
        job_id: The posting.
        **fields: Any of `company`, `title`, `location`, `notes`.

    Returns:
        The changed values.

    Raises:
        LookupError: No such posting, or a runner is working on it.
        ValueError: Nothing to change.
    """
    allowed = {k: _clean(v) for k, v in fields.items() if k in {"company", "title", "location", "notes"} and v is not None}
    if not allowed:
        raise ValueError("give company, title, location or notes")
    if "company" in allowed and not allowed["company"]:
        raise ValueError("the company cannot be empty")
    with conn:
        conn.execute("BEGIN IMMEDIATE") if not conn.in_transaction else None
        row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise LookupError("no such posting")
        if row["status"] == "running":
            raise LookupError("a run is working on this posting; wait until it finishes")
        if "company" in allowed:
            allowed["company_slug"] = Q.slug(allowed["company"])
        sets = ", ".join(f"{k} = ?" for k in allowed)
        conn.execute(f"UPDATE jobs SET {sets}, user_edited = 1, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id = ?",
                     (*allowed.values(), job_id))
    return allowed


def delete(conn: sqlite3.Connection, ids: list[int]) -> dict:
    """Remove postings that have no application and are not being worked on.

    Args:
        conn: Open job database.
        ids: Job ids.

    Returns:
        `{"deleted": n, "refused": [{"id", "why"}]}`.
    """
    if not isinstance(ids, list) or not ids or not all(type(i) is int for i in ids) or len(ids) > 500:
        raise ValueError("give a list of job ids")
    deleted, refused = 0, []
    with conn:
        conn.execute("BEGIN IMMEDIATE") if not conn.in_transaction else None
        for jid in ids:
            row = conn.execute("SELECT status, url FROM jobs WHERE id = ?", (jid,)).fetchone()
            if row is None:
                refused.append({"id": jid, "why": "no such posting"})
            elif row["status"] not in DELETE_FROM:
                refused.append({"id": jid, "why": f"a {row['status'].replace('_', ' ')} posting is kept (it is a record)"})
            elif conn.execute("SELECT 1 FROM applications WHERE job_id = ? OR url = ? LIMIT 1", (jid, row["url"])).fetchone():
                refused.append({"id": jid, "why": "it has an application on record"})
            else:
                conn.execute("DELETE FROM jobs WHERE id = ?", (jid,))
                deleted += 1
    return {"deleted": deleted, "refused": refused}


def watch_update(conn: sqlite3.Connection, watch_id: int, *, enabled: bool | None = None, max_jobs: int | None = None,
                 note: str | None = None, remove: bool = False) -> dict:
    """Change or remove one entry of the company list.

    Args:
        conn: Open job database.
        watch_id: The entry.
        enabled: Switch it on or off.
        max_jobs: At most this many openings per run (1-20).
        note: Free text.
        remove: Delete the entry.

    Returns:
        `{"id", "removed"}`.

    Raises:
        LookupError: No such entry.
    """
    with conn:
        if conn.execute("SELECT 1 FROM watchlist WHERE id = ?", (watch_id,)).fetchone() is None:
            raise LookupError("no such company on the list")
        if remove:
            conn.execute("DELETE FROM watchlist WHERE id = ?", (watch_id,))
            return {"id": watch_id, "removed": True}
        if max_jobs is not None:
            if type(max_jobs) is not int or not 1 <= max_jobs <= 20:
                raise ValueError("max_jobs must be between 1 and 20")
            conn.execute("UPDATE watchlist SET max_jobs = ? WHERE id = ?", (max_jobs, watch_id))
        if enabled is not None:
            conn.execute("UPDATE watchlist SET enabled = ? WHERE id = ?", (1 if enabled else 0, watch_id))
        if note is not None:
            conn.execute("UPDATE watchlist SET note = ? WHERE id = ?", (_clean(note), watch_id))
    return {"id": watch_id, "removed": False}


def mark_watch_run(conn: sqlite3.Connection, names: list[str]) -> None:
    """Record that a run was started for these companies (shows as "last run" on the list)."""
    with conn:
        for name in names:
            conn.execute("UPDATE watchlist SET last_run_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE company_key = ?", (Q.employer_key(name),))


def listing(conn: sqlite3.Connection, limit: int = 500) -> list[dict]:
    """The queue as the candidate should see it: running first, then queued in the order the runner will take them, then the rest.

    Args:
        conn: Open job database.
        limit: At most this many rows.

    Returns:
        One dict per posting with `position` (1-based, queued only) and `editable`.
    """
    rows = conn.execute(
        "SELECT j.id, j.url, j.company, j.title, j.location, j.status, j.notes, j.role_id, j.match_score, j.priority, j.user_edited, j.source, "
        "j.claimed_by, j.updated_at, (SELECT COUNT(*) FROM applications a WHERE a.job_id = j.id OR a.url = j.url) AS applications FROM jobs j "
        "ORDER BY CASE j.status WHEN 'running' THEN 0 WHEN 'queued' THEN 1 WHEN 'needs_review' THEN 2 ELSE 3 END, "
        "CASE WHEN j.status = 'queued' THEN COALESCE(j.priority, 0) END DESC, CASE WHEN j.status = 'queued' THEN COALESCE(j.match_score, 0) END DESC, "
        "j.updated_at DESC, j.id DESC LIMIT ?", (limit,)).fetchall()
    out, position = [], 0
    for r in rows:
        d = {k: r[k] for k in r.keys()}
        for k in ("url", "company", "title", "location", "notes"):
            d[k] = (d[k] or "")[:300]
        d["user_edited"] = bool(d["user_edited"])
        d["editable"] = d["status"] != "running"
        if d["status"] == "queued":
            position += 1
            d["position"] = position
        out.append(d)
    return out


def watch_listing(conn: sqlite3.Connection) -> list[dict]:
    """The company list, each with how many postings are known for it and how many are queued."""
    conn.create_function("employer_key", 1, lambda s: Q.employer_key(s or ""), deterministic=True)
    rows = conn.execute(
        "SELECT w.id, w.company, w.note, w.max_jobs, w.enabled, w.added_at, w.last_run_at, "
        "(SELECT COUNT(*) FROM jobs j WHERE employer_key(COALESCE(NULLIF(j.company_slug,''), j.company, '')) = w.company_key) AS postings, "
        "(SELECT COUNT(*) FROM jobs j WHERE j.status = 'queued' AND employer_key(COALESCE(NULLIF(j.company_slug,''), j.company, '')) = w.company_key) AS queued "
        "FROM watchlist w ORDER BY w.company COLLATE NOCASE").fetchall()
    return [{**{k: r[k] for k in r.keys()}, "enabled": bool(r["enabled"])} for r in rows]
