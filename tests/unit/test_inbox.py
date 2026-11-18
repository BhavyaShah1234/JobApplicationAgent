from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import format_datetime

import pytest

from jobagent import db
from jobagent.tools.inbox import Inbox, Mail, classify_verification, is_protected, parse


def _mail(subject: str, text: str, sender: str = "no-reply@greenhouse.io", age_h: float = 0, uid: str = "1") -> Mail:
    return Mail(uid, f"<{uid}@x>", sender, subject, datetime.now(UTC) - timedelta(hours=age_h), text)


@pytest.mark.parametrize(
    "subject,text,kind,code,link",
    [
        ("Your verification code", "Your one-time code is 482913. It expires in 10 minutes.", "otp", "482913", None),
        ("123456 is your Workday code", "Use this code to verify your email.", "otp", "123456", None),
        ("Verify your email", "Click https://acme.wd5.myworkdayjobs.com/verify?token=abc to confirm your email.",
         "link", None, "https://acme.wd5.myworkdayjobs.com/verify?token=abc"),
        ("Reset your password", "Reset your password here: https://jobs.example.com/reset?t=9", "password_reset",
         None, "https://jobs.example.com/reset?t=9"),
    ],
)
def test_classifies_verification(subject, text, kind, code, link):
    v = classify_verification(_mail(subject, text))
    assert v is not None and (v.kind, v.code, v.link) == (kind, code, link)


@pytest.mark.parametrize(
    "subject,text",
    [
        ("Interview invitation", "Please verify your availability using code 1234 and schedule a call."),
        ("Your application status", "Unfortunately we are not moving forward. Ref 556677."),
        ("Thank you for applying", "Confirm your email at https://x.com/confirm?t=1 to track your application."),
        ("Weekly newsletter", "Our office moved to 1600 Main St."),
    ],
)
def test_protected_or_unrelated_mail_is_not_verification(subject, text):
    assert classify_verification(_mail(subject, text)) is None


def test_html_only_mail_keeps_links():
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["Message-ID"] = "Confirm your account", "a@b.com", "<m1@b>"
    msg["Date"] = format_datetime(datetime.now(UTC))
    msg.set_content('<p>Please <a href="https://b.com/activate?token=xyz">activate your account</a></p>', subtype="html")
    mail = parse("7", msg.as_bytes())
    v = classify_verification(mail)
    assert v and v.link == "https://b.com/activate?token=xyz"


class FakeImap:
    def __init__(self, mails: dict[str, Mail]):
        self.mails, self.moved, self.folder = mails, [], None

    def select(self, folder):
        self.folder = folder

    def list(self):
        return "OK", [b'(\\HasNoChildren \\Trash) "/" "[Gmail]/Trash"']

    def uid(self, cmd, *args):
        if cmd == "MOVE":
            self.moved.append(args[0])
            self.mails.pop(args[0], None)
            return "OK", [None]
        if cmd == "SEARCH":
            self.searches = getattr(self, "searches", []) + [args[1]]
            return "OK", [" ".join(self.mails).encode()]
        raise AssertionError(cmd)


def _inbox(tmp_path, mails, agent_sites=("https://boards.greenhouse.io/acme",)):
    conn = db.connect(tmp_path / "jobs.sqlite")
    for site in agent_sites:
        db.record_agent_domain(conn, site)
    inbox = Inbox("agent@gmail.com", "abcd efgh ijkl mnop", conn)
    inbox._imap = FakeImap(mails)
    inbox.fetch = lambda uid, folder="INBOX": mails[uid]
    return inbox


def _own(inbox, mail):
    """The agent's action consumed this mail (what wait_for_verification records): the only thing that makes it the agent's."""
    inbox.begin_action("https://boards.greenhouse.io/acme", "portal")
    with inbox.conn:
        inbox.conn.execute("UPDATE agent_actions SET status = 'consumed', consumed_message_id = ?", (mail.message_id,))


@pytest.mark.parametrize(
    "value,domain",
    [("https://acme.wd5.myworkdayjobs.com/en-US/careers", "myworkdayjobs.com"),
     ("no-reply@us.greenhouse-mail.io", "greenhouse-mail.io"),
     ("Jobs <careers@mail.example.co.uk>", "example.co.uk"),
     ("boards.greenhouse.io", "greenhouse.io"),
     ("hr@unrelated.co.za", "unrelated.co.za"), ("hr@acme.co.za", "acme.co.za"), ("https://x.github.io/a", "x.github.io")],
)
def test_registrable_domain(value, domain):
    assert db.registrable_domain(value) == domain


def test_recording_an_ats_records_its_sender_family(tmp_path):
    conn = db.connect(tmp_path / "jobs.sqlite")
    recorded = db.record_agent_domain(conn, "https://acme.wd5.myworkdayjobs.com/job/123")
    assert {"myworkdayjobs.com", "myworkday.com"} <= recorded == db.agent_domains(conn)


