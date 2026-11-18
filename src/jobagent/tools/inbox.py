"""Agent Gmail inbox over IMAP/SMTP (app password), verification-mail parsing and cleanup.

Only verification mail (OTP / one-time code / login or verification link / password reset) is ever
trashed, and only after its use succeeded or when stale. Application-related mail is never trashed.
"""

import base64
import binascii
import email
import email.header
import email.utils
import imaplib
import quopri
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
class Cleanup:
    """What a cleanup did: `completed` (the message is in Trash), `failed` (a step was refused; see detail for what
    state it is left in) or `unsupported` (the server cannot do it safely, nothing was touched)."""
    status: str
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "completed"


@dataclass
class Mail:
    uid: str
    message_id: str
    sender: str
    subject: str
    date: datetime | None
    text: str
    thread_id: str = ""
    recipients: tuple = ()        # complete normalized mailbox addresses from To, Cc, Delivered-To and X-Original-To
    has_text: bool = True         # False for a headers-only message (its body was not retrieved)
    uidvalidity: str = ""         # the folder's UIDVALIDITY when this was read: (uidvalidity, uid) is the durable identity
    delivered: datetime | None = None   # INTERNALDATE: when the mailbox received it (not what the Date header claims)
    date_ok: bool = True          # False when the Date header was missing or unparsable (date is then None)
    bulk: bool = False            # List-Unsubscribe or Precedence: bulk/list/junk: a mailing, not a personal message


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


def _recipients(msg: Message) -> tuple:
    """The recipients as parsed mailboxes (display names dropped, lower-cased): a display name that merely contains an address,
    or a larger address that contains one, is not that recipient."""
    values = [str(v) for h in ("To", "Cc", "Delivered-To", "X-Original-To") for v in msg.get_all(h, [])]
    return tuple(dict.fromkeys(addr.strip().lower() for _, addr in email.utils.getaddresses(values) if addr and "@" in addr))


def parse(uid: str, raw: bytes, thread_id: str = "", headers_only: bool = False) -> Mail:
    msg = email.message_from_bytes(raw)
    subject = str(email.header.make_header(email.header.decode_header(msg.get("Subject", ""))))
    date, ok = None, bool(msg["Date"])
    try:
        date = parsedate_to_datetime(msg["Date"]) if msg["Date"] else None
        if date is not None and date.tzinfo is None:
            date = date.replace(tzinfo=UTC)      # a Date without a zone is read as UTC, the same way every time
    except (TypeError, ValueError, IndexError):
        date, ok = None, False
    bulk = bool(msg.get("List-Unsubscribe")) or str(msg.get("Precedence", "")).strip().lower() in ("bulk", "list", "junk")
    return Mail(uid, msg.get("Message-ID", "").strip(), msg.get("From", ""), subject, date,
                "" if headers_only else _body_text(msg), thread_id, _recipients(msg), not headers_only, date_ok=ok, bulk=bulk)


# --- retrieving only what is needed (RFC 9051): headers first, then the text parts of plausible messages --------------------------

HEADER_FIELDS = "FROM TO CC DELIVERED-TO X-ORIGINAL-TO SUBJECT DATE MESSAGE-ID LIST-UNSUBSCRIBE PRECEDENCE"
MAX_TEXT_BYTES = 64_000       # per text part: a verification code or a status line is never deeper than this


def _tokens(s: str):
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c in "() ":
            if c != " ":
                yield c
            i += 1
        elif c == '"':
            j, out = i + 1, []
            while j < n and s[j] != '"':
                if s[j] == "\\" and j + 1 < n:
                    j += 1
                out.append(s[j])
                j += 1
            yield "".join(out)
            i = j + 1
        else:
            j = i
            while j < n and s[j] not in '() "':
                j += 1
            word = s[i:j]
            yield None if word.upper() == "NIL" else word
            i = j


def parse_structure(s: str):
    """A BODYSTRUCTURE string as nested lists (NIL as None)."""
    stack: list[list] = [[]]
    for tok in _tokens(s):
        if tok == "(":
            stack.append([])
        elif tok == ")":
            done = stack.pop()
            stack[-1].append(done)
        else:
            stack[-1].append(tok)
    return stack[0][0] if stack[0] else []


