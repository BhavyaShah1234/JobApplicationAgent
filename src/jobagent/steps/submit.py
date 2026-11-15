"""Step 13: human review -> submit -> confirmation email.

`jobagent submit <job_dir> <company>` fills the application to its review screen (step 12, same browser session),
writes `application/review.md` (every field with its value and source, the flags, the documents, the ATS score,
screenshots) and asks the candidate (`jobagent hitl`): 'approve' clicks Submit, 'skip' leaves it unsubmitted. The
candidate may first fix anything in noVNC. Nothing is ever submitted without that 'approve'.

After submitting, the confirmation page is checked and screenshotted, and the agent inbox is watched for the
confirmation mail (sender: the company or its ATS, sent after the submission, naming the company or the role). Every
step is recorded in the `applications` table; `jobagent confirm <job_dir>` checks the inbox again later.
"""

import hashlib
import json
import re
from dataclasses import dataclass
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


# States in which the application has been (or may have been) sent. They only move forward; nothing here rewrites them
# to filled / approved / skipped / failed. A new attempt starts only after the candidate or the evidence settles an
# uncertain one as "not sent".
SENT = ("submission_in_progress", "submission_unknown", "submitted", "confirmed")
UNCERTAIN = ("submission_in_progress", "submission_unknown")
_FORWARD = {"submission_in_progress": {"submission_unknown", "submitted", "confirmed"},
            "submission_unknown": {"submitted", "confirmed"},
            "submitted": {"confirmed"}, "confirmed": set()}


def record(conn, job_dir: Path, jd: dict, via_attempt: bool = False, **fields) -> None:
    """Insert or update the application's row. A row that was sent, or may have been, is never moved back: a write that
    would rewrite its state (and the timestamps and notes that go with it) is ignored. Only the attempt code, having
    proved that nothing went out (no Submit button, or the site refused the form), passes via_attempt=True to move an
    attempt in progress to failed."""
    key = db.job_key(job_dir)
    cur = conn.execute("SELECT state FROM applications WHERE job_dir = ?", (key,)).fetchone()
    if cur and cur["state"] in SENT:
        new = fields.get("state")
        allowed = _FORWARD[cur["state"]] | ({"failed"} if via_attempt and cur["state"] == "submission_in_progress" else set())
        if new is not None and new != cur["state"] and new not in allowed:
            return                                    # would move a sent application backwards
        if new is None and {"filled_at", "decided_at"} & fields.keys():
            return
    if jd.get("apply_url") and jd.get("source_url"):
        db.record_url_alias(conn, jd["apply_url"], jd["source_url"])     # F25: the apply link and the posting are one job
    with conn:
        job_id = db.ensure_job(conn, jd, key)
        conn.execute("INSERT INTO applications (job_id, job_dir, company, title, url, portal, state) VALUES (?, ?, ?, ?, ?, ?, ?) "
                     "ON CONFLICT(job_dir) DO NOTHING",
                     (job_id, key, jd.get("company"), jd.get("title"), jd.get("apply_url") or jd.get("source_url"),
                      jd.get("portal"), fields.get("state", "filled")))
        conn.execute("UPDATE applications SET job_id=? WHERE job_dir=?", (job_id, key))
        if fields:
            cols = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(f"UPDATE applications SET {cols} WHERE job_dir = ?", (*fields.values(), key))
    if fields.get("state") in ("submitted", "confirmed"):  # step 15: freeze what was sent (resume hash, scores, role family, skills)
        try:
            from jobagent.tools import outcome_stats
            outcome_stats.snapshot(conn, job_dir)
        except Exception:
            pass


def guard(conn, job_dir: Path) -> dict | None:
    """Why this application must not be filled and sent again, or None when a (new) attempt may start."""
    row = conn.execute("SELECT state, submitted_at, confirmed_at FROM applications WHERE job_dir = ?", (db.job_key(job_dir),)).fetchone()
    if row is None or row["state"] not in SENT:
        return None
    if row["state"] in ("submitted", "confirmed"):
        return {"state": "already_submitted", "application": row["state"],
                "message": f"already {row['state']} ({row['confirmed_at'] or row['submitted_at']}): nothing was clicked"}
    return {"state": "submission_uncertain", "application": row["state"],
            "message": f"an earlier attempt may have been sent and is not settled ({row['state']}): run "
                       f"`jobagent confirm {job_dir}` to look for the confirmation mail, or `jobagent confirm {job_dir} "
                       "--attest sent|not-sent` after checking the portal. Nothing was clicked."}


# --- the attempt: frozen payload, written before the click, settled by evidence ----------------------------------------------

FILE_BYTES_JS = """async (sel) => {
  const el = document.querySelector(sel);
  if (!el || !el.files) return null;
  const out = [];
  for (const f of el.files) {
    const buf = new Uint8Array(await f.arrayBuffer());
    let bin = '';
    for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
    out.push({name: f.name, size: f.size, b64: btoa(bin)});
  }
  return out;
}"""


