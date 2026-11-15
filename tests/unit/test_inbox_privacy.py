"""F08/F09: ownership is action-specific, candidates are judged on headers before any body, attachments are never fetched,
and sender authority comes from the exact sender domain, not from a display name."""
import email.utils
import json
from datetime import UTC, datetime, timedelta

import pytest

from jobagent import db
from jobagent.steps import submit as S
from jobagent.tools import inbox as I
from jobagent.tools.inbox import Inbox, Mail

NOW = datetime.now(UTC)
TRASH = '"[Gmail]/Trash"'


class Msg:
    """One stored message: headers, and its MIME parts by IMAP part number (type, subtype, charset, encoding, disposition, bytes)."""
    def __init__(self, uid, sender, subject, to, when, parts, mid=None, thrid="9", structure_text=None, partmap=None):
        self.uid, self.sender, self.subject, self.to, self.when, self.parts = uid, sender, subject, to, when, parts
        self.mid, self.thrid = mid or f"<m{uid}@x>", thrid
        self.structure_text, self.partmap = structure_text, partmap

    def header_bytes(self):
        return (f"From: {self.sender}\r\nTo: {self.to}\r\nSubject: {self.subject}\r\nDate: {email.utils.format_datetime(self.when)}\r\n"
                f"Message-ID: {self.mid}\r\n\r\n").encode()

    def structure(self):
        if self.structure_text:
            return self.structure_text

        def leaf(p):
            _, ctype, sub, charset, enc, disp, data = p
            d = f'("{disp}" NIL)' if disp else "NIL"
            return f'("{ctype}" "{sub}" ("charset" "{charset}") NIL NIL "{enc}" {len(data)} 1 NIL {d} NIL)'
        if len(self.parts) == 1:
            return leaf(self.parts[0])
        return "(" + "".join(leaf(p) for p in self.parts) + ' "mixed")'


class StrictImap:
    """A mailbox that records every FETCH and refuses anything that would retrieve more than headers, the structure or a
    named text part. A whole-message fetch, RFC822 and any attachment part raise."""
    def __init__(self, msgs):
        self.msgs = {m.uid: m for m in msgs}
        self.log, self.moved = [], []
        self.capabilities = ("IMAP4REV1", "MOVE", "UIDPLUS")

    def select(self, folder):
        return "OK", [b""]

    def list(self):
        return "OK", [b'(\\HasNoChildren \\Trash) "/" "[Gmail]/Trash"']

    def fetches(self):
        return [a[1:] for a in self.log if a[0] == "FETCH"]

    def uid(self, cmd, *args):
        self.log.append((cmd, *args))
        if cmd == "SEARCH":
            crit = args[1]
            keep = []
            for m in self.msgs.values():
                ok = True
                for field, attr in (("FROM", m.sender), ("TO", m.to), ("SUBJECT", m.subject)):
                    for needle in __import__("re").findall(rf'{field} "(.+?)"', crit):
                        ok = ok and needle.lower() in attr.lower()            # IMAP substring semantics
                keep.append(m.uid) if ok else None
            return "OK", [" ".join(keep).encode()]
        if cmd == "MOVE":
            self.moved.append(args[0])
            self.msgs.pop(args[0], None)
            return "OK", [None]
        if cmd != "FETCH":
            raise AssertionError(cmd)
        uid, spec = args
        m = self.msgs[uid]
        if "HEADER.FIELDS" in spec:
            h = m.header_bytes()
            return "OK", [(f"1 (UID {uid} X-GM-THRID {m.thrid} BODY[HEADER.FIELDS (...)] {{{len(h)}}}".encode(), h), b")"]
        if spec == "(BODYSTRUCTURE)":
            return "OK", [f"1 (UID {uid} BODYSTRUCTURE {m.structure()})".encode()]
        import re
        part = re.fullmatch(r"\(BODY\.PEEK\[([\d.]+)\]<0\.(\d+)>\)", spec)
        if not part:
            raise AssertionError(f"unexpected FETCH {spec!r}: more than headers, the structure or a text part")
        n, cap = part.group(1), int(part.group(2))
        _, ctype, sub, _, _, disp, data = m.partmap[n] if m.partmap else m.parts[int(n) - 1]
        if disp == "attachment" or ctype != "text":
            raise AssertionError(f"an attachment / non-text part {n} was requested")
        return "OK", [(f"1 (UID {uid} BODY[{n}]<0> {{{len(data[:cap])}}}".encode(), data[:cap]), b")"]


