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


PASSWORD_PAGE = """<!doctype html><html><body><h1>Create a login</h1>
<label>Password <input type="password" name="pw" required></label><label>Password (Re-enter) <input type="password" name="pw2" required></label></body></html>"""


def test_empty_required_passwords_are_handed_to_the_candidate_and_the_run_continues_when_they_are_filled():
    from jobagent.steps import apply as A
    from jobagent.tools import forms
    class FakeHitl:
        def __init__(self, page): self.page, self.prompts = page, []
        def ask(self, prompt, done_when=None, timeout_s=None, **kw):
            self.prompts.append(prompt)
            assert done_when() is False               # still empty when asked
            for box in self.page.query_selector_all("input[type=password]"):
                box.fill("typed-by-the-candidate")      # the candidate types in the browser window
            assert done_when() is True
            return ""
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(channel="chrome", headless=True)
        except Exception:
            pytest.skip("no Chrome here")
        try:
            page = browser.new_page()
            page.route("**/*", lambda r: r.fulfill(status=200, content_type="text/html", body=PASSWORD_PAGE) if r.request.resource_type == "document" else r.abort())
            page.goto("https://portal.test/apply")
            view = forms.read_page(page)
            hitl = FakeHitl(page)
            env = {"hitl": hitl, "_experimental_blockers": ["Password", "Password (Re-enter)"], "ask_s": 5}
            logs = []
            assert A._candidate_types_passwords(page, view, env, logs.append) is True
            assert "never types or asks for a password" in hitl.prompts[0] and any("only the password field" in x for x in logs)
            env2 = {"hitl": hitl, "_experimental_blockers": ["Password", "Phone"], "ask_s": 5}       # something else is blocking too: not this hand-off
            assert A._candidate_types_passwords(page, view, env2, logs.append) is False
        finally:
            browser.close()


POSTING_PAGE = """<!doctype html><html><body><h1>Software Engineer (AWS Cloud Services)</h1><button>Apply for this job online</button></body></html>"""
PROFILE_PAGE = """<!doctype html><html><body><h1>Candidate profile</h1>""" + "".join(
    f'<label>Field {i} <input name="f{i}" type="text"></label>' for i in range(12)) + """<label>Password <input type="password"></label></body></html>"""
SIGN_IN_PAGE = """<!doctype html><html><body><h1>Welcome</h1><label>Username or email address <input name="u" type="text"></label>
<label>Password <input type="password"></label></body></html>"""


def _chrome_page(html):
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(channel="chrome", headless=True)
    except Exception:
        pw.stop()
        pytest.skip("no Chrome here")
    page = browser.new_page()
    page.route("**/*", lambda r: r.fulfill(status=200, content_type="text/html", body=html) if r.request.resource_type == "document" else r.abort())
    page.goto("https://portal.test/apply")
    return pw, browser, page


def test_candidate_start_waits_without_clicking_and_takes_over_when_the_application_page_shows():
    from jobagent.steps import apply as A
    pw, browser, page = _chrome_page(POSTING_PAGE)
    try:
        assert A._application_page_showing(page) is False
        class Candidate:
            prompts = []
            def ask(self, prompt, done_when=None, timeout_s=None, **kw):
                Candidate.prompts.append(prompt)
                assert done_when() is False                      # the agent has clicked nothing: still the posting page
                assert page.query_selector("button").inner_text() == "Apply for this job online"
                page.set_content(PROFILE_PAGE)                   # the candidate clicks through to the profile page
                assert done_when() is True
                return ""
        logs = []
        A._candidate_start(page, Candidate(), logs.append, timeout_s=5)
        assert "profile page" in Candidate.prompts[0] and any("clicks nothing" in x for x in logs)
        assert A._application_page_showing(page) is True
    finally:
        browser.close(); pw.stop()


def test_a_sign_in_page_is_not_the_application_page_and_a_missing_candidate_stops_the_run():
    from jobagent.steps import apply as A
    from jobagent.tools import challenge
    pw, browser, page = _chrome_page(SIGN_IN_PAGE)
    try:
        assert A._application_page_showing(page) is False
        with pytest.raises(challenge.Blocked, match="needs someone to answer"):
            A._candidate_start(page, None, print)
        class Silent:
            def ask(self, *a, **k): return ""
        with pytest.raises(challenge.Blocked, match="not reached"):
            A._candidate_start(page, Silent(), print, timeout_s=1)
    finally:
        browser.close(); pw.stop()


def test_candidate_start_is_refused_for_the_docker_browser():
    from typer.testing import CliRunner
    from jobagent.main import app
    out = CliRunner().invoke(app, ["apply", "outputs/none/0", "none", "--candidate-start"])
    assert out.exit_code != 0 and "--browser-mode desktop" in out.output