def _attachments(page: Page, view: forms.PageView, records: list[dict]) -> list[dict]:
    """What each upload control holds RIGHT NOW, read from the browser: the name, size and SHA-256 of the bytes of every
    file in its native input. A control whose input is gone needs a live captured-dispatch proof tied to the same widget.
    Otherwise its file name is recorded as `unverified`, never attributed another file's bytes."""
    import base64
    out = []
    for f in view.fields:
        if f.kind == "file":
            frame = view.frames.get(f.frame)
            held = None
            if frame is not None:
                try:
                    held = frame.evaluate(FILE_BYTES_JS, f'[data-jf="{f.id}"]')
                except Exception:
                    held = None
            if held is None:
                # the bytes cannot be read, but WHAT THE WIDGET SHOWS can: its file name (inside its own boundary) is part of what
                # was approved, so replacing the file under it changes the approval hash even though its bytes are opaque (F04)
                try:
                    shown = forms.attachment_evidence(page, view, f, [], [], f.label)
                except Exception:
                    shown = ""
                out.append({"frame": f.frame, "slot": f.slot, "label": f.label, "files": None, "unverified": "bytes unreadable",
                            "observed": shown or (f.value or "")})
            else:
                out.append({"frame": f.frame, "slot": f.slot, "label": f.label,
                            "files": [{"name": h["name"], "size": h["size"],
                                       "sha256": hashlib.sha256(base64.b64decode(h["b64"])).hexdigest()} for h in held]})
        elif forms.DOC_NAME.search(f.label) or forms.DOC_NAME.search(f.value or ""):
            out.append({"frame": f.frame, "slot": f.slot, "label": f.label, "files": None,
                        "unverified": f"only the name is shown: {(forms.DOC_NAME.search(f.label) or forms.DOC_NAME.search(f.value)).group(0)}"})
    for w in forms.widget_uploads(page, view, records):   # uploads with no native input left: they stay in the inventory
        out.append({"frame": None, "slot": None, "label": w["label"], "files": None, "observed": w["observed"] or "",
                    "unverified": f"only the name is shown: {w['observed']}" if w["observed"] else "no attachment shown"})
    # The opt-in filler can retain the ACTUAL dispatched File after an uploader
    # clears its input. Revalidate its bytes and owned-widget acknowledgement
    # now; never substitute the job-folder file or trust a recorded hash alone.
    for proof in getattr(page,'_jobagent_upload_proofs',[]):
        bound=[r for r in records if r.get('identity')==getattr(proof,'identity',None)
               and (r.get('field_key') or [None])[0]==getattr(proof,'frame',None)
               and r.get('held_file_sha256')==proof.expected and r.get('verified')]
        if len(bound)!=1 or not proof.verify():continue
        matches=[a for a in out if a['label']==bound[0]['label']]
        if len(matches)!=1 or matches[0].get('files'):continue
        matches[0].update(files=[{'name':proof.name,'size':proof.size,'sha256':proof.expected,
                                 'verification':proof.method}])
        matches[0].pop('unverified',None)
    return sorted(out, key=lambda d: (-1 if d["frame"] is None else d["frame"], d["slot"] or 0, d["label"]))


_RECORD_KEYS = ("page", "section", "kind", "label", "required", "answer", "filled", "readback", "verified", "outcome", "source",
                "flag", "error", "frame", "slot", "identity", "field_key", "fact_id", "upload_verification", "held_file_sha256")


def _reviewed(records: list[dict]) -> list[dict]:
    """Every answer collected across all pages of the application, in order, exactly as the review packet shows them: one
    entry per record (repeated labels on different pages or in different sections stay separate), with where it was read
    and how it was verified. A password is never written down."""
    out = []
    for i, e in enumerate(records):
        item = {"i": i, **{k: e.get(k) for k in _RECORD_KEYS if k in e}}
        item["provenance"] = f"filled and read back on page {e.get('page', '?')}, not on the final screen"
        if e.get("kind") == "password":
            for k in ("answer", "filled", "readback"):
                if item.get(k):
                    item[k] = "[redacted]"
        out.append(item)
    return out