def text(body, enc="7bit"):
    return (1, "text", "plain", "utf-8", enc, None, body)


def vendor_mail(uid, to, when, code="483920", sender="Vendor <no-reply@vendor.com>", subject="Your verification code", extra=()):
    return Msg(uid, sender, subject, to, when, [("1", "text", "plain", "utf-8", "7bit", None,
                                                  f"Your verification code is {code}".encode()), *extra])


def make(tmp_path, msgs):
    conn = db.connect(tmp_path / "jobs.sqlite")
    inbox = Inbox("agent@gmail.com", "abcd efgh ijkl mnop", conn)
    inbox._imap = StrictImap(msgs)
    return inbox, conn


def bodies_fetched(inbox):
    return {f[0] for f in inbox._imap.fetches() if "BODY.PEEK[" in f[1] and "HEADER" not in f[1] or f[1] == "(BODYSTRUCTURE)"}


# --- F08: the user's own code from the same vendor is not the agent's -------------------------------------------------------------

def test_a_personal_code_from_the_same_vendor_is_not_taken_and_its_body_is_never_read(tmp_path):
    since = NOW - timedelta(minutes=2)
    personal = vendor_mail("1", "agent@gmail.com", NOW - timedelta(minutes=1), code="111111")        # the user's own sign-up
    mine = vendor_mail("2", "agent+job42@gmail.com", NOW - timedelta(minutes=1), code="222222")       # the agent's action address
    inbox, conn = make(tmp_path, [personal, mine])
    mail, v = inbox.wait_for_verification("https://vendor.com", since, address="agent+job42@gmail.com", timeout_s=2)
    assert v.code == "222222" and mail.uid == "2"
    assert "1" not in bodies_fetched(inbox)                                  # the personal mail's body/structure was never requested
    # the match is only a CANDIDATE of the action; the site's acceptance (mark_used) is what makes it the agent's
    row = conn.execute("SELECT candidate_message_id, consumed_message_id FROM agent_actions").fetchone()
    assert (row[0], row[1]) == (mine.mid, None)
    assert inbox.mark_used(mail) and conn.execute("SELECT consumed_message_id FROM agent_actions").fetchone()[0] == mine.mid


def test_exact_sender_domain_beats_the_imap_substring_search(tmp_path):
    since = NOW - timedelta(minutes=2)
    lookalikes = [vendor_mail("1", "agent+a@gmail.com", NOW, sender="x <a@vendor.com.evil.org>"),
                  vendor_mail("2", "agent+a@gmail.com", NOW, sender="x <a@notvendor.com>"),
                  vendor_mail("3", "agent+a@gmail.com", NOW, sender="Vendor <vendor.com@evil.org>")]
    inbox, _ = make(tmp_path, lookalikes)
    with pytest.raises(TimeoutError):
        inbox.wait_for_verification("https://vendor.com", since, address="agent+a@gmail.com", timeout_s=0)
    assert bodies_fetched(inbox) == set()                                    # all matched the server search; none was read


def test_mail_outside_the_actions_window_is_not_read(tmp_path):
    since = NOW - timedelta(minutes=2)
    old = vendor_mail("1", "agent+a@gmail.com", since - timedelta(hours=3))
    late = vendor_mail("2", "agent+a@gmail.com", since + timedelta(hours=3))
    inbox, _ = make(tmp_path, [old, late])
    with pytest.raises(TimeoutError):
        inbox.wait_for_verification("https://vendor.com", since, address="agent+a@gmail.com", timeout_s=0)
    assert bodies_fetched(inbox) == set()