def text_parts(structure, prefix: str = "") -> list[dict]:
    """The text/plain and text/html parts that are not attachments: [{part, subtype, charset, encoding, size}]. Everything
    else (attachments, images, nested messages) is never requested."""
    out: list[dict] = []
    if not structure:
        return out
    if isinstance(structure[0], list):
        # multipart: the child bodies, then the subtype string, then the extension fields (parameters, disposition, ...).
        # Only the leading lists are children; the parameter and disposition lists come after the subtype.
        children, extension = [], []
        for item in structure:
            (extension if (extension or not isinstance(item, list)) else children).append(item)
        disposition = extension[2] if len(extension) > 2 and isinstance(extension[2], list) else None
        if disposition and str(disposition[0]).lower() == "attachment":
            return out                                       # an attached multipart: none of its descendants is read
        for k, child in enumerate(children, 1):
            out += text_parts(child, f"{prefix}{k}" if not prefix else f"{prefix}.{k}")
        return out
    kind, sub = str(structure[0]).lower(), str(structure[1]).lower()
    if kind != "text" or sub not in ("plain", "html"):
        return out
    params = structure[2] if len(structure) > 2 and isinstance(structure[2], list) else []
    pdict = {str(params[i]).lower(): params[i + 1] for i in range(0, len(params) - 1, 2)}
    disposition = structure[9] if len(structure) > 9 and isinstance(structure[9], list) else None
    if disposition and str(disposition[0]).lower() == "attachment":
        return out
    size = int(structure[6]) if len(structure) > 6 and str(structure[6]).isdigit() else 0
    return [{"part": prefix or "1", "subtype": sub, "charset": pdict.get("charset") or "utf-8",
             "encoding": str(structure[5] or "7bit").lower() if len(structure) > 5 else "7bit", "size": size}]