def freeze_payload(page: Page, view: forms.PageView, records: list[dict] = (), job_dir: Path | None = None) -> tuple[str, dict]:
    """What is about to be sent, as the candidate reviewed it:
      * `reviewed`: every answer from every page (the accumulated field records), with page, section, value, read-back,
        verification outcome and provenance. Earlier pages are not on the final screen but are part of the application;
      * `screen`: every field the final screen shows now, with its value as read now;
      * `attachments`: for every upload control the hash of the bytes the browser holds in it (not of a same-named
        job-folder file), or a live captured-dispatch proof with current widget acknowledgement. Without either,
        an upload widget with no native input is listed with the file name it shows and no hash.
    Returns (sha256 of all three, payload). Any changed answer, value, file name or byte changes the hash."""
    screen = sorted(({"frame": f.frame, "kind": f.kind, "label": f.label, "value": "[redacted]" if f.kind == "password" else f.value,
                      "provenance": "read on the final screen"} for f in view.fields),
                    key=lambda d: (d["frame"], d["kind"], d["label"], d["value"]))
    attachments = _attachments(page, view, list(records))
    from jobagent.tools import ats_result
    roles = ats_result.attachment_roles(attachments)
    payload = {"reviewed": _reviewed(list(records)), "screen": screen, "attachments": attachments,
               # what was submitted, from the controls themselves (F06): the learning snapshot uses these, never the disk files
               "identity": {"resume_sha256": roles["resume"], "cover_letter_sha256": roles["cover_letter"],
                            "job_fingerprint": ats_result.identity(job_dir)[1] if job_dir is not None else None}}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(), payload


class AttemptRefused(RuntimeError):
    """An attempt cannot start: the application was sent, may have been, or another attempt is open."""


def begin_attempt(conn, job_dir: Path, sha: str, payload: dict, approved_at: str) -> int:
    """Reserve the right to click, atomically: inside one write transaction, refuse unless the application is in an unsent
    state with no open attempt, then insert the attempt and move the application to `submission_in_progress`. Returns the
    attempt number; raises AttemptRefused for a stale approval. The payload file belongs to the winning reservation."""
    key = db.job_key(job_dir)
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")                  # takes the write lock: a second reservation waits and then sees this one
    try:
        row = conn.execute("SELECT id, state FROM applications WHERE job_dir = ?", (key,)).fetchone()
        if row is None or row["state"] in SENT:
            raise AttemptRefused(f"the application is {row['state'] if row else 'unknown'}: no new attempt")
        if conn.execute("SELECT 1 FROM submission_attempts WHERE job_dir = ? AND state IN ('in_progress', 'unknown', 'submitted', "
                        "'confirmed')", (key,)).fetchone():
            raise AttemptRefused("an earlier attempt is open or went through")
        n = conn.execute("SELECT COALESCE(MAX(attempt_no), 0) + 1 FROM submission_attempts WHERE job_dir = ?", (key,)).fetchone()[0]
        path = Path(job_dir) / "application" / f"submission-{n}.json"
        conn.execute("INSERT INTO submission_attempts (application_id, job_dir, attempt_no, state, payload_sha, payload_path, approved_at, "
                     "started_at) VALUES (?, ?, ?, 'in_progress', ?, ?, ?, ?)", (row['id'], key, n, sha, str(path), approved_at, _now()))
        conn.execute("UPDATE applications SET state = 'submission_in_progress', submitted_at = NULL, confirmed_at = NULL, "
                     "confirmation_id = NULL, confirmation = NULL, resume_sha = NULL, ats_average = NULL, ats_scores = NULL, "
                     "role_id = NULL, keywords = NULL, resume_pdf_sha256 = NULL, cover_letter_sha256 = NULL, jd_fingerprint = NULL, "
                     "snapshot_status = NULL, ats_sites = NULL, snapshot_detail = NULL WHERE job_dir = ?", (key,))    # nothing of an earlier attempt carries over
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"attempt": n, "payload_sha": sha, "approved_at": approved_at, **payload}, indent=2))
    except BaseException as e:                       # nothing was clicked: release the reservation
        settle_attempt(conn, job_dir, n, "not_sent", notes=f"the payload could not be saved: {type(e).__name__}")
        conn.execute("UPDATE applications SET state = 'failed' WHERE job_dir = ?", (key,))
        conn.commit()
        raise
    return n


def mark_action(conn, job_dir: Path, n: int) -> str:
    """Committed immediately before the click is started: from here the click may have gone out."""
    at = _now()
    with conn:
        conn.execute("UPDATE submission_attempts SET action_at = ? WHERE job_dir = ? AND attempt_no = ?", (at, db.job_key(job_dir), n))
    return at


def settle_attempt(conn, job_dir: Path, n: int, state: str, evidence: str = "", notes: str = "", clicked_at: str | None = None) -> None:
    """Close the attempt with the outcome the evidence supports. `clicked_at` is the time captured at the click itself
    (never the time of settlement); None leaves it unknown."""
    with conn:
        conn.execute("UPDATE submission_attempts SET state = ?, evidence = ?, notes = ?, finished_at = ?, "
                     "clicked_at = COALESCE(clicked_at, ?) WHERE job_dir = ? AND attempt_no = ?",
                     (state, evidence[:500], notes[:500], _now(), clicked_at, db.job_key(job_dir), n))


def _mark_job(conn, jd: dict, status: str, note: str = "") -> None:
    conn.execute("UPDATE jobs SET status = ?, notes = COALESCE(NULLIF(?, ''), notes), updated_at = ? WHERE url = ?",
                 (status, note, _now(), jd.get("source_url") or ""))
    conn.commit()


