"""Agent Gmail inbox over IMAP/SMTP (app password), verification-mail parsing and cleanup.

Only verification mail (OTP / one-time code / login or verification link / password reset) is ever
trashed, and only after its use succeeded or when stale. Application-related mail is never trashed.
"""

import email
import email.header
import email.utils
import imaplib
import re
import smtplib
import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage, Message
from email.utils import parsedate_to_datetime
from html import unescape

from jobagent import db

IMAP_HOST, SMTP_HOST = "imap.gmail.com", "smtp.gmail.com"

_VERIFY_WORDS = re.compile(
    r"(?i)\b(verification|verify|one[- ]time|otp|passcode|security code|confirm (your )?(email|account)|"
    r"activate (your )?account|sign[- ]?in link|magic link|reset (your )?password|password reset|login code)\b"
)
# Mail that must never be trashed even if it contains a code or link.
_PROTECTED_WORDS = re.compile(
    r"(?i)\b(interview|offer letter|assessment|coding challenge|hackerrank|codesignal|unfortunately|"
    r"not moving forward|thank you for (applying|your application)|application (received|submitted|status)|"
    r"next steps|recruiter|schedule a call)\b"
)
_CODE = re.compile(r"(?<![\d#])(\d{4,8}|[A-Z0-9]{3}-[A-Z0-9]{3})(?![\d])")
_LINK = re.compile(r"https?://[^\s\"'<>)]+", re.I)
_LINK_HINT = re.compile(r"(?i)(verif|confirm|activat|token|magic|signin|sign-in|login|reset|validate|auth)")


@dataclass
class Mail:
    uid: str
    message_id: str
    sender: str
    subject: str
    date: datetime | None
    text: str
    thread_id: str = ""


@dataclass
class Verification:
    kind: str  # otp | link | password_reset
    code: str | None
    link: str | None
    confidence: float


def _body_text(msg: Message) -> str:
    parts = msg.walk() if msg.is_multipart() else [msg]
    plain, html = [], []
    for part in parts:
        ctype = part.get_content_type()
        if part.get_content_maintype() == "multipart" or part.get("Content-Disposition", "").startswith("attachment"):
            continue
        payload = part.get_payload(decode=True) or b""
        text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        (plain if ctype == "text/plain" else html if ctype == "text/html" else []).append(text)
    if plain:
        return "\n".join(plain)
    # Keep hrefs from HTML-only mail so verification links survive tag stripping.
    joined = "\n".join(html)
    joined = re.sub(r"(?is)<a\s[^>]*href=[\"']([^\"']+)[\"'][^>]*>", r" \1 ", joined)
    return unescape(re.sub(r"(?s)<[^>]+>", " ", joined))


def parse(uid: str, raw: bytes, thread_id: str = "") -> Mail:
    msg = email.message_from_bytes(raw)
    subject = str(email.header.make_header(email.header.decode_header(msg.get("Subject", ""))))
    try:
        date = parsedate_to_datetime(msg["Date"]) if msg["Date"] else None
    except (TypeError, ValueError):
        date = None
    return Mail(uid, msg.get("Message-ID", "").strip(), msg.get("From", ""), subject, date, _body_text(msg), thread_id)


SELF_TEST_TAG = "jobagent-selftest-"


def sender_domain(mail: Mail) -> str:
    return db.registrable_domain(email.utils.parseaddr(mail.sender)[1] or mail.sender)


def is_protected(mail: Mail) -> bool:
    return bool(_PROTECTED_WORDS.search(f"{mail.subject}\n{mail.text}"))


def classify_verification(mail: Mail) -> Verification | None:
    """Heuristic detector; returns None when the mail isn't verification mail (or is protected)."""
    blob = f"{mail.subject}\n{mail.text}"
    if is_protected(mail) or not _VERIFY_WORDS.search(blob):
        return None
    kind = "password_reset" if re.search(r"(?i)reset (your )?password|password reset", blob) else "otp"
    # Prefer a code near the keyword (codes in the subject are the most reliable).
    code = None
    for source in (mail.subject, mail.text):
        for m in _CODE.finditer(source):
            window = source[max(0, m.start() - 120): m.end() + 40]
            if _VERIFY_WORDS.search(window) or re.search(r"(?i)\bcode\b", window):
                code = m.group(1)
                break
        if code:
            break
    links = [l.rstrip(".,;") for l in _LINK.findall(mail.text) if _LINK_HINT.search(l)]
    link = links[0] if links else None
    if not code and not link:
        return None
    if link and not code and kind == "otp":
        kind = "link"
    confidence = 0.9 if code and re.search(r"(?i)code|otp|passcode", blob) else 0.8
    return Verification(kind, code, link, confidence)


