"""Step 13: human review -> submit -> confirmation email.

`jobagent submit <job_dir> <company>` fills the application to its review screen (step 12, same browser session),
writes `application/review.md` (every field with its value and source, the flags, the documents, the ATS score,
screenshots) and asks the candidate (`jobagent hitl`): 'approve' clicks Submit, 'skip' leaves it unsubmitted. The
candidate may first fix anything in noVNC. Nothing is ever submitted without that 'approve'.

After submitting, the confirmation page is checked and screenshotted, and the agent inbox is watched for the
confirmation mail (sender: the company or its ATS, sent after the submission, naming the company or the role). Every
step is recorded in the `applications` table; `jobagent confirm <job_dir>` checks the inbox again later.
"""

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from playwright.sync_api import Page

from jobagent import db
from jobagent.tools import forms
from jobagent.tools.browser import screenshot

CONFIRMED_PAGE = re.compile(r"(?i)thank(s| you) for (applying|your (application|interest))|application (has been |was )?"
                            r"(received|submitted)|we('ve| have) received your application|successfully submitted|"
                            r"submission (is )?complete|your application is complete")
CONFIRMATION_MAIL = re.compile(r"(?i)thank(s| you) for (applying|your (application|interest))|application (has been |was )?"
                               r"(received|submitted)|we('ve| have) received|received your application|"
                               r"application confirmation|confirm(ing|ation of) your application")


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def record(conn, job_dir: Path, jd: dict, **fields) -> None:
    """Insert or update the application's row."""
    key = str(job_dir)
    with conn:
        conn.execute("INSERT INTO applications (job_dir, company, title, url, portal, state) VALUES (?, ?, ?, ?, ?, ?) "
                     "ON CONFLICT(job_dir) DO NOTHING",
                     (key, jd.get("company"), jd.get("title"), jd.get("apply_url") or jd.get("source_url"),
                      jd.get("portal"), fields.get("state", "filled")))
        if fields:
            cols = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(f"UPDATE applications SET {cols} WHERE job_dir = ?", (*fields.values(), key))


def ats_line(conn, job_dir: Path) -> str:
    rows = conn.execute("SELECT site, score FROM ats_runs WHERE job_dir = ? AND round = (SELECT MAX(round) FROM "
                        "ats_runs WHERE job_dir = ?)", (str(job_dir), str(job_dir))).fetchall()
    scores = {r["site"]: r["score"] for r in rows if r["score"] is not None}
    if not scores:
        return "no ATS scores recorded"
    return f"average {sum(scores.values()) / len(scores):.1f} " + json.dumps(scores)


def review_text(job_dir: Path, result: dict, jd: dict, ats: str) -> str:
    """The review packet: what will be sent, where each answer came from, and what to look at first."""
    fields = result.get("fields", [])
    flagged = [e for e in fields if e.get("flag") or e.get("error") or not e.get("verified")]
    cell = lambda x: str(x).replace("|", "/").replace("\n", " ")[:110]  # noqa: E731
    lines = [f"# Review: {jd.get('company', '')} - {jd.get('title', '')}", "",
             f"- Posting: {result.get('url')}",
             f"- Resume: {job_dir / 'resume.pdf'}" + (f" | cover letter: {job_dir / 'cover_letter.pdf'}"
                                                      if (job_dir / 'cover_letter.pdf').exists() else ""),
             f"- ATS: {ats}",
             f"- Fields: {len(fields)}, read back OK: {result.get('verified', 0)}; to look at: {len(flagged)}",
             f"- Screenshots: {', '.join(p['screenshot'] for p in result.get('pages', []))} (in {job_dir / 'application'})",
             "- The form is open on its review screen in noVNC (http://localhost:6080): fix anything there first.", ""]
    if flagged:
        lines += ["## Look at these first", "", "| Field | Value | Note |", "|---|---|---|"]
        lines += [f"| {cell(e['label'][:70])} | {cell(e.get('readback', ''))} | "
                  f"{cell(e.get('flag') or e.get('error') or 'not confirmed on the page')} |" for e in flagged]
        lines.append("")
    lines += ["## Every field", "", "| Page | Field | Value | Source |", "|---|---|---|---|"]
    lines += [f"| {e['page']} | {cell(e['label'][:70])} | {cell(e.get('readback', ''))} | {e.get('source', '')} |"
              for e in fields]
    return "\n".join(lines) + "\n"


SUBMIT_FIRST = [re.compile(r"(?i)^\s*submit( (my |your )?application)?\s*$"),
                re.compile(r"(?i)submit|send application|^\s*finish\b|complete (my |your )?application|^\s*apply\s*$")]


def click_submit(page: Page, view: forms.PageView) -> str | None:
    """The one place the agent clicks Submit (after the candidate's 'approve')."""
    for pat in SUBMIT_FIRST:
        if (t := forms.click_button(page, view, pat, allow_submit=True)):
            return t
    return None


def submitted_page(page: Page, timeout_s: float = 45) -> tuple[bool, str, list[str]]:
    """(confirmed, the page's text, errors) after Submit: a thank-you / received page, or the errors that stopped it."""
    deadline = datetime.now() + timedelta(seconds=timeout_s)
    text, errors = "", []
    while datetime.now() < deadline:
        page.wait_for_timeout(3000)
        try:
            text = " ".join(f.locator("body").inner_text(timeout=5000) for f in forms.frames_of(page))
        except Exception:
            continue
        if CONFIRMED_PAGE.search(text):
            return True, text, []
        errors = forms.read_page(page).errors
    return False, text, errors