def ats_line(conn, job_dir: Path) -> str:
    """The review packet's ATS line: the authoritative result for this exact resume PDF and job (tools/ats_result)."""
    from jobagent.tools import ats_result
    return ats_result.line(job_dir if job_dir.is_absolute() or (job_dir / "resume.pdf").exists() else Path("/app") / job_dir, conn)


def readiness_text(result: dict) -> str:
    """Reaching the review screen is not being ready: say which, and why not."""
    if result.get("ready_to_submit"):
        return "- Ready to submit: **yes**"
    return "- Ready to submit: **NOT ready**" + "".join(f"\n  - {b}" for b in result.get("blockers", []))


def live_blockers(page: Page, view: forms.PageView, records: list[dict] = (), job_dir: Path | None = None) -> list[str]:
    """Checked again at approval, on the page as it is now (the candidate may have fixed things in noVNC): required
    fields still empty, required uploads with no file attached, and errors the page shows. Uploads are judged from
    the page itself (`forms.upload_blockers`), not from the earlier fill record: a manual upload clears a failed one,
    a removed file raises a verified one."""
    out = [f"the form was not fully read: {p}" for p in view.coverage.problems()]            # F17: unread parts cannot be approved
    out += [f"{f.label[:60]}: required and empty" for f in view.fields if f.required and not f.value and f.kind != "file"]
    known = [p.name for p in (job_dir.glob("*.pdf") if job_dir and job_dir.exists() else [])]
    out += forms.upload_blockers(page, view, list(records), known)
    return out + [f"the page shows an error: {e[:80]}" for e in view.errors[:3]]


def documents_section(job_dir: Path) -> list[str]:
    """What the generation stage found about the resume and cover letter: validity, the ATS target, findings and the
    figures the tailor estimated, which the candidate confirms before approving."""
    from jobagent.tools import documents
    rec = documents.load(job_dir)
    if rec is None:
        return ["## Documents", "", "- No documents.json: these documents have no recorded validation.", ""]
    ats = rec["ats"]
    lines = ["## Documents", "",
             f"- Validity: **{'valid' if rec['valid'] else 'INVALID'}** (resume {rec['resume']['pages']} page(s), cover letter "
             f"{rec['cover_letter']['pages']} page(s)); built {rec['built_at']}",
             f"- ATS: **{ats['status']}**, average {ats['average']} against the target {ats['target']} (policy: {ats['policy']})"]
    lines += [f"- BLOCKER: {b}" for b in rec["blockers"]] + [f"- Warning: {w}" for w in rec["warnings"]]
    lines += [f"- Cover letter finding: {i}" for i in rec["cover_letter"]["issues"]]
    if rec["estimates"]:
        lines += ["", "### Figures the tailor estimated: confirm each is true for you before approving", "",
                  "| Estimated | Bullet |", "|---|---|"]
        lines += [f"| {', '.join(e['estimated'])} | {e['bullet'][:110].replace('|', '/')} |" for e in rec["estimates"]]
    return lines + [""]


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
             readiness_text(result),
             f"- Screenshots: {', '.join(p['screenshot'] for p in result.get('pages', []))} (in {job_dir / 'application'})",
             "- The form is open on its review screen in noVNC (http://localhost:6080): fix anything there first.", ""]
    if flagged:
        lines += ["## Look at these first", "", "| Field | Value | Note |", "|---|---|---|"]
        lines += [f"| {cell(e['label'][:70])} | {cell(e.get('readback', ''))} | "
                  f"{cell(e.get('flag') or e.get('error') or 'not confirmed on the page')} |" for e in flagged]
        lines.append("")
    lines += documents_section(job_dir)
    try:
        from jobagent.tools import skillgraph
        lines += skillgraph.review_lines(job_dir)
    except Exception:
        pass
    lines += ["## Every field", "", "| Page | Field | Value | Source |", "|---|---|---|---|"]
    lines += [f"| {e['page']} | {cell(e['label'][:70])} | {cell(e.get('readback', ''))} | {e.get('source', '')} |"
              for e in fields]
    return "\n".join(lines) + "\n"


SUBMIT_FIRST = [re.compile(r"(?i)^\s*submit( (my |your )?application)?\s*$"),
                re.compile(r"(?i)submit|send application|^\s*finish\b|complete (my |your )?application|^\s*apply\s*$")]


def click_submit(page: Page, view: forms.PageView, approval: forms.SubmitApproval) -> str | None:
    """The one place the agent clicks Submit. It needs the `SubmitApproval` made from the candidate's 'approve' to this
    application's review prompt; startup, the form filler and the queue have no way to make one."""
    if not isinstance(approval, forms.SubmitApproval):
        raise TypeError("sending an application needs the candidate's approval")
    from jobagent.tools import challenge
    if challenge.guard(page):
        view = forms.read_page(page)
    for pat in SUBMIT_FIRST:
        if (t := forms.click_final_submit(page, view, pat, approval)):
            return t
    if forms.click_submit_input(page, approval):  # an unlabelled submit input (shown once a human check is done)
        return "the form's submit button"
    return None