def test_destructive_cleanup_needs_an_owning_action(tmp_path):
    since = NOW - timedelta(minutes=2)
    personal = vendor_mail("1", "agent@gmail.com", NOW - timedelta(minutes=1), code="111111")
    mine = vendor_mail("2", "agent+job42@gmail.com", NOW - timedelta(minutes=1), code="222222")
    inbox, conn = make(tmp_path, [personal, mine])
    db.record_agent_domain(conn, "https://vendor.com")                      # the old rule: a used domain was enough
    parsed = inbox.fetch("1")
    assert not inbox.is_agent_mail(parsed)
    with pytest.raises(PermissionError):
        inbox.consume_and_trash(parsed, "otp")
    mail, _ = inbox.wait_for_verification("https://vendor.com", since, address="agent+job42@gmail.com", timeout_s=2)
    assert inbox.mark_used(mail, "the site accepted it")
    assert inbox.consume_and_trash(mail, "otp").ok and inbox._imap.moved == ["2"]      # only the one the action consumed
    assert "1" in inbox._imap.msgs


def test_the_sweep_only_touches_mail_an_action_owns_and_never_reads_the_rest(tmp_path):
    old = NOW - timedelta(hours=40)
    personal = vendor_mail("1", "agent@gmail.com", old, code="111111")                      # same vendor, the user's own
    abandoned = vendor_mail("2", "agent+job7@gmail.com", old, code="222222")                 # the agent's unconsumed code
    inbox, conn = make(tmp_path, [personal, abandoned])
    db.record_agent_domain(conn, "https://vendor.com")
    inbox.begin_action("https://vendor.com", "portal", since=old - timedelta(minutes=5), address="agent+job7@gmail.com")
    swept = inbox.sweep_stale_verification(max_age_h=24)
    assert swept == ["Your verification code"] and inbox._imap.moved == ["2"] and "1" in inbox._imap.msgs
    assert "1" not in bodies_fetched(inbox)
    assert not any(c[0] == "SEARCH" and 'FROM "vendor.com"' in c[2] for c in inbox._imap.log if len(c) > 2)   # no sender sweep of the vendor


def test_a_tagged_self_test_is_still_the_agents_own(tmp_path):
    m = Msg("1", "agent@gmail.com", f"Self-test {I.SELF_TEST_TAG}abc", "agent@gmail.com", NOW - timedelta(hours=30),
            [("1", "text", "plain", "utf-8", "7bit", None, b"Harness inbox self-test.")])
    inbox, _ = make(tmp_path, [m])
    assert inbox.is_agent_mail(inbox.fetch_headers("1"))


# --- F08: headers first; only text parts; never an attachment ---------------------------------------------------------------------

def test_only_text_parts_are_fetched_never_the_attachment(tmp_path):
    pdf = ("3", "application", "pdf", "utf-8", "base64", "attachment", b"JVBERi0xLjQ=" * 5000)
    html = ("2", "text", "html", "utf-8", "7bit", None, b"<p>code 987654</p>")
    msg = Msg("1", "a@vendor.com", "Hello", "agent@gmail.com", NOW,
              [("1", "text", "plain", "utf-8", "7bit", None, b"plain body 987654"), html, pdf])
    inbox, _ = make(tmp_path, [msg])
    mail = inbox.fetch("1")
    assert "987654" in mail.text
    requested = [f[1] for f in inbox._imap.fetches()]
    assert not any("[3]" in r for r in requested) and not any(r == "(BODY.PEEK[])" or "RFC822" in r for r in requested)


def test_each_text_part_is_capped_and_encodings_are_decoded(tmp_path):
    import base64
    big = b"x" * (I.MAX_TEXT_BYTES * 2)
    msg = Msg("1", "a@vendor.com", "Hello", "agent@gmail.com", NOW,
              [("1", "text", "plain", "utf-8", "base64", None, base64.b64encode(b"Your code is 123456")),
               ("2", "text", "html", "utf-8", "7bit", None, big)])
    inbox, _ = make(tmp_path, [msg])
    assert "123456" in inbox.fetch("1").text
    caps = [f[1] for f in inbox._imap.fetches() if "BODY.PEEK[" in f[1] and "HEADER" not in f[1]]
    assert all(f"<0.{I.MAX_TEXT_BYTES}>" in c for c in caps)


def test_headers_carry_every_recipient_field(tmp_path):
    msg = Msg("1", "a@vendor.com", "Hello", "agent+job1@gmail.com", NOW, [("1", "text", "plain", "utf-8", "7bit", None, b"x")])
    inbox, _ = make(tmp_path, [msg])
    h = inbox.fetch_headers("1")
    assert h.has_text is False and h.text == "" and h.recipients == ("agent+job1@gmail.com",)