def wait_for_confirmation(inbox, conn, jd: dict, since: datetime, timeout_s: float = 900):
    """The confirmation mail: from the company's or its ATS's domain, sent after the submission, about this
    application (names the company or the role). None when it hasn't come within the timeout."""
    url = jd.get("apply_url") or jd.get("source_url") or ""
    allowed = db.record_agent_domain(conn, url, "portal")
    company = (jd.get("company") or "").lower()
    if company:
        allowed |= {company.replace(" ", "") + ".com"}
    words = [w for w in [company, *(jd.get("title") or "").lower().split()[:3]] if len(w) > 3]
    criteria = f'SINCE "{(since - timedelta(days=1)).strftime("%d-%b-%Y")}"'

    def match(m) -> bool:
        if m.date is None or m.date < since - timedelta(minutes=2):
            return False
        from jobagent.tools.inbox import sender_domain
        about = f"{m.subject} {m.text[:3000]}".lower()
        from_them = sender_domain(m) in allowed or (company and company.split()[0] in m.sender.lower())
        return bool(from_them and CONFIRMATION_MAIL.search(about) and any(w in about for w in words))

    try:
        return inbox.wait_for(match, criteria, timeout_s=timeout_s, poll_s=20)
    except TimeoutError:
        return None


def make_at_review(job_dir: Path, jd: dict, hitl, inbox, conn, log, ask_s: float = 3600, mail_s: float = 900):
    """The step 13 hook for apply.run: review packet, the candidate's decision, Submit, confirmation."""
    def at_review(page: Page, view: forms.PageView, result: dict) -> dict:
        out = job_dir / "application"
        (out / "review.md").write_text(review_text(job_dir, result, jd, ats_line(conn, job_dir)))
        screenshot(page, str(out / "review.png"))
        record(conn, job_dir, jd, state="filled", filled_at=_now())
        flagged = sum(1 for e in result.get("fields", []) if e.get("flag") or e.get("error") or not e.get("verified"))
        from jobagent.tools.hitl import HitlTimeout
        try:
            answer = hitl.ask(
                f"{jd.get('company')} - {jd.get('title')}: the application is filled and open on its review screen "
                f"(noVNC http://localhost:6080). Review {out / 'review.md'} ({result.get('verified', 0)}/"
                f"{len(result.get('fields', []))} fields confirmed, {flagged} to look at). Fix anything in noVNC "
                "if needed, then answer 'approve' to SUBMIT it or 'skip' to leave it unsubmitted.",
                timeout_s=ask_s).strip().lower()
        except HitlTimeout:
            answer = ""
        if answer != "approve":
            record(conn, job_dir, jd, state="skipped", decided_at=_now(),
                   notes="no answer in time" if not answer else f"candidate answered {answer!r}")
            log(f"submit: not submitted ({answer or 'no answer'})")
            return {"state": "skipped", "answer": answer}
        record(conn, job_dir, jd, state="approved", decided_at=_now())
        view = forms.read_page(page)  # the candidate may have changed things in noVNC
        clicked = click_submit(page, view)
        if not clicked:
            record(conn, job_dir, jd, state="failed", notes=f"no Submit button; buttons: {view.buttons[:12]}")
            log(f"submit: no Submit button found ({view.buttons[:12]})")
            return {"state": "failed", "reason": "no Submit button"}
        since = datetime.now(UTC)
        log(f"submit: clicked {clicked!r}")
        ok, text, errors = submitted_page(page)
        screenshot(page, str(out / "submitted.png"))
        if not ok and errors:
            record(conn, job_dir, jd, state="failed", notes="the site refused: " + "; ".join(errors)[:400])
            log(f"submit: the site refused it: {errors[:4]}")
            return {"state": "failed", "errors": errors}
        record(conn, job_dir, jd, state="submitted", submitted_at=_now(),
               notes=None if ok else "no thank-you page seen; waiting for the confirmation mail")
        conn.execute("UPDATE jobs SET status = 'applied', updated_at = ? WHERE url = ?",
                     (_now(), jd.get("source_url") or ""))
        conn.commit()
        log("submit: submitted" + ("" if ok else " (no thank-you page seen)") + "; watching the inbox for the "
            "confirmation")
        mail = wait_for_confirmation(inbox, conn, jd, since, timeout_s=mail_s)
        if mail:
            record(conn, job_dir, jd, state="confirmed", confirmed_at=_now(), confirmation_id=mail.message_id,
                   confirmation=f"{mail.sender} | {mail.subject}")
            log(f"submit: confirmation mail: {mail.subject!r} from {mail.sender}")
            return {"state": "confirmed", "page": ok, "mail": mail.subject}
        log("submit: no confirmation mail yet; check later with `jobagent confirm`")
        return {"state": "submitted", "page": ok}
    return at_review


def confirm(job_dir: Path, inbox, conn, timeout_s: float = 60) -> str:
    """Look for a submitted application's confirmation mail again."""
    row = conn.execute("SELECT * FROM applications WHERE job_dir = ?", (str(job_dir),)).fetchone()
    if row is None or row["state"] not in ("submitted", "confirmed"):
        return f"not submitted ({row['state'] if row else 'no record'})"
    if row["state"] == "confirmed":
        return f"confirmed: {row['confirmation']}"
    jd = json.loads((job_dir / "jd.json").read_text())
    since = datetime.strptime(row["submitted_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    mail = wait_for_confirmation(inbox, conn, jd, since, timeout_s=timeout_s)
    if mail is None:
        return "no confirmation mail yet"
    record(conn, job_dir, jd, state="confirmed", confirmed_at=_now(), confirmation_id=mail.message_id,
           confirmation=f"{mail.sender} | {mail.subject}")
    return f"confirmed: {mail.subject} ({mail.sender})"