def page_text(page: Page) -> str:
    try:
        return " ".join(f.locator("body").inner_text(timeout=5000) for f in forms.frames_of(page))
    except Exception:
        return ""


def submitted_page(page: Page, before: str, shot: Path, timeout_s: float = 60) -> tuple[bool, str, list[str]]:
    """(confirmed, the thank-you sentence, errors) after Submit. Only a thank-you / received sentence that was NOT on
    the page before the click counts; it is screenshotted the moment it shows."""
    deadline = datetime.now() + timedelta(seconds=timeout_s)
    errors: list[str] = []
    while datetime.now() < deadline:
        page.wait_for_timeout(3000)
        text = page_text(page)
        new = [m.group(0) for m in CONFIRMED_PAGE.finditer(text) if m.group(0) not in before]
        if new:
            screenshot(page, str(shot))
            line = next((ln.strip() for ln in text.splitlines() if new[0] in ln), new[0])
            return True, line[:300], []
        errors = forms.read_page(page).errors
    return False, "", errors


@dataclass
class ApplicationMatch:
    """'This mail is about this application'. Two separate questions, in this order (F09):
      1. WHO sent it: the sender's registrable domain, parsed from the address and compared exactly, must be one of the
         company's verified own domains (`own`) or the portal / ATS sender family of the apply URL (`allowed`). A display
         name, a subject or a body never establishes that: `Acme Recruiting <x@unrelated.example>` is not Acme.
      2. WHAT it is about: only then do the company and role words (display name, subject, body) decide.
    One definition shared by the confirmation wait and the outcome sync (step 15)."""
    allowed: set[str]      # sender domains: the portal / ATS family of the apply URL plus the company's own
    company: str           # lower-cased
    words: list[str]       # the company and the first words of the title
    own: frozenset = frozenset()   # the company's own VERIFIED sender domains (not the shared ATS ones)

    def sender_ok(self, mail) -> bool:
        from jobagent.tools.inbox import sender_domain
        return sender_domain(mail) in self.allowed

    def plausible(self, mail) -> bool:
        """From the HEADERS alone: may this message be read? A company's own domain, yes; a shared ATS sender (Greenhouse,
        Lever, Workday...) writes for many companies, so it must name the company in its display name or subject."""
        import email.utils

        from jobagent.tools.inbox import sender_domain
        if not self.sender_ok(mail):
            return False
        if sender_domain(mail) in self.own:
            return True
        head = f"{email.utils.parseaddr(mail.sender)[0]} {mail.subject}".lower()
        first = self.company.split()[0] if self.company else ""
        return bool(self.company and (self.company in head or (len(first) >= 3 and first in head)))

    def __call__(self, mail) -> bool:
        import email.utils

        from jobagent.tools.inbox import sender_domain
        if not self.sender_ok(mail):
            return False                                  # authority first: a company-looking name cannot stand in for it
        # the sender's display name counts for WHAT the mail is about: ATS mail shares its sender address and names the
        # company there
        about = f"{email.utils.parseaddr(mail.sender)[0]} {mail.subject} {mail.text[:3000]}".lower()
        if sender_domain(mail) in self.own:
            return any(w in about for w in self.words)
        # a shared ATS sender writes for many companies: the company must be named; a title word like "engineer" alone
        # would link another company's mail to this application
        first = self.company.split()[0] if self.company else ""
        return bool(self.company and (self.company in about or (len(first) >= 3 and first in about)))


def company_domains(company: str, jd: dict) -> set[str]:
    """The company's own sender domains from VERIFIED metadata only: domains the job record lists explicitly, the official
    site and official pages the company research found (state/companies/<slug>.json), and the careers site discovery
    resolved (<slug>.careers.json) when it is not a shared job board. A guess from the company's name is not evidence."""
    from jobagent.config import get_settings
    from jobagent.tools import research
    from jobagent.tools.jobqueue import slug as q_slug

    shared = {d for fam in db.ATS_SENDER_FAMILIES for d in fam}
    found = {db.registrable_domain(d) for d in (jd.get("company_domains") or []) if d}
    root = get_settings().state_dir / "companies"
    try:
        prof = json.loads((root / f"{research.slug(company)}.json").read_text())
        if prof.get("website"):
            found.add(db.registrable_domain(prof["website"]))
        for src in (prof.get("sources") or {}).values():
            if src.get("kind") == "official" and src.get("url"):
                found.add(db.registrable_domain(src["url"]))
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    try:
        careers = json.loads((root / f"{q_slug(company)}.careers.json").read_text())
        board = (careers.get("board") or {}).get("url") or ""
        if careers.get("url") and db.registrable_domain(careers["url"]) != db.registrable_domain(board or "x.invalid"):
            found.add(db.registrable_domain(careers["url"]))
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    from jobagent.tools import psl
    # never a shared ATS domain, and never a public suffix itself (`co.za` would admit every .co.za sender)
    return {d for d in found if d and d not in shared and not psl.is_public_suffix(d)}


