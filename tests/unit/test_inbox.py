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
            return "OK", [" ".join(self.mails).encode()]
        raise AssertionError(cmd)


def _inbox(tmp_path, mails):
    inbox = Inbox("agent@gmail.com", "abcd efgh ijkl mnop", db.connect(tmp_path / "jobs.sqlite"))
    inbox._imap = FakeImap(mails)
    inbox.fetch = lambda uid, folder="INBOX": mails[uid]
    return inbox


def test_consume_and_trash_logs_without_secret(tmp_path):
    mail = _mail("Your verification code", "code 482913")
    inbox = _inbox(tmp_path, {"1": mail})
    inbox.consume_and_trash(mail, "otp")
    assert inbox._imap.moved == ["1"]
    row = inbox.conn.execute("SELECT * FROM verification_log").fetchone()
    assert (row["purpose"], row["domain"]) == ("otp", "greenhouse.io")
    assert "482913" not in " ".join(str(v) for v in tuple(row))


def test_refuses_to_trash_protected_mail(tmp_path):
    mail = _mail("Interview invitation", "verify code 1234")
    inbox = _inbox(tmp_path, {"1": mail})
    with pytest.raises(PermissionError):
        inbox.consume_and_trash(mail, "otp")
    assert inbox._imap.moved == []


def test_sweep_only_trashes_stale_verification(tmp_path):
    mails = {
        "1": _mail("Your verification code", "code 111111", age_h=30, uid="1"),   # stale → trash
        "2": _mail("Your verification code", "code 222222", age_h=2, uid="2"),    # fresh → keep
        "3": _mail("Interview invitation", "verify code 3333", age_h=48, uid="3"),  # protected → keep
        "4": _mail("Application received", "Thanks for applying", age_h=72, uid="4"),  # not verification
    }
    inbox = _inbox(tmp_path, mails)
    trashed = inbox.sweep_stale_verification(max_age_h=24)
    assert inbox._imap.moved == ["1"] and len(trashed) == 1
    assert set(mails) == {"2", "3", "4"}