def test_users_own_verification_mail_is_never_trashed(tmp_path):
    own = _mail("Your verification code", "code 777777", sender="no-reply@some-shop.com", age_h=48, uid="9")
    inbox = _inbox(tmp_path, {"9": own})
    assert not inbox.is_agent_mail(own)
    with pytest.raises(PermissionError, match="isn't the agent's"):
        inbox.consume_and_trash(own, "otp")
    assert inbox._imap.moved == []


def test_self_test_mail_counts_as_agent_mail(tmp_path):
    mail = _mail("Self-test jobagent-selftest-abc123", "hi", sender="bhavya24091952@gmail.com")
    inbox = _inbox(tmp_path, {"1": mail}, agent_sites=())
    inbox.consume_and_trash(mail, "self_test")
    assert inbox._imap.moved == ["1"]


def test_consume_and_trash_logs_without_secret(tmp_path):
    mail = _mail("Your verification code", "code 482913")
    inbox = _inbox(tmp_path, {"1": mail})
    _own(inbox, mail)
    inbox.consume_and_trash(mail, "otp")
    assert inbox._imap.moved == ["1"]
    row = inbox.conn.execute("SELECT * FROM verification_log").fetchone()
    assert (row["purpose"], row["domain"]) == ("otp", "greenhouse.io")
    assert "482913" not in " ".join(str(v) for v in tuple(row))


def test_refuses_to_trash_protected_mail(tmp_path):
    mail = _mail("Interview invitation", "verify code 1234")
    inbox = _inbox(tmp_path, {"1": mail})
    _own(inbox, mail)
    with pytest.raises(PermissionError):
        inbox.consume_and_trash(mail, "otp")
    assert inbox._imap.moved == []


def test_verification_search_starts_the_day_before(monkeypatch):
    """IMAP SINCE is a date in the server's zone: an evening-in-Arizona UTC date is already tomorrow there."""
    from datetime import datetime, UTC
    from jobagent.tools import inbox as I
    ib = I.Inbox.__new__(I.Inbox)
    ib.conn = None
    seen = {}

    def fake_wait_for(predicate, criteria="ALL", timeout_s=180, poll_s=5, header_filter=None):
        seen["criteria"] = criteria
        raise TimeoutError

    monkeypatch.setattr(ib, "wait_for", fake_wait_for, raising=False)
    try:
        ib.wait_for_verification("https://www.amazon.jobs", datetime(2026, 11, 3, 1, 30, tzinfo=UTC), timeout_s=1)
    except TimeoutError:
        pass
    assert seen["criteria"] == 'SINCE "02-Nov-2026"'


def test_mail_deadline_stops_inside_a_long_header_scan(monkeypatch):
    from jobagent.tools import inbox as module
    clock=[0.0];headers=[];bodies=[]
    monkeypatch.setattr(module.time,'monotonic',lambda:clock[0])
    ib=Inbox.__new__(Inbox)
    ib.search=lambda _:list(map(str,range(100)))
    def fetch(uid):
        headers.append(uid);clock[0]+=1
        return _mail('Unrelated','',uid=uid)
    ib.fetch_headers=fetch
    ib.fetch_text=lambda head:bodies.append(head.uid)
    with pytest.raises(TimeoutError):
        ib.wait_for(lambda _:False,timeout_s=3,header_filter=lambda _:False)
    assert headers==['99','98','97'] and bodies==[] and clock[0]==3


def test_mail_deadline_does_not_fetch_a_body_after_header_work_exhausts_budget(monkeypatch):
    from jobagent.tools import inbox as module
    clock=[0.0];bodies=[]
    monkeypatch.setattr(module.time,'monotonic',lambda:clock[0])
    ib=Inbox.__new__(Inbox);ib.search=lambda _:['1']
    def fetch(_):
        clock[0]=4
        return _mail('Verification','')
    ib.fetch_headers=fetch;ib.fetch_text=lambda head:bodies.append(head.uid)
    with pytest.raises(TimeoutError):ib.wait_for(lambda _:True,timeout_s=3,header_filter=lambda _:True)
    assert bodies==[]


def test_mail_poll_sleep_is_capped_by_remaining_budget(monkeypatch):
    from jobagent.tools import inbox as module
    clock=[0.0];sleeps=[]
    monkeypatch.setattr(module.time,'monotonic',lambda:clock[0])
    def sleep(seconds):sleeps.append(seconds);clock[0]+=seconds
    monkeypatch.setattr(module.time,'sleep',sleep)
    ib=Inbox.__new__(Inbox);ib.search=lambda _:[]
    with pytest.raises(TimeoutError):ib.wait_for(lambda _:False,timeout_s=3,poll_s=20)
    assert sleeps==[3] and clock[0]==3


def test_zero_mail_budget_retains_one_header_scan_and_no_body_without_permission(monkeypatch):
    from jobagent.tools import inbox as module
    headers=[];bodies=[]
    ib=Inbox.__new__(Inbox);ib.search=lambda _:['1']
    def fetch(uid):headers.append(uid);return _mail('Unrelated','',uid=uid)
    ib.fetch_headers=fetch;ib.fetch_text=lambda head:bodies.append(head.uid)
    with pytest.raises(TimeoutError):ib.wait_for(lambda _:False,timeout_s=0,header_filter=lambda _:False)
    assert headers==['1'] and bodies==[]