def test_the_structure_parser_handles_nested_multiparts_and_attachments():
    s = ('(("text" "plain" ("charset" "utf-8") NIL NIL "7bit" 5 1 NIL NIL NIL)("text" "html" ("charset" "utf-8") NIL NIL "7bit" 9 1 NIL NIL NIL) '
         '"alternative")')
    nested = f'({s}("application" "pdf" NIL NIL NIL "base64" 99 NIL ("attachment" ("filename" "cv.pdf")) NIL) "mixed")'
    parts = I.text_parts(I.parse_structure(nested))
    assert [(p["part"], p["subtype"]) for p in parts] == [("1.1", "plain"), ("1.2", "html")]
    assert I.text_parts(I.parse_structure('("text" "plain" ("charset" "utf-8") NIL NIL "7bit" 5 1 NIL NIL NIL)'))[0]["part"] == "1"


# --- F08/F09: confirmation and outcome candidates are judged on headers first ---------------------------------------------------------

class Domains:
    pass


JD = {"company": "Acme", "title": "Backend Engineer", "apply_url": "https://job-boards.greenhouse.io/acme/jobs/1",
      "source_url": "https://acme.example/jobs/1", "company_domains": ["acme.example"]}


def test_a_confirmation_wait_does_not_read_unrelated_candidates(tmp_path):
    since = NOW - timedelta(minutes=5)
    other = Msg("1", "Palantir <no-reply@us.greenhouse-mail.io>", "Thank you for applying to Palantir", "agent@gmail.com", NOW,
                [("1", "text", "plain", "utf-8", "7bit", None, b"Thank you for applying to Palantir")])
    forged = Msg("2", "Acme Recruiting <outsider@unrelated.example>", "Acme: thank you for applying", "agent@gmail.com", NOW,
                 [("1", "text", "plain", "utf-8", "7bit", None, b"thank you for applying")])
    good = Msg("3", "Acme <no-reply@us.greenhouse-mail.io>", "Thank you for applying to Acme", "agent@gmail.com", NOW,
               [("1", "text", "plain", "utf-8", "7bit", None, b"We have received your application")])
    inbox, conn = make(tmp_path, [other, forged, good])
    got = S.wait_for_confirmation(inbox, conn, JD, since, timeout_s=1)
    assert got is not None and got.uid == "3"
    assert bodies_fetched(inbox) == {"3"}                                    # the other two were rejected on their headers


# --- F09: sender authority comes from the exact domain --------------------------------------------------------------------------------

def mk(sender, subject="Update on your application", text=""):
    return Mail("1", "<m>", sender, subject, NOW, text)


def test_a_company_looking_display_name_cannot_stand_in_for_the_senders_domain():
    m = S.ApplicationMatch({"greenhouse.io", "carvana.com"}, "carvana", ["carvana", "engineer"], frozenset({"carvana.com"}))
    forged = mk("Carvana Recruiting <outsider@unrelated.example>", "Your Carvana application", "Carvana engineer role")
    assert not m(forged) and not m.plausible(forged)
    assert not m(mk("Carvana <carvana@gmail.com>", "Carvana engineer"))
    assert m(mk("Recruiting <jobs@mail.carvana.com>", "Next steps", "about the engineer role"))       # a genuine subdomain
    assert m(mk("no-reply@us.greenhouse-mail.io".replace("greenhouse-mail.io", "greenhouse.io"), "Your Carvana application"))


