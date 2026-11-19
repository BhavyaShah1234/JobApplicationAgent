"""Human verification after dispatch must preserve the attempt, never click again."""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from jobagent.steps import submit
from jobagent.tools import challenge


class Clock:
    seconds = 0

    @classmethod
    def now(cls):
        return datetime(2026, 11, 18) + timedelta(seconds=cls.seconds)


class Page:
    text = "Application form"
    url = 'https://jobs.example.com/apply'
    frames = []

    def title(self):
        return 'Application'

    def locator(self, selector):
        assert selector == 'body'
        return SimpleNamespace(inner_text=lambda **kwargs: self.text)

    def wait_for_timeout(self, milliseconds):
        Clock.seconds += milliseconds / 1000


def setup_clock(monkeypatch):
    Clock.seconds = 0
    monkeypatch.setattr(submit, "datetime", Clock)
    monkeypatch.setattr(submit, "page_text", lambda page: page.text)
    monkeypatch.setattr(submit.forms, "read_page", lambda page: SimpleNamespace(errors=[]))
    monkeypatch.setattr(challenge, "page_ready", lambda page: page.text == "Application form")
    monkeypatch.setattr(submit, "screenshot", lambda *args: None)
    return Page()


def test_human_wait_can_exceed_receipt_timeout_and_finish_on_receipt(monkeypatch, tmp_path):
    page = setup_clock(monkeypatch)
    calls = []

    def human_guard(p, *, ready):
        calls.append(1)
        Clock.seconds += 120
        p.text = "Thank you for applying"
        assert ready(p)  # receipt pages need not have any form controls
        return True

    monkeypatch.setattr(challenge, "guard", human_guard)
    assert submit.submitted_page(page, "Application form", tmp_path / "receipt.png") == (
        True, "Thank you for applying", [])
    assert calls == [1]


def test_challenge_clearance_without_receipt_is_not_submission_success(monkeypatch, tmp_path):
    page = setup_clock(monkeypatch)
    calls = []

    def human_guard(p, *, ready):
        calls.append(1)
        if len(calls) == 1:
            Clock.seconds += 120
            assert ready(p)
            return True
        return False

    monkeypatch.setattr(challenge, "guard", human_guard)
    assert submit.submitted_page(page, "Application form", tmp_path / "receipt.png") == (False, "", [])
    assert len(calls) > 1 and Clock.seconds >= 180
    # This fake has no click API: no second Submit may be attempted here.


def test_skipped_post_dispatch_verification_propagates_as_unresolved(monkeypatch, tmp_path):
    page = setup_clock(monkeypatch)

    def skipped(*args, **kwargs):
        raise challenge.Blocked("Human skipped verification")

    monkeypatch.setattr(challenge, "guard", skipped)
    with pytest.raises(challenge.Blocked, match="skipped"):
        submit.submitted_page(page, "Application form", tmp_path / "receipt.png")


def test_emailed_application_code_uses_human_handoff_in_existing_attempt(monkeypatch, tmp_path):
    page = setup_clock(monkeypatch)
    page.text = ('Job description. ' * 400 +
                 'A verification code was sent to applicant@example.test. '
                 "To submit your application, enter the 8-character code to confirm you're a human.")
    calls = []
    with challenge.session(page, None) as controller:
        def human_verification(p, block):
            calls.append(block.kind)
            assert block.kind == 'application-email-code'
            Clock.seconds += 120  # a person can take longer than the ordinary receipt timeout
            p.text = 'Thank you for applying'
            assert controller.ready(p)

        monkeypatch.setattr(controller, 'wait', human_verification)
        assert submit.submitted_page(page, 'Application form', tmp_path / 'receipt.png') == (
            True, 'Thank you for applying', [])
    assert calls == ['application-email-code']
    # Page has no fill/click API: the agent must not enter a code or click Submit again.


@pytest.mark.parametrize("blocked", [False, True])
def test_readiness_override_preserves_policy_and_restores_session_ready(monkeypatch, blocked):
    page = Page()
    events = []
    normal_ready = lambda p: False
    receipt_ready = lambda p: True
    monkeypatch.setattr(challenge, "detect", lambda p: challenge.Challenge("hcaptcha", "https://example.com/apply", ""))
    with challenge.session(page, None, ready=normal_ready, policy=lambda p: events.append("policy")) as controller:
        def wait(p, block):
            events.append("human")
            assert controller.ready is receipt_ready
            if blocked:
                raise challenge.Blocked("unresolved")

        monkeypatch.setattr(controller, "wait", wait)
        if blocked:
            with pytest.raises(challenge.Blocked):
                challenge.guard(page, ready=receipt_ready)
        else:
            assert challenge.guard(page, ready=receipt_ready)
        assert controller.ready is normal_ready
    assert events == ["policy", "human"]