def _decode_part(raw: bytes, encoding: str, charset: str) -> str:
    if encoding == "base64":
        raw = raw.strip()
        raw = raw[: len(raw) - len(raw) % 4]
        try:
            raw = base64.b64decode(raw)
        except (binascii.Error, ValueError):
            raw = b""
    elif encoding == "quoted-printable":
        raw = quopri.decodestring(raw)
    try:
        return raw.decode(charset, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def _assemble(plain: list[str], html: list[str]) -> str:
    if plain:
        return "\n".join(plain)
    joined = "\n".join(html)
    joined = re.sub(r"(?is)<a\s[^>]*href=[\"']([^\"']+)[\"'][^>]*>", r" \1 ", joined)
    return unescape(re.sub(r"(?s)<[^>]+>", " ", joined))


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
    # The privacy contract, honestly: the mailbox is read through server-side filters (sender, date), and every candidate is
    # judged on its HEADERS first; only a plausible one has its text parts retrieved, never an attachment. A domain the agent
    # used is NOT proof that a mail is the agent's: the user's own code from the same vendor looks identical. Ownership is
    # action-specific: a mail is the agent's only if an action it started consumed it (Message-ID), or it was delivered to an
    # address the agent made up for an action (`name+tag@...`), or it is the agent's tagged self-test. Destructive cleanup
    # needs that ownership. Where the agent used the plain address, a same-vendor mail in the same window cannot be told
    # from the user's, so it can be READ to find the agent's code but is never deleted unless consumed by that action.
    ACTION_WINDOW = timedelta(minutes=60)

    def begin_action(self, site: str, kind: str = "portal", since: datetime | None = None, address: str | None = None) -> int:
        """Record one thing the agent is about to do that expects mail. `address`: the exact address it used."""
        if self.conn is None:
            return 0
        db.record_agent_domain(self.conn, site, kind)
        domain = db.registrable_domain(site)
        start = (since or datetime.now(UTC)).astimezone(UTC)
        with self.conn:
            cur = self.conn.execute("INSERT INTO agent_actions (domain, kind, address, started_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                                    (domain, kind, (address or self.address).lower(), start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                     (start + self.ACTION_WINDOW).strftime("%Y-%m-%dT%H:%M:%SZ")))
        return cur.lastrowid

    def action_address(self, token: str) -> str:
        """A plus-addressed variant of the agent's address for one action: mail sent to it can only be about that action."""
        local, _, domain = self.address.partition("@")
        return f"{local}+{token}@{domain}"

    def _owned_by_action(self, mail: Mail) -> bool:
        if self.conn is None:
            return False
        if mail.message_id and self.conn.execute("SELECT 1 FROM agent_actions WHERE consumed_message_id = ?",
                                                 (mail.message_id,)).fetchone():
            return True
        for row in self.conn.execute("SELECT domain, address FROM agent_actions WHERE address LIKE '%+%'"):
            if (row["address"] or "").lower() in mail.recipients and sender_domain(mail) == row["domain"]:   # the whole address
                return True
        return False

    def is_agent_mail(self, mail: Mail) -> bool:
        """The agent's own tagged self-test, or mail an agent ACTION owns (see above). Never merely 'from a site it used'."""
        return SELF_TEST_TAG in mail.subject or self._owned_by_action(mail)

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

    def fetch_headers(self, uid: str, folder: str = "INBOX") -> Mail:
        """Only the headers of one message (and Gmail's thread id): no body, no attachment."""
        imap = self.imap()
        imap.select(folder)
        typ, data = imap.uid("FETCH", uid, f"(X-GM-THRID INTERNALDATE BODY.PEEK[HEADER.FIELDS ({HEADER_FIELDS})])")
        if typ != "OK":
            raise imaplib.IMAP4.error(f"FETCH {uid} answered {typ}")      # transient: NOT proof that the message is gone
        if not data or data[0] is None or not isinstance(data[0], tuple):
            raise LookupError(f"message uid {uid} not found in {folder}")  # the server answered and there is no such message
        meta, raw = data[0][0].decode(errors="replace"), data[0][1]
        thrid = re.search(r"X-GM-THRID (\d+)", meta)
        mail = parse(uid, raw, thrid.group(1) if thrid else "", headers_only=True)
        mail.uidvalidity = self.uidvalidity(folder)
        m = re.search(r'INTERNALDATE "([^"]+)"', meta)
        if m:
            try:
                mail.delivered = datetime.strptime(m.group(1).strip(), "%d-%b-%Y %H:%M:%S %z").astimezone(UTC)
            except ValueError:
                mail.delivered = None
        return mail

    def uidvalidity(self, folder: str = "INBOX") -> str:
        """The folder's UIDVALIDITY ('' if the server does not say): with a UID it names a message for good."""
        imap = self.imap()
        imap.select(folder)
        try:
            typ, vals = imap.response("UIDVALIDITY")
            return vals[0].decode() if typ == "UIDVALIDITY" and vals and vals[0] else ""
        except Exception:
            return ""

    def fetch_text(self, mail: Mail, folder: str = "INBOX") -> Mail:
        """The text/plain and text/html parts of a message whose headers were judged plausible: the structure first, then each
        text part (at most MAX_TEXT_BYTES). Attachments and other parts are never requested."""
        imap = self.imap()
        imap.select(folder)
        typ, data = imap.uid("FETCH", mail.uid, "(BODYSTRUCTURE)")
        if typ != "OK" or not data or data[0] is None:
            raise LookupError(f"message uid {mail.uid} not found in {folder}")
        row = data[0][0] if isinstance(data[0], tuple) else data[0]
        m = re.search(r"BODYSTRUCTURE (.*)\)\s*$", row.decode(errors="replace"), re.S)
        parts = text_parts(parse_structure(m.group(1) if m else ""))
        plain, html = [], []
        for part in parts:
            typ, got = imap.uid("FETCH", mail.uid, f"(BODY.PEEK[{part['part']}]<0.{MAX_TEXT_BYTES}>)")
            if typ != "OK" or not got or not isinstance(got[0], tuple):
                continue
            (plain if part["subtype"] == "plain" else html).append(_decode_part(got[0][1], part["encoding"], part["charset"]))
        return Mail(mail.uid, mail.message_id, mail.sender, mail.subject, mail.date, _assemble(plain, html), mail.thread_id,
                    mail.recipients, True, mail.uidvalidity, mail.delivered, mail.date_ok, mail.bulk)

    def fetch(self, uid: str, folder: str = "INBOX") -> Mail:
        """The whole readable message: headers, then its text parts (no attachment is ever retrieved)."""
        return self.fetch_text(self.fetch_headers(uid, folder), folder)

    def wait_for(self, predicate, criteria: str = "ALL", timeout_s: float = 180, poll_s: float = 5,
                 header_filter=None) -> Mail:
        """Poll the inbox until a message satisfies `predicate` (newest first). `header_filter(headers_only_mail)` decides
        from the headers alone whether the message may be read at all: a rejected candidate's body is never retrieved.
        Positive budgets are checked between IMAP operations; a nonpositive budget retains one immediate scan."""
        deadline = time.monotonic() + timeout_s
        seen: set[str] = set()
        while True:
            if timeout_s > 0 and time.monotonic() >= deadline:
                raise TimeoutError(f"no matching mail within {timeout_s}s")
            for uid in reversed(self.search(criteria)):
                if timeout_s > 0 and time.monotonic() >= deadline:
                    raise TimeoutError(f"no matching mail within {timeout_s}s")
                if uid in seen:
                    continue
                seen.add(uid)
                head = self.fetch_headers(uid)
                if header_filter is not None and not header_filter(head):
                    continue
                if timeout_s > 0 and time.monotonic() >= deadline:
                    raise TimeoutError(f"no matching mail within {timeout_s}s")
                mail = self.fetch_text(head)
                if predicate(mail):
                    return mail
            if time.monotonic() >= deadline:
                raise TimeoutError(f"no matching mail within {timeout_s}s")
            time.sleep(min(poll_s, max(0, deadline - time.monotonic())))
            seen.clear()  # re-check: Gmail may deliver late into an existing UID range

    def wait_for_verification(self, site: str, since: datetime, kind: str = "portal",
                              timeout_s: float = 300, address: str | None = None) -> tuple[Mail, Verification]:
        """Wait for a verification mail from `site` (URL or domain) sent after `since` (the agent's action).

        An action is recorded first. A candidate is read only if, from its headers alone, it comes from the site's exact
        sender domain, was sent inside the action's window and (when the action used a plus-addressed `address`) was
        delivered to exactly that address. The mail that matches is recorded as consumed by the action, which is what later
        allows it to be trashed.
        """
        allowed = db.record_agent_domain(self.conn, site, kind) if self.conn is not None else {db.registrable_domain(site)}
        action = self.begin_action(site, kind, since, address)
        end = since + self.ACTION_WINDOW
        want = (address or "").lower() if address and "+" in address else ""
        # IMAP SINCE is a calendar date in the server's own time zone (Gmail: US Pacific). A UTC date can already be
        # tomorrow there (evenings in Arizona), which hid every code arriving after 5 pm: search from the day before;
        # the exact cut-off (mail sent after the agent's action) is checked on each message's own timestamp below.
        criteria = f'SINCE "{(since - timedelta(days=1)).strftime("%d-%b-%Y")}"'
        found: dict[str, Verification] = {}

        def headers_ok(m: Mail) -> bool:
            if m.date is None or m.date < since - timedelta(seconds=30) or m.date > end:
                return False
            if sender_domain(m) not in allowed:          # the exact registrable domain, not a substring of the address
                return False
            return not want or want in m.recipients

        def match(m: Mail) -> bool:
            v = classify_verification(m)
            if v:
                found[m.uid] = v
            return v is not None

        mail = self.wait_for(match, criteria, timeout_s, header_filter=headers_ok)
        # Selecting a candidate is NOT ownership: it only means the headers and a code pattern fit. The mail becomes the
        # agent's, and may be trashed, when the caller confirms the site accepted it (`mark_used`).
        if self.conn is not None and action:
            with self.conn:
                self.conn.execute("UPDATE agent_actions SET candidate_message_id = ? WHERE id = ?", (mail.message_id, action))
        return mail, found[mail.uid]

    def mark_used(self, mail: Mail, evidence: str = "") -> bool:
        """The caller saw the site accept this verification mail's code or link (the page moved on, the account is signed in):
        only now does the action own it, which is what allows it to be trashed. Mail that was merely selected, a code that
        was rejected or had no field to go into, and ambiguous same-vendor mail never become the agent's this way."""
        if self.conn is None:
            return False
        with self.conn:
            cur = self.conn.execute(
                "UPDATE agent_actions SET status = 'consumed', consumed_message_id = ?, consumed_at = ?, evidence = ? "
                "WHERE id = (SELECT id FROM agent_actions WHERE candidate_message_id = ? AND status = 'open' ORDER BY id DESC LIMIT 1)",
                (mail.message_id, datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), evidence[:200], mail.message_id))
        return cur.rowcount > 0

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
    def consume_and_trash(self, mail: Mail, purpose: str, folder: str = "INBOX") -> "Cleanup":
        """Move a used verification mail to Trash and say what happened. The audit row (without the code or link) is
        written after the attempt, with its real outcome: `completed` only when the message is in Trash."""
        if not self.is_agent_mail(mail):
            raise PermissionError(f"refusing to trash mail that isn't the agent's: {mail.sender} / {mail.subject!r}")
        if purpose != "self_test" and is_protected(mail):
            raise PermissionError(f"refusing to trash protected mail: {mail.subject!r}")
        result = self._move_to_trash(mail.uid, folder)
        if self.conn is not None:
            db.log_verification(self.conn, mail.message_id, mail.sender, purpose,
                                "completed" if result.ok else "failed", result.detail)
        return result

    def _move_to_trash(self, uid: str, folder: str) -> "Cleanup":
        """MOVE; else COPY + STORE + `UID EXPUNGE <uid>`, each step checked, and only where the server can expunge that
        one message (UIDPLUS). A plain EXPUNGE would also remove every other message already flagged \\Deleted in the
        folder, so without UIDPLUS (or after any failed step) nothing further is done: the message stays where it is."""
        imap = self.imap()
        imap.select(folder)
        trash = self.trash_folder()

        def run(cmd: str, *args) -> tuple[bool, str]:
            try:
                typ, _ = imap.uid(cmd, *args)
            except (imaplib.IMAP4.error, OSError) as e:
                return False, f"{cmd} raised {type(e).__name__}"
            return typ == "OK", f"{cmd} answered {typ}"

        ok, why = run("MOVE", uid, trash)
        if ok:
            return Cleanup("completed", "MOVE")
        caps = {(c.decode() if isinstance(c, bytes) else str(c)).upper() for c in getattr(imap, "capabilities", ())}
        if "UIDPLUS" not in caps:
            return Cleanup("unsupported", f"{why}, and the server has no UIDPLUS: a copy would need an unrestricted "
                                          f"EXPUNGE, so the message was left in {folder}")
        ok, why = run("COPY", uid, trash)
        if not ok:
            return Cleanup("failed", f"{why}: the message was left untouched")
        ok, why = run("STORE", uid, "+FLAGS", r"(\Deleted)")
        if not ok:
            return Cleanup("failed", f"{why} after a good COPY: not expunged (a copy is in Trash, the original stays)")
        ok, why = run("EXPUNGE", uid)       # UID EXPUNGE <uid>
        if not ok:
            return Cleanup("failed", f"{why}: the original is flagged \\Deleted and left in place")
        return Cleanup("completed", "COPY + STORE + UID EXPUNGE")

    def sweep_stale_verification(self, max_age_h: float = 24, min_confidence: float = 0.8) -> list[str]:
        """Trash unused verification mail older than `max_age_h` that an agent ACTION owns: mail delivered to the
        plus-addressed address of an action that no wait consumed, and the agent's tagged self-test. Mail merely from a site
        the agent used is left alone (it may be the user's own), and nothing else is downloaded: candidates come from
        server-side searches on that address (or the self-test tag), their headers are checked, and only then their text."""
        cutoff = datetime.now(UTC) - timedelta(hours=max_age_h)
        trashed: list[str] = []
        before = (cutoff + timedelta(days=1)).strftime("%d-%b-%Y")
        searches = [f'SUBJECT "{SELF_TEST_TAG}" BEFORE "{before}"']
        if self.conn is not None:
            for row in self.conn.execute("SELECT DISTINCT address FROM agent_actions WHERE address LIKE '%+%' AND status = 'open'"):
                searches.append(f'TO "{row["address"]}" BEFORE "{before}"')
        done: set[str] = set()
        for criteria in searches:
            for uid in self.search(criteria):
                if uid in done:
                    continue
                done.add(uid)
                head = self.fetch_headers(uid)
                if head.date is None or head.date > cutoff or not self.is_agent_mail(head):
                    continue                      # too new, or not provably the agent's: its body is never read
                mail = self.fetch_text(head)
                v = classify_verification(mail)
                if v and v.confidence >= min_confidence:
                    if self.consume_and_trash(mail, "stale_sweep").ok:   # only what really reached Trash is reported
                        trashed.append(mail.subject)
        return trashed


def open_inbox() -> Inbox:
    from jobagent.tools.vault import open_vault

    entry = open_vault().get("gmail") or {}
    if not entry.get("email") or not entry.get("app_password"):
        raise RuntimeError("gmail vault entry needs email and app_password (run `jobagent step gmail`)")
    return Inbox(entry["email"], entry["app_password"], db.connect())