def test_the_name_guess_is_not_evidence_and_verified_metadata_is(tmp_path, monkeypatch):
    from jobagent.config import get_settings
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    get_settings.cache_clear()
    root = tmp_path / "companies"
    root.mkdir()
    jd = {"company": "Meta Platforms", "title": "Engineer", "apply_url": "https://boards.greenhouse.io/meta/jobs/1"}
    conn = db.connect(tmp_path / "jobs.sqlite")
    guessed = S.application_match(conn, jd)
    assert "metaplatforms.com" not in guessed.allowed and not guessed(mk("hr@metaplatforms.com", "Meta Platforms application"))
    (root / "meta-platforms.json").write_text(json.dumps({"website": "https://www.meta.com",
                                                          "sources": {"1": {"kind": "official", "url": "https://about.fb.com/x"}}}))
    (root / "meta-platforms.careers.json").write_text(json.dumps({"url": "https://www.metacareers.com/jobs", "board": None}))
    verified = S.application_match(conn, jd)
    assert {"meta.com", "fb.com", "metacareers.com"} <= verified.own
    assert verified(mk("Recruiter <jane@meta.com>", "Next steps", "Meta Platforms engineer role"))
    assert not verified(mk("hr@metaplatforms.com", "Meta Platforms application"))
    # a shared ATS host is never mistaken for the company's own domain
    (root / "meta-platforms.careers.json").write_text(json.dumps({"url": "https://boards.greenhouse.io/meta", "board": None}))
    assert "greenhouse.io" not in S.company_domains("Meta Platforms", {"company_domains": ["https://greenhouse.io"]})
    get_settings.cache_clear()



# --- F08 verification: exact recipients, selection is not ownership, attached multiparts are not read ------------------------------------------

@pytest.mark.parametrize("to", [
    '"Personal agent+job42@gmail.com" <agent+personal@gmail.com>',      # the address only inside a display name
    "xagent+job42@gmail.com",                                           # extra prefix characters
    "agent+job42@gmail.com.evil.org",                                   # extra suffix characters
    "agent+job43@gmail.com",                                            # a different plus alias
    "agent+job42@gmail.co",
])
def test_only_the_exact_recipient_address_counts_for_an_action(tmp_path, to):
    since = NOW - timedelta(minutes=2)
    wrong = vendor_mail("1", to, NOW - timedelta(minutes=1), code="111111")
    inbox, conn = make(tmp_path, [wrong])
    with pytest.raises(TimeoutError):
        inbox.wait_for_verification("https://vendor.com", since, address="agent+job42@gmail.com", timeout_s=0)
    assert bodies_fetched(inbox) == set()                                  # rejected on its headers
    inbox.begin_action("https://vendor.com", "portal", since=since, address="agent+job42@gmail.com")
    head = inbox.fetch_headers("1")
    assert not inbox.is_agent_mail(head)                                   # and it is not owned either
    with pytest.raises(PermissionError):
        inbox.consume_and_trash(inbox.fetch_text(head), "otp")
    assert inbox._imap.moved == []


def test_a_genuine_exact_recipient_with_a_display_name_still_matches(tmp_path):
    since = NOW - timedelta(minutes=2)
    good = vendor_mail("1", '"Agent Jobs" <Agent+Job42@Gmail.com>', NOW - timedelta(minutes=1), code="222222")
    inbox, _ = make(tmp_path, [good])
    mail, v = inbox.wait_for_verification("https://vendor.com", since, address="agent+job42@gmail.com", timeout_s=1)
    assert v.code == "222222" and mail.recipients == ("agent+job42@gmail.com",)


def test_the_sweep_rejects_a_look_alike_recipient_without_reading_it(tmp_path):
    old = NOW - timedelta(hours=40)
    lookalike = vendor_mail("1", '"agent+job7@gmail.com" <agent+other@gmail.com>', old, code="111111")
    inbox, conn = make(tmp_path, [lookalike])
    inbox.begin_action("https://vendor.com", "portal", since=old - timedelta(minutes=5), address="agent+job7@gmail.com")
    assert inbox.sweep_stale_verification(max_age_h=24) == [] and inbox._imap.moved == [] and bodies_fetched(inbox) == set()


def test_selecting_a_candidate_is_not_ownership_until_the_site_accepts_it(tmp_path):
    since = NOW - timedelta(minutes=10)
    agents = vendor_mail("1", "agent@gmail.com", NOW - timedelta(minutes=5), code="222222")       # the agent's own code
    personal = vendor_mail("2", "agent@gmail.com", NOW - timedelta(minutes=1), code="111111")     # the user's newer one
    inbox, conn = make(tmp_path, [agents, personal])
    mail, v = inbox.wait_for_verification("https://vendor.com", since, timeout_s=1)               # the plain-address caller pattern
    assert v.code == "111111"                                                                       # the ambiguity is real
    assert not inbox.is_agent_mail(mail)
    with pytest.raises(PermissionError):
        inbox.consume_and_trash(mail, "otp")                                                       # selected, not owned
    assert inbox._imap.moved == [] and conn.execute("SELECT consumed_message_id FROM agent_actions").fetchone()[0] is None
    assert inbox.mark_used(mail, "the site accepted it") is True                                    # only now
    assert inbox.is_agent_mail(mail) and inbox.consume_and_trash(mail, "otp").ok
    assert inbox._imap.moved == ["2"] and "1" in inbox._imap.msgs                                   # the other mail is untouched


