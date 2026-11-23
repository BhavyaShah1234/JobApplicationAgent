"""Real Chrome: the gate page is recognised, the portal's mail is read from a (fake) inbox, its link is opened in the same browser."""
from datetime import datetime, timezone

import pytest
from playwright.sync_api import sync_playwright

from jobagent.tools import email_gate as G
from jobagent.tools import inbox as I

GATE_HTML = "<!doctype html><html><body><h1>Check your email</h1><p>Please verify your email account with us by clicking on the link we have just sent to a@b.com.</p></body></html>"
NEXT_HTML = "<!doctype html><html><body><h1>Application</h1><label>Phone <input name=phone></label><button>Next</button></body></html>"


class FakeInbox:
    def __init__(self, text):
        self.mail = I.Mail(uid="9", message_id="<9@x>", sender="Garmin <noreply@careers-garmin.icims.com>", subject="Continue your application",
                           date=datetime.now(timezone.utc), text=text)
        self.used, self.asked = [], {}

    def wait_for_verification(self, site, since, kind="portal", timeout_s=300, classify=None, also_from=(), **kw):
        self.asked = {"site": site, "kind": kind, "also_from": also_from}
        v = classify(self.mail)
        if v is None:
            raise TimeoutError("no mail")
        return self.mail, v

    def mark_used(self, mail, evidence=""):
        self.used.append(evidence)
        return True


def run(inbox, gate_html=GATE_HTML):
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(channel="chrome", headless=True)
        except Exception:
            pytest.skip("no Chrome here")
        try:
            page = browser.new_page()
            seen = []

            def route(r):
                seen.append(r.request.url)
                if r.request.resource_type != "document":
                    return r.abort()
                body = NEXT_HTML if "token=abc" in r.request.url else gate_html
                r.fulfill(status=200, content_type="text/html", body=body)
            page.route("**/*", route)
            page.goto("https://login.icims.com/u/login/identifier")
            logs = []
            opened = G.follow(page, logs.append, datetime.now(timezone.utc), "https://careers.garmin.com/jobs/18475", timeout_s=5, inbox=inbox)
            return opened, page.inner_text("body"), seen, logs
        finally:
            browser.close()


def test_the_emailed_link_is_opened_in_the_same_browser_and_the_mail_is_marked_used():
    inbox = FakeInbox("Click the link to continue:\nhttps://careers-garmin.icims.com/jobs/18475/login?token=abc\nhttps://www.icims.com/privacy")
    opened, text, seen, logs = run(inbox)
    assert opened and "Phone" in text
    assert any("token=abc" in u for u in seen) and not any("privacy" in u for u in seen)
    assert inbox.used and inbox.asked["kind"] == "portal" and inbox.asked["also_from"] == ("https://careers.garmin.com/jobs/18475",)
    assert any("emailed link" in x for x in logs)


def test_a_page_that_is_not_waiting_for_a_link_is_left_alone():
    inbox = FakeInbox("https://careers-garmin.icims.com/jobs/18475/login?token=abc")
    opened, text, seen, _ = run(inbox, gate_html="<!doctype html><html><body><h1>Enter your phone</h1></body></html>")
    assert not opened and not inbox.asked


def test_no_mail_in_time_means_the_run_stops_as_before():
    inbox = FakeInbox("no link in this mail at all")
    opened, text, seen, logs = run(inbox)
    assert not opened and any("no usable mail" in x for x in logs)
