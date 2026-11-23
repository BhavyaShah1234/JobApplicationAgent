"""An application that continues from an emailed link: recognising the page, choosing the link, and the per-run limit."""
from datetime import datetime, timezone

import pytest

from jobagent.tools import email_gate as G
from jobagent.tools import inbox as I

GARMIN = ("Please verify your email account with us by clicking on the link we have just sent to bhavya24091952@gmail.com. "
          "If you have entered an incorrect email address, or do not receive a notification within 5 minutes, please go back and try again")
ALLOWED = {"icims.com", "garmin.com"}


@pytest.mark.parametrize("text", [GARMIN, "Check your email to continue.", "We've sent you an email with a link.", "Verify your e-mail address",
                                  "A link has been sent to your inbox", "Click the link in the email to continue your application"])
def test_pages_that_wait_for_an_emailed_link_are_recognised(text):
    assert G.is_gate(text)


@pytest.mark.parametrize("text", ["", "Enter Your Information\nEmail\nNext", "Upload your resume", "Work Eligibility questions", "Review your application"])
def test_ordinary_pages_are_not_gates(text):
    assert not G.is_gate(text)


MAIL_TEXT = """Hello,
Thank you for your interest in Garmin. Click the link below to continue your application:
https://careers-garmin.icims.com/jobs/18475/login?mode=apply&token=abc123.
Unsubscribe: https://careers-garmin.icims.com/unsubscribe?x=1
Privacy policy https://www.icims.com/privacy
Logo https://images.icims.com/logo.png
Plain http link http://careers-garmin.icims.com/jobs/18475/login?token=zzz
Elsewhere https://evil.example.com/verify?token=abc
"""


def test_the_link_that_continues_the_application_is_chosen_and_nothing_else():
    assert G.pick_link(MAIL_TEXT, ALLOWED) == "https://careers-garmin.icims.com/jobs/18475/login?mode=apply&token=abc123"


@pytest.mark.parametrize("text", ["", "no links here", "http://careers-garmin.icims.com/x?token=1",
                                  "https://evil.example.com/verify?token=1", "https://www.icims.com/privacy https://x.icims.com/unsubscribe"])
def test_no_safe_link_means_none(text):
    assert G.pick_link(text, ALLOWED) is None


def mail(text, subject="Continue your application"):
    return I.Mail(uid="1", message_id="<1@x>", sender="Garmin <noreply@careers-garmin.icims.com>", subject=subject,
                  date=datetime.now(timezone.utc), text=text)


def test_the_classifier_takes_a_portal_mail_with_a_link_and_never_outcome_mail():
    classify = G.classifier(ALLOWED)
    v = classify(mail(MAIL_TEXT))
    assert v and v.kind == "link" and v.link.startswith("https://careers-garmin.icims.com/jobs/18475/login")
    assert classify(mail(MAIL_TEXT, subject="Thank you for applying: interview next steps")) is None      # protected outcome mail
    assert classify(mail("no link at all")) is None


def test_an_application_run_follows_at_most_two_emailed_links(monkeypatch):
    from jobagent.steps import apply as A
    calls = []
    monkeypatch.setattr(G, "follow", lambda page, log, since, posting_url="": calls.append(since) or True)
    env = {}
    results = [A._email_gate(object(), env, lambda m: None, "https://careers.garmin.com/jobs/1") for _ in range(4)]
    assert results == [True, True, False, False] and len(calls) == 2
