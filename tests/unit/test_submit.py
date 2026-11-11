from datetime import UTC, datetime, timedelta
from pathlib import Path

from jobagent import db
from jobagent.steps import submit as S
from jobagent.tools.inbox import Mail

JD = {"company": "DoorDash", "title": "Software Engineer I, Entry-Level", "apply_url":
      "https://job-boards.greenhouse.io/doordashusa/jobs/8163709", "source_url": "https://x/8163709",
      "portal": "greenhouse"}


def test_records_follow_the_application(tmp_path):
    conn = db.connect(tmp_path / "jobs.sqlite")
    S.record(conn, Path("outputs/doordash/1"), JD, state="filled", filled_at="t1")
    S.record(conn, Path("outputs/doordash/1"), JD, state="submitted", submitted_at="t2")
    row = conn.execute("SELECT * FROM applications").fetchone()
    assert (row["state"], row["filled_at"], row["submitted_at"], row["company"]) == ("submitted", "t1", "t2", "DoorDash")


def test_review_packet_lists_flags_first(tmp_path):
    result = {"url": "u", "verified": 1, "pages": [{"screenshot": "page-1.png"}], "fields": [
        {"page": 1, "label": "Name", "readback": "Bhavya", "source": "applicant", "verified": True, "flag": "", "error": ""},
        {"page": 1, "label": "Internships", "readback": "", "source": "none", "verified": False, "flag": "", "error": ""}]}
    text = S.review_text(tmp_path, result, JD, "average 80")
    assert text.index("Look at these first") < text.index("Every field") and "Internships" in text


class FakeInbox:
    def __init__(self, mails):
        self.mails = mails

    def wait_for(self, predicate, criteria, timeout_s=0, poll_s=0):
        for m in self.mails:
            if predicate(m):
                return m
        raise TimeoutError


def test_confirmation_mail_is_this_application_after_submission(tmp_path):
    conn = db.connect(tmp_path / "jobs.sqlite")
    since = datetime(2026, 11, 5, 18, 0, tzinfo=UTC)
    mk = lambda sender, subject, when, text="": Mail("1", "<m>", sender, subject, when, text)  # noqa: E731
    old = mk("no-reply@greenhouse.io", "Thank you for applying to DoorDash", since - timedelta(hours=3))
    other = mk("no-reply@greenhouse.io", "Thank you for applying to Stripe", since + timedelta(minutes=5))
    news = mk("news@doordash.com", "DoorDash weekly deals", since + timedelta(minutes=5))
    good = mk("no-reply@us.greenhouse-mail.io", "Thank you for applying to DoorDash", since + timedelta(minutes=3))
    got = S.wait_for_confirmation(FakeInbox([old, other, news, good]), conn, JD, since)
    assert got is good


def test_thank_you_pages_are_recognised():
    assert S.CONFIRMED_PAGE.search("Thank you for applying! We've received your application.")
    assert S.CONFIRMED_PAGE.search("Your application has been submitted")
    assert not S.CONFIRMED_PAGE.search("Review your application before you submit")


def test_another_companys_mail_on_a_shared_ats_sender_never_matches_by_a_title_word():
    from jobagent.steps.submit import ApplicationMatch
    m = ApplicationMatch({"greenhouse-mail.io", "greenhouse.io", "stripe.com"}, "stripe", ["stripe", "backend", "engineer"],
                         frozenset({"stripe.com"}))
    mk = lambda sender, subject, text="": Mail("1", "<m>", sender, subject, None, text)  # noqa: E731
    assert not m(mk("Palantir <no-reply@us.greenhouse-mail.io>", "Thank you for applying", "to Software Engineer at Palantir"))
    assert m(mk("Stripe <no-reply@us.greenhouse-mail.io>", "Thank you for applying", "to Backend Engineer"))   # display name
    assert m(mk("no-reply@us.greenhouse-mail.io", "Update on your application to Stripe"))                       # subject
    assert m(mk("Recruiter <jane@stripe.com>", "Next steps", "about the Backend role"))                           # their own domain
    assert not m(mk("jane@gmail.com", "Stripe interview"))                                                         # not from them