def application_match(conn, jd: dict, register: bool = True) -> ApplicationMatch:
    """Who may write about this application. `register=False` builds the same matcher without recording the portal as an
    agent domain (a dry run must leave the database as it found it); the default records it, as the submit step needs."""
    url = jd.get("apply_url") or jd.get("source_url") or ""
    allowed = db.record_agent_domain(conn, url, "portal") if register else db.agent_domain_family(url, "portal")[0]
    company = (jd.get("company") or "").lower()
    own = company_domains(jd.get("company") or "", jd) if company else set()
    allowed |= own
    words = [w for w in [company, *(jd.get("title") or "").lower().split()[:3]] if len(w) > 3]
    return ApplicationMatch(allowed, company, words, frozenset(own))


def wait_for_confirmation(inbox, conn, jd: dict, since: datetime, timeout_s: float = 900):
    """The confirmation mail: from the company's or its ATS's exact sender domain, sent after the submission, about this
    application (names the company or the role). Candidates are judged on their headers first; the body of one that is
    not plausible is never retrieved. None when it hasn't come within the timeout."""
    about_it = application_match(conn, jd)
    criteria = f'SINCE "{(since - timedelta(days=1)).strftime("%d-%b-%Y")}"'

    def in_window(m) -> bool:
        return m.date is not None and m.date >= since - timedelta(minutes=2)

    def match(m) -> bool:
        return bool(in_window(m) and about_it(m) and CONFIRMATION_MAIL.search(f"{m.subject} {m.text[:3000]}".lower()))

    try:
        return inbox.wait_for(match, criteria, timeout_s=timeout_s, poll_s=20,
                              header_filter=lambda h: in_window(h) and about_it.plausible(h))
    except TimeoutError:
        return None