def test_mark_used_needs_a_selected_candidate(tmp_path):
    stranger = vendor_mail("1", "agent@gmail.com", NOW, code="333333")
    inbox, _ = make(tmp_path, [stranger])
    assert inbox.mark_used(inbox.fetch("1")) is False and not inbox.is_agent_mail(inbox.fetch("1"))


class PageStub:
    def __init__(self, body, has_box=True):
        self.body, self.has_box, self.typed, self.pressed = body, has_box, [], []
        self.keyboard = type("K", (), {"press": lambda s, k: self.pressed.append(k)})()
        self.visited = []

    def evaluate(self, *a, **k):
        return None

    def locator(self, sel):
        page = self
        if sel == "body":
            return type("B", (), {"inner_text": lambda s, timeout=0: page.body})()
        return type("L", (), {"count": lambda s: int(page.has_box),
                              "first": type("F", (), {"fill": lambda s, v: page.typed.append(v),
                                                      "is_visible": lambda s: False})()})()

    def goto(self, url, wait_until=None):
        self.visited.append(url)

    def wait_for_timeout(self, ms):
        pass


class InboxStub:
    def __init__(self, text):
        self.calls = []
        self.mail = Mail("1", "<m1>", "no-reply@vendor.com", "Your verification code", NOW, text)

    def wait_for_verification(self, site, since, kind="portal", timeout_s=300, address=None):
        return self.mail, I.classify_verification(self.mail)

    def mark_used(self, mail, evidence=""):
        self.calls.append("used")
        return True

    def consume_and_trash(self, mail, purpose):
        self.calls.append("trash")
        return I.Cleanup("completed")


@pytest.mark.parametrize("page,expect", [
    (PageStub("Welcome back! Your email is verified."), ["used", "trash"]),                    # the site accepted it
    (PageStub("That code is incorrect. Try again."), []),                                       # rejected
    (PageStub("Enter the code", has_box=False), []),                                            # no field to put it in
])
def test_the_portal_caller_trashes_a_code_only_after_the_site_accepts_it(page, expect):
    from jobagent.tools import portal_auth
    inbox = InboxStub("Your verification code is 482913")
    done = portal_auth._confirm_email(page, inbox, "vendor.com", NOW - timedelta(minutes=1), lambda m: None, "otp", timeout_s=1)
    assert inbox.calls == expect and done is bool(expect)


def test_an_attached_multipart_subtree_is_never_requested(tmp_path):
    structure = ('(("text" "plain" ("charset" "utf-8") NIL NIL "7bit" 17 1 NIL NIL NIL)'
                 '(("text" "plain" ("charset" "utf-8") NIL NIL "7bit" 20 1 NIL NIL NIL)'
                 '("text" "html" ("charset" "utf-8") NIL NIL "7bit" 20 1 NIL NIL NIL) "mixed" ("boundary" "b") '
                 '("attachment" ("filename" "private.mime")) NIL) "mixed" ("boundary" "a") NIL NIL)')
    msg = Msg("1", "a@vendor.com", "Hello", "agent@gmail.com", NOW, [], structure_text=structure, partmap={
        "1": ("1", "text", "plain", "utf-8", "7bit", None, b"Main message body"),
        "2.1": ("2.1", "text", "plain", "utf-8", "7bit", "attachment", b"PRIVATE ATTACHED TEXT"),
        "2.2": ("2.2", "text", "html", "utf-8", "7bit", "attachment", b"<p>PRIVATE</p>")})
    inbox, _ = make(tmp_path, [msg])
    mail = inbox.fetch("1")
    assert mail.text == "Main message body"
    assert not any("[2" in f[1] for f in inbox._imap.fetches())
    assert [p["part"] for p in I.text_parts(I.parse_structure(structure))] == ["1"]


