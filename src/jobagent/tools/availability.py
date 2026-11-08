"""Postings that can't be applied to: closed, expired, removed, or already applied to.

The agent stops on these instead of failing: `Unavailable` names the reason, the `jobs` row gets the status
(`closed` / `already_applied`), and the commands exit with `EXIT_UNAVAILABLE` (3) so a chain such as
`jobagent scrape U && jobagent tailor ... && jobagent submit ...` stops there. A run over several postings records
the reason and moves on to the next one.

Signals, cheapest first: the page's HTTP status (404 / 410), the schema.org JobPosting `validThrough` date, and the
page's own wording ("no longer accepting applications", "this job has expired", "you have already applied").
"""

import re
from datetime import UTC, datetime

EXIT_UNAVAILABLE = 3

CLOSED = re.compile(
    r"(?i)\b("
    r"no longer (accepting|taking|open (for|to)|available|active|being accepted|posted|exists?)|"
    r"(is|are) not (currently )?accepting (new )?applications|not accepting applications|"
    r"applications? (for this (job|position|role) )?(are|is|have been|has been) closed|"
    r"(this|the) (job|position|role|posting|opening|requisition|vacancy)( posting)? (has been|is|was) "
    r"(closed|filled|removed|deleted|expired|cancell?ed|archived|taken down|no longer)|"
    r"(job|position|posting|vacancy|requisition) (has )?(expired|closed)|"
    r"(job|position|posting|page) (could not be|cannot be|was not) found|"
    r"(we )?(couldn['’]t|could not|can['’]t|cannot) find (the|this|that) (job|position|posting|page|role)|"
    r"(job|position|posting) (is )?(no longer|not) (available|found)|"
    r"the (job|page) you('re| are) looking for (is no longer|has been|was|does not|doesn['’]t|cannot|can['’]t)|"
    r"position (has been|is) filled"
    r")")
ALREADY = re.compile(
    r"(?i)\b(you('ve| have) already (applied|submitted)|already (applied|submitted an application) (to|for) "
    r"(this|the) (job|position|role|posting)|an application (for this (job|position|role) )?(has already been|"
    r"was already) submitted)")
# a page that is a form or a posting with a way to apply still counts as open, even if a footer says "closed"
APPLY_ACTION = re.compile(r"(?i)^\s*(apply( now| for this (job|position|role))?|start (your )?application|"
                          r"submit (your )?application|i'?m interested)\s*$")


class Unavailable(Exception):
    """The posting can't be applied to. `kind`: closed | expired | not_found | already_applied."""

    def __init__(self, kind: str, reason: str, url: str = ""):
        super().__init__(f"{kind}: {reason}" + (f" ({url})" if url else ""))
        self.kind, self.reason, self.url = kind, reason, url

    @property
    def status(self) -> str:
        """The `jobs` / `applications` status it maps to."""
        return "already_applied" if self.kind == "already_applied" else "closed"


def sentence(text: str, m: re.Match) -> str:
    """The sentence around a match, for the log and the jobs table."""
    start = max(text.rfind(".", 0, m.start()), text.rfind("\n", 0, m.start())) + 1
    ends = [i for i in (text.find(".", m.end()), text.find("\n", m.end())) if i != -1]
    return re.sub(r"\s+", " ", text[start:min(ends) + 1 if ends else m.end() + 120]).strip()[:240]


def from_text(text: str, url: str = "", can_apply: bool = False) -> Unavailable | None:
    """The page's own words say it's closed or already applied to. A page offering an Apply action only counts
    as closed when the closed sentence is near the top (a job page, not an unrelated footer)."""
    if m := ALREADY.search(text):
        return Unavailable("already_applied", sentence(text, m), url)
    m = CLOSED.search(text)
    if m and (not can_apply or m.start() < 3000):
        kind = "not_found" if re.search(r"(?i)found|find|looking for", m.group(0)) else \
            "expired" if re.search(r"(?i)expired", m.group(0)) else "closed"
        return Unavailable(kind, sentence(text, m), url)
    return None


def from_jsonld(posting: dict | None, url: str = "", now: datetime | None = None) -> Unavailable | None:
    """schema.org JobPosting `validThrough` in the past: the posting has expired."""
    raw = str((posting or {}).get("validThrough") or "").strip()
    if not raw:
        return None
    try:
        until = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if until.tzinfo is None:
        until = until.replace(tzinfo=UTC)
    if until < (now or datetime.now(UTC)):
        return Unavailable("expired", f"the posting's validThrough date {raw} has passed", url)
    return None


def from_status(status: int | None, url: str = "") -> Unavailable | None:
    """404 Not Found / 410 Gone for the posting itself."""
    if status in (404, 410):
        return Unavailable("not_found", f"the page answered HTTP {status}", url)
    return None


def check_page(page, response=None, posting: dict | None = None) -> Unavailable | None:
    """All signals for a loaded Playwright page (the goto response and JSON-LD when the caller has them)."""
    url = page.url
    if response is not None and (u := from_status(response.status, url)):
        return u
    if u := from_jsonld(posting, url):
        return u
    try:
        text = page.inner_text("body", timeout=5000)
        actions = page.eval_on_selector_all(
            "a, button, input[type=submit], [role=button]",
            "els => els.filter(e => e.offsetParent !== null).map(e => (e.innerText || e.value || '').trim())")
    except Exception:
        return None
    return from_text(text, url, can_apply=any(APPLY_ACTION.match(a or "") for a in actions))


def mark(url: str, u: Unavailable, conn=None) -> None:
    """Record the reason on the job's `jobs` row (created if the posting was never read)."""
    from jobagent import db

    conn = conn or db.connect()
    with conn:
        conn.execute("INSERT INTO jobs (url, status, notes) VALUES (?, ?, ?) ON CONFLICT(url) DO UPDATE SET "
                     "status = excluded.status, notes = excluded.notes, "
                     "updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')",
                     (url, u.status, f"{u.kind}: {u.reason}"))


def known(url: str, conn=None) -> Unavailable | None:
    """The reason recorded for this posting by an earlier step, if it was found unavailable."""
    from jobagent import db

    row = (conn or db.connect()).execute("SELECT status, notes FROM jobs WHERE url = ?", (url,)).fetchone()
    if row and row["status"] in ("closed", "already_applied"):
        kind, _, reason = (row["notes"] or f"{row['status']}: ").partition(": ")
        return Unavailable(kind or row["status"], reason or "recorded earlier", url)
    return None