def make_at_review(job_dir: Path, jd: dict, hitl, inbox, conn, log, ask_s: float = 3600, mail_s: float = 900):
    """The step 13 hook for apply.run: review packet, the candidate's decision, Submit, confirmation."""
    def at_review(page: Page, view: forms.PageView, result: dict) -> dict:
        if stop := guard(conn, job_dir):              # never fill-and-send what was already sent, or may have been
            log(f"submit: {stop['message']}")
            return {"state": stop["state"], "message": stop["message"]}
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
                f"{len(result.get('fields', []))} fields confirmed, {flagged} to look at). "
                + (f"The form has a human check ({result['human_check']!r}): tick it yourself in noVNC first. "
                   if result.get("human_check") else "")
                + (f"NOT READY: {'; '.join(result.get('blockers', [])[:4])[:400]}. Fix these in noVNC first (the page is "
                   "checked again when you approve). " if not result.get("ready_to_submit") else "")
                + "Fix anything in noVNC if needed, then answer 'approve' to SUBMIT it or 'skip' to leave it "
                "unsubmitted.",
                timeout_s=ask_s).strip().lower()
        except HitlTimeout:
            answer = ""
        if answer != "approve":
            record(conn, job_dir, jd, state="skipped", decided_at=_now(),
                   notes="no answer in time" if not answer else f"candidate answered {answer!r}")
            log(f"submit: not submitted ({answer or 'no answer'})")
            return {"state": "skipped", "answer": answer}
        view = forms.read_page(page)  # the candidate may have changed things in noVNC
        if live := live_blockers(page, view, result.get("fields", []), job_dir):
            # approving does not override missing required answers or uploads: nothing is sent until the page itself is complete
            record(conn, job_dir, jd, state="filled", notes="approve refused: " + "; ".join(live)[:300])
            log(f"submit: not sent: {live[:4]}")
            result.update(ready_to_submit=False, blockers=live)
            return {"state": "not_ready", "blockers": live}
        # the page is complete now (the candidate may have fixed what the fill could not): what is sent is described by this
        earlier = [b for b in result.get("blockers", []) if b.startswith("page ") and "not fully read" in b]
        if earlier:               # F17: a page earlier in the wizard that could not be read stays a blocker; approving does not clear it
            record(conn, job_dir, jd, state="filled", notes="approve refused: " + "; ".join(earlier)[:300])
            log(f"submit: not sent: {earlier[:3]}")
            result.update(ready_to_submit=False, blockers=earlier)
            return {"state": "not_ready", "blockers": earlier}
        result.update(ready_to_submit=True, blockers=[], readiness_rechecked_at_approval=_now())
        approved_at = _now()
        if stop := guard(conn, job_dir):              # the wait for the answer was long: another review may have sent it since
            log(f"submit: {stop['message']}")
            return {"state": stop["state"], "message": stop["message"]}
        record(conn, job_dir, jd, state="approved", decided_at=approved_at)
        # the approval covers exactly this page and these files: freeze them, and check once more right before the click
        records = result.get("fields", [])
        sha, payload = freeze_payload(page, view, records, job_dir)
        view = forms.read_page(page)
        if freeze_payload(page, view, records, job_dir)[0] != sha:
            record(conn, job_dir, jd, state="filled", notes="the page changed after approval; approve again")
            log("submit: not sent: the page or an attached file changed after you approved")
            return {"state": "not_ready", "blockers": ["the page or an attached file changed after you approved it; review it and approve again"]}

        def still_approved() -> None:                 # run by the click helpers right before the click is dispatched
            if freeze_payload(page, forms.read_page(page), records, job_dir)[0] != sha:
                raise forms.ApprovalInvalidated("the page or an attached file changed after you approved it")
        # the candidate's 'approve' for THIS application, as the object the sending functions require
        approval = forms.SubmitApproval(str(job_dir), f"approve@{approved_at}", _issuer=forms._ISSUER, payload_sha=sha,
                                        verify=still_approved)
        before = page_text(page)
        try:
            n = begin_attempt(conn, job_dir, sha, payload, approved_at)   # atomic, committed BEFORE the click
        except AttemptRefused as why:
            log(f"submit: not sent: {why}")
            stop = guard(conn, job_dir) or {"state": "submission_uncertain", "message": str(why)}
            return {"state": stop["state"], "message": stop["message"]}
        mark_action(conn, job_dir, n)
        try:
            clicked = click_submit(page, view, approval)
        except forms.ApprovalInvalidated as why:      # proven before dispatch: nothing was sent, the approval is void
            settle_attempt(conn, job_dir, n, "not_sent", notes=f"approval invalidated before the click: {why}")
            with conn:
                conn.execute("UPDATE submission_attempts SET action_at = NULL WHERE job_dir = ? AND attempt_no = ?", (db.job_key(job_dir), n))
            record(conn, job_dir, jd, via_attempt=True, state="failed", notes=f"approval invalidated before the click: {why}")
            log(f"submit: not sent: {why}")
            return {"state": "not_ready", "blockers": [f"{why}; review it and approve again"]}
        except BaseException as e:                    # the click may or may not have gone out: never "not sent"
            settle_attempt(conn, job_dir, n, "unknown", notes=f"the click ended with {type(e).__name__}: {str(e)[:200]}")
            record(conn, job_dir, jd, state="submission_unknown", notes="the click did not finish cleanly; not proven sent or unsent")
            _mark_job(conn, jd, "needs_attention", "submission_unknown")
            if not isinstance(e, Exception):
                raise
            log(f"submit: the click did not finish cleanly ({type(e).__name__}); treated as possibly sent")
            return {"state": "submission_unknown", "reason": f"the click ended with {type(e).__name__}"}
        if not clicked:                               # no control was found and none was clicked
            settle_attempt(conn, job_dir, n, "not_sent", notes=f"no Submit button; buttons: {view.buttons[:12]}")
            record(conn, job_dir, jd, via_attempt=True, state="failed", notes=f"no Submit button; buttons: {view.buttons[:12]}")
            log(f"submit: no Submit button found ({view.buttons[:12]})")
            return {"state": "failed", "reason": "no Submit button"}
        since = datetime.now(UTC)
        clicked_at = _now()                           # captured at the click; every later record keeps this time
        with conn:
            conn.execute("UPDATE submission_attempts SET clicked_at = ? WHERE job_dir = ? AND attempt_no = ?", (clicked_at, db.job_key(job_dir), n))
        log(f"submit: clicked {clicked!r}")
        try:
            ok, text, errors = submitted_page(page, before, out / "submitted.png")
            page.wait_for_timeout(5000)
            screenshot(page, str(out / "after-submit.png"))
        except BaseException as e:
            settle_attempt(conn, job_dir, n, "unknown", notes=f"after the click: {type(e).__name__}", clicked_at=clicked_at)
            record(conn, job_dir, jd, state="submission_unknown", notes="clicked; the result was not read")
            _mark_job(conn, jd, "needs_attention", "submission_unknown")
            raise
        if not ok and errors:
            settle_attempt(conn, job_dir, n, "refused", evidence="; ".join(errors), clicked_at=clicked_at)
            record(conn, job_dir, jd, via_attempt=True, state="failed", notes="the site refused: " + "; ".join(errors)[:400])
            log(f"submit: the site refused it: {errors[:4]}")
            return {"state": "failed", "errors": errors}
        if ok:
            settle_attempt(conn, job_dir, n, "submitted", evidence=f"page said: {text}", clicked_at=clicked_at)
            record(conn, job_dir, jd, state="submitted", submitted_at=clicked_at, notes=f"page said: {text}")
            _mark_job(conn, jd, "applied")
            log("submit: submitted; watching the inbox for the confirmation")
        else:
            # clicked, but neither a thank-you page nor an error: receipt is NOT proven. Never record this as submitted.
            settle_attempt(conn, job_dir, n, "unknown", notes="Submit clicked, no thank-you page and no error seen", clicked_at=clicked_at)
            record(conn, job_dir, jd, state="submission_unknown", notes="Submit clicked, no thank-you page seen: not proven received")
            _mark_job(conn, jd, "needs_attention", "submission_unknown")
            log("submit: clicked, but nothing proves it was received; looking for the confirmation mail")
        mail = wait_for_confirmation(inbox, conn, jd, since, timeout_s=mail_s)
        if mail:
            settle_attempt(conn, job_dir, n, "confirmed", evidence=f"{mail.sender} | {mail.subject}", clicked_at=clicked_at)
            record(conn, job_dir, jd, state="confirmed", submitted_at=clicked_at, confirmed_at=_now(),
                   confirmation_id=mail.message_id, confirmation=f"{mail.sender} | {mail.subject}")
            _mark_job(conn, jd, "applied")
            log(f"submit: confirmation mail: {mail.subject!r} from {mail.sender}")
            return {"state": "confirmed", "page": ok, "mail": mail.subject}
        if not ok:
            log(f"submit: still unproven; settle it with `jobagent confirm {job_dir}`")
            return {"state": "submission_unknown", "page": False}
        log("submit: no confirmation mail yet; check later with `jobagent confirm`")
        return {"state": "submitted", "page": ok}
    return at_review