class Inbox:
    def __init__(self, address: str, app_password: str, conn: sqlite3.Connection | None = None):
        self.address, self._password = address, app_password.replace(" ", "")
        self.conn = conn
        self._imap: imaplib.IMAP4_SSL | None = None
        self._trash: str | None = None

    # --- connection ---------------------------------------------------------------
    def imap(self) -> imaplib.IMAP4_SSL:
        if self._imap is None:
            self._imap = imaplib.IMAP4_SSL(IMAP_HOST, timeout=60)
            self._imap.login(self.address, self._password)
        return self._imap

    def close(self) -> None:
        if self._imap is not None:
            try:
                self._imap.logout()
            finally:
                self._imap = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # --- ownership (the inbox is shared with the user's own sign-ups) -------------
    def is_agent_mail(self, mail: Mail) -> bool:
        """Mail from a site the agent itself used, or the agent's own tagged self-test mail."""
        if SELF_TEST_TAG in mail.subject:
            return True
        if self.conn is None:
            return False
        return sender_domain(mail) in db.agent_domains(self.conn)

    def trash_folder(self) -> str:
        if self._trash is None:
            _, rows = self.imap().list()
            for row in rows or []:
                line = row.decode(errors="replace")
                if "\\Trash" in line:
                    self._trash = line.rsplit(' "/" ', 1)[-1].strip()
                    break
            else:
                self._trash = '"[Gmail]/Trash"'
        return self._trash

    # --- reading ----------------------------------------------------------------
    def search(self, criteria: str = "ALL", folder: str = "INBOX") -> list[str]:
        imap = self.imap()
        imap.select(folder)
        typ, data = imap.uid("SEARCH", None, criteria)
        return data[0].decode().split() if typ == "OK" and data and data[0] else []

    def fetch(self, uid: str, folder: str = "INBOX") -> Mail:
        imap = self.imap()
        imap.select(folder)
        typ, data = imap.uid("FETCH", uid, "(X-GM-THRID BODY.PEEK[])")
        if typ != "OK" or not data or data[0] is None:
            raise LookupError(f"message uid {uid} not found in {folder}")
        meta, raw = data[0][0].decode(errors="replace"), data[0][1]
        thrid = re.search(r"X-GM-THRID (\d+)", meta)
        return parse(uid, raw, thrid.group(1) if thrid else "")

    def wait_for(self, predicate, criteria: str = "ALL", timeout_s: float = 180, poll_s: float = 5) -> Mail:
        """Poll the inbox until a message satisfies `predicate` (newest first)."""
        deadline = time.monotonic() + timeout_s
        seen: set[str] = set()
        while True:
            for uid in reversed(self.search(criteria)):
                if uid in seen:
                    continue
                seen.add(uid)
                mail = self.fetch(uid)
                if predicate(mail):
                    return mail
            if time.monotonic() > deadline:
                raise TimeoutError(f"no matching mail within {timeout_s}s")
            time.sleep(poll_s)
            seen.clear()  # re-check: Gmail may deliver late into an existing UID range

    def wait_for_verification(self, site: str, since: datetime, kind: str = "portal",
                              timeout_s: float = 300) -> tuple[Mail, Verification]:
        """Wait for a verification mail from `site` (URL or domain) sent after `since` (the agent's action).

        Both are required: the inbox also receives the user's own sign-up codes, which must never be used.
        """
        allowed = db.record_agent_domain(self.conn, site, kind) if self.conn is not None else {db.registrable_domain(site)}
        criteria = f'SINCE "{since.strftime("%d-%b-%Y")}"'
        found: dict[str, Verification] = {}

        def match(m: Mail) -> bool:
            if m.date is None or m.date < since - timedelta(seconds=30):
                return False
            if sender_domain(m) not in allowed:
                return False
            v = classify_verification(m)
            if v:
                found[m.uid] = v
            return v is not None

        mail = self.wait_for(match, criteria, timeout_s)
        return mail, found[mail.uid]

    # --- sending ------------------------------------------------------------------
    def send(self, to: str, subject: str, body: str) -> str:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.address, to, subject
        msg.set_content(body)
        with smtplib.SMTP_SSL(SMTP_HOST, 465, timeout=60) as smtp:
            smtp.login(self.address, self._password)
            smtp.send_message(msg)
        return msg["Message-ID"] or ""

    # --- cleanup ------------------------------------------------------------------
    def consume_and_trash(self, mail: Mail, purpose: str, folder: str = "INBOX") -> None:
        """Log (without the code/link) and move a used verification mail to Trash."""
        if not self.is_agent_mail(mail):
            raise PermissionError(f"refusing to trash mail that isn't the agent's: {mail.sender} / {mail.subject!r}")
        if purpose != "self_test" and is_protected(mail):
            raise PermissionError(f"refusing to trash protected mail: {mail.subject!r}")
        if self.conn is not None:
            db.log_verification(self.conn, mail.message_id, mail.sender, purpose)
        imap = self.imap()
        imap.select(folder)
        typ, _ = imap.uid("MOVE", mail.uid, self.trash_folder())
        if typ != "OK":  # servers without MOVE: copy + delete + expunge
            imap.uid("COPY", mail.uid, self.trash_folder())
            imap.uid("STORE", mail.uid, "+FLAGS", r"(\Deleted)")
            imap.expunge()

    def sweep_stale_verification(self, max_age_h: float = 24, min_confidence: float = 0.8) -> list[str]:
        """Trash unused verification mail older than `max_age_h`. Returns trashed subjects."""
        cutoff = datetime.now(UTC) - timedelta(hours=max_age_h)
        trashed = []
        for uid in self.search(f'BEFORE "{(cutoff + timedelta(days=1)).strftime("%d-%b-%Y")}"'):
            mail = self.fetch(uid)
            if mail.date is None or mail.date > cutoff or not self.is_agent_mail(mail):
                continue  # too new, or the user's own mail
            v = classify_verification(mail)
            if v and v.confidence >= min_confidence:
                self.consume_and_trash(mail, "stale_sweep")
                trashed.append(mail.subject)
        return trashed


def open_inbox() -> Inbox:
    from jobagent.tools.vault import open_vault

    entry = open_vault().get("gmail") or {}
    if not entry.get("email") or not entry.get("app_password"):
        raise RuntimeError("gmail vault entry needs email and app_password (run `jobagent step gmail`)")
    return Inbox(entry["email"], entry["app_password"], db.connect())