def test_inline_nested_multiparts_are_still_read():
    s = ('((("text" "plain" ("charset" "utf-8") NIL NIL "7bit" 5 1 NIL NIL NIL)("text" "html" ("charset" "utf-8") NIL NIL "7bit" 9 1 NIL NIL NIL) '
         '"alternative" ("boundary" "x") NIL NIL)("application" "pdf" NIL NIL NIL "base64" 99 NIL ("attachment" ("filename" "cv.pdf")) NIL) '
         '"mixed" ("boundary" "y") NIL NIL)')
    assert [p["part"] for p in I.text_parts(I.parse_structure(s))] == ["1.1", "1.2"]


# --- F09 verification: public-suffix-aware identities ------------------------------------------------------------------------------------

def test_a_shared_suffix_does_not_make_every_sender_the_companys(tmp_path):
    conn = db.connect(tmp_path / "jobs.sqlite")
    jd = {"company": "Acme", "title": "Backend Engineer", "apply_url": "https://boards.greenhouse.io/acme/jobs/1",
          "company_domains": ["https://acme.co.za"]}
    m = S.application_match(conn, jd)
    assert m.own == frozenset({"acme.co.za"}) and "co.za" not in m.allowed
    forged = mk("Acme Recruiting <hr@unrelated.co.za>", "Your Acme Backend Engineer application", "Acme Backend Engineer")
    assert not m.plausible(forged) and not m(forged)
    assert m(mk("Recruiting <hr@acme.co.za>", "Next steps", "about the Backend Engineer role"))
    assert m(mk("Recruiting <hr@mail.acme.co.za>", "Next steps", "about the Backend Engineer role"))


@pytest.mark.parametrize("configured,genuine,forged", [
    ("https://acme.co.uk", "hr@acme.co.uk", "hr@evil.co.uk"),
    ("https://acme.com.au", "hr@jobs.acme.com.au", "hr@other.com.au"),
    ("https://acme.github.io", "hr@acme.github.io", "hr@attacker.github.io"),         # shared hosted tenants
    ("https://acme.herokuapp.com", "hr@acme.herokuapp.com", "hr@evil.herokuapp.com"),
])
def test_other_multi_label_and_private_suffixes_follow_the_same_policy(tmp_path, configured, genuine, forged):
    conn = db.connect(tmp_path / "jobs.sqlite")
    m = S.application_match(conn, {"company": "Acme", "title": "Engineer", "apply_url": "https://boards.greenhouse.io/acme/1",
                                   "company_domains": [configured]})
    assert m(mk(genuine, "Update", "Acme engineer role")) and not m(mk(f"Acme <{forged}>", "Acme engineer role", "Acme engineer"))


@pytest.mark.parametrize("suffix", ["co.za", "co.uk", "github.io", "com"])
def test_a_public_suffix_is_never_a_companys_own_domain(tmp_path, suffix):
    assert S.company_domains("Acme", {"company_domains": [suffix, f"https://{suffix}"]}) == set()


def test_the_public_suffix_algorithm():
    from jobagent.tools import psl
    assert psl.registrable_domain("a.b.acme.co.za") == "acme.co.za" and psl.is_public_suffix("co.za")
    assert psl.registrable_domain("x.github.io") == "x.github.io" and psl.registrable_domain("a.b.github.io") == "b.github.io"
    assert psl.registrable_domain("www.ck") == "www.ck"                       # an exception rule (!www.ck)
    assert psl.registrable_domain("anything.ck") == "anything.ck" and psl.is_public_suffix("anything.ck")     # a wildcard rule (*.ck)
    assert psl.registrable_domain("bücher.example.de") == "example.de" or psl.registrable_domain("bücher.de") == "xn--bcher-kva.de"
    assert psl.registrable_domain("10.0.0.1") == "10.0.0.1" and psl.registrable_domain("localhost") == "localhost"
    assert psl.registrable_domain("something.unknowntld") == "something.unknowntld"     # the default rule
    assert db.registrable_domain("hr@Unrelated.CO.ZA") == "unrelated.co.za"