def confirm(job_dir: Path, inbox, conn, timeout_s: float = 60, attest: str | None = None) -> str:
    """Look for a sent application's confirmation mail again. For an attempt whose outcome is unknown (a crash around the
    click, or no thank-you page), this is the reconciliation: a matching confirmation mail settles it as confirmed; the
    candidate, after looking at the portal, can settle it by hand with attest='sent' or 'not-sent' ('not-sent' is the
    only way a new attempt can start)."""
    row = conn.execute("SELECT * FROM applications WHERE job_dir = ?", (db.job_key(job_dir),)).fetchone()
    if row is None or row["state"] not in SENT:
        return f"not submitted ({row['state'] if row else 'no record'})"
    if row["state"] == "confirmed":
        return f"confirmed: {row['confirmation']}"
    jd = json.loads((job_dir / "jd.json").read_text())
    att = conn.execute("SELECT * FROM submission_attempts WHERE job_dir = ? ORDER BY attempt_no DESC LIMIT 1",
                       (db.job_key(job_dir),)).fetchone()
    if row["state"] in UNCERTAIN and att is not None:
        if attest == "not-sent":
            settle_attempt(conn, job_dir, att["attempt_no"], "not_sent", evidence="the candidate checked the portal: not sent")
            with conn:                                # the one deliberate way back: a new attempt may now start
                conn.execute("UPDATE applications SET state = 'failed', notes = ? WHERE job_dir = ?",
                             ("attempt settled by the candidate as not sent", db.job_key(job_dir)))
            _mark_job(conn, jd, "queued", "")
            return "settled as not sent: a new attempt may start"
        if attest == "sent":
            settle_attempt(conn, job_dir, att["attempt_no"], "submitted", evidence="the candidate checked the portal: received")
            sent_at = att["clicked_at"] or att["action_at"] or att["started_at"]      # the action time, or the earliest estimate
            record(conn, job_dir, jd, state="submitted", submitted_at=sent_at, notes="settled by the candidate: portal shows it")
            _mark_job(conn, jd, "applied")
            return "settled as submitted (candidate's word); watching for the confirmation mail goes on"
        since = datetime.strptime(att["started_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    else:
        since = datetime.strptime(row["submitted_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    mail = wait_for_confirmation(inbox, conn, jd, since, timeout_s=timeout_s)
    if mail is None:
        return ("no confirmation mail yet" if row["state"] == "submitted" else
                "still unproven: no confirmation mail. Check the portal, then `jobagent confirm "
                f"{job_dir} --attest sent` or `--attest not-sent`")
    if att is not None:
        settle_attempt(conn, job_dir, att["attempt_no"], "confirmed", evidence=f"{mail.sender} | {mail.subject}")
    record(conn, job_dir, jd, state="confirmed", submitted_at=row["submitted_at"] or (
               (att["clicked_at"] or att["action_at"] or att["started_at"]) if att else _now()),
           confirmed_at=_now(), confirmation_id=mail.message_id, confirmation=f"{mail.sender} | {mail.subject}")
    _mark_job(conn, jd, "applied")
    return f"confirmed: {mail.subject} ({mail.sender})"
