"""F27: the account agent counts a rejected login once per submitted attempt, keeps the login lockout out of registration, handles a
native list, bounds form-alternating, stops on an existing account of unknown credentials, and waits out a loading page."""
import re
from types import SimpleNamespace

import pytest
from playwright.sync_api import sync_playwright

from jobagent import llm
from jobagent.tools import portal_agent as PA

PORTAL = """<html><body>
<div id=view></div>
<script>
const state = {mode: 'login', account: %(account)s, chosen: ''};
function render() {
  const v = document.getElementById('view');
  if (state.mode === 'login') {
    v.innerHTML = '<h1>Sign in</h1>' + (state.error ? '<div role=alert>' + state.error + '</div>' : '') +
      '<label>Email <input id=e type=email></label><label>Password <input id=p type=password></label>' +
      '<button id=go>Sign In</button> <a href="#" id=toreg>Create Account</a>';
    document.getElementById('go').onclick = () => { if (document.getElementById('p').value) { fetch('/login', {method: 'POST', body: 'user=jamie&password=' + encodeURIComponent(document.getElementById('p').value)}).then(() => { state.error = 'Invalid email address or password.'; render(); }); } };
    document.getElementById('toreg').onclick = (ev) => { ev.preventDefault(); state.mode = 'register'; render(); };
  } else if (state.mode === 'register') {
    v.innerHTML = '<h1>Create account</h1>' + (state.regerror ? '<div role=alert>' + state.regerror + '</div>' : '') +
      '<label>First Name <input id=fn></label><label>Last Name <input id=ln></label><label>Email <input id=re type=email></label>' +
      '<label>Country/Region Code <select id=cc><option>Select...</option><option>United Kingdom (+44)</option><option>United States (+1)</option></select></label>' +
      '<label>Phone <input id=ph></label><label>Password <input id=rp type=password></label><label>Confirm Password <input id=rp2 type=password></label>' +
      '<button id=mk>Create Account</button> <a href="#" id=tologin>Sign In</a>';
    document.getElementById('tologin').onclick = (ev) => { ev.preventDefault(); state.mode = 'login'; render(); };
    document.getElementById('mk').onclick = () => {
      if (state.account) { state.regerror = 'An account with this email already exists.'; render(); return; }
      if (document.getElementById('cc').value.startsWith('United States') && document.getElementById('rp').value) { state.mode = 'welcome'; render(); }
    };
  } else {
    v.innerHTML = '<h1>My Profile</h1><p>Signed in as ' + (document.getElementById('re') ? '' : '') + 'jamie@example.org</p><a href="#">Sign Out</a>';
  }
}
render();
</script></body></html>"""

VALUES = {"EMAIL": "jamie@example.org", "PASSWORD": "Shared-pw-1", "FIRST_NAME": "Jamie", "LAST_NAME": "Lee", "PHONE": "5551234567"}


def test_account_evidence_probe_stays_on_its_portal_and_restores_failed_probes(monkeypatch):
    class Page:
        url = "https://recruit.test/application"
        visits = []
        def evaluate(self, script):
            return ["https://corporate.test/dashboard", "https://recruit.test/profile"]
        def goto(self, url, **kwargs):
            self.visits.append(url)
            self.url = "https://identity.test/login" if url.endswith("/profile") else url
        def wait_for_timeout(self, ms):pass
        def go_back(self, **kwargs):self.url = "https://recruit.test/application"
    page = Page()
    monkeypatch.setattr(PA, "_shows_account", lambda page, values: page.url.startswith("https://identity.test"))
    assert not PA.signed_in_evidence(page, VALUES)
    assert page.visits == ["https://recruit.test/profile"]
    assert page.url == "https://recruit.test/application"


@pytest.mark.parametrize("password,sign_out,email,expected", [
    (False, True, "jamie@example.org", True),
    (True, True, "jamie@example.org", False),
    (False, False, "jamie@example.org", False),
    (False, True, "somebody@example.org", False),
])
def test_account_evidence_reads_profile_values_without_trusting_a_signout_link(password,sign_out,email,expected):
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel="chrome",headless=True)
        try:
            page=browser.new_page()
            page.set_content('<h1>My Profile</h1><input type=email value="'+email+'">'+
                             ('<button>Sign Out</button>' if sign_out else '')+
                             ('<input type=password>' if password else ''))
            assert PA._shows_account(page,VALUES) is expected
        finally:browser.close()


@pytest.mark.parametrize('email_type,password,expected',[('text',False,True),('hidden',False,False),('text',True,False)])
def test_collapsed_profile_information_counts_but_hidden_metadata_or_login_password_do_not(email_type,password,expected):
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True)
        try:
            page=browser.new_page()
            page.set_content('<h2>Profile Information</h2><button>Sign Out</button><section hidden>'
                             '<input name=contactEmail type="'+email_type+'" value="jamie@example.org">'+
                             ('<input type=password>' if password else '')+'</section>')
            assert PA._shows_account(page,VALUES) is expected
        finally:browser.close()


@pytest.mark.parametrize("close", [
    '<button title="Close cookie notice" onclick="notice.remove()">×</button>',
    '<button onclick="notice.remove()"><img alt="Close cookie notice" width=20 height=20></button>',
    '<button id="cookie-notice-close" onclick="notice.remove()"> </button>',
])
def test_auth_snapshot_exposes_cookie_obstruction_and_icon_close(close):
    """A visible sign-in button can be covered; its blocking notice must reach the account agent."""
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        try:
            page = browser.new_page()
            page.set_content('<button id="signin" style="width:120px;height:40px"><span>Sign In</span></button>'
                             '<div id="notice" style="position:fixed;inset:0;z-index:100;background:white">'
                             '<p>Cookie preferences</p>' + close + '</div>')
            snap = page.evaluate(PA.SNAPSHOT_JS)
            signin = next(x for x in snap["items"] if x["label"] == "Sign In")
            assert signin["blocked_by"]
            view, frames = PA.snapshot(page)
            assert "BLOCKED_BY=" in view
            close_index = next(i for i, x in enumerate(snap["items"])
                               if "close" in x["label"].lower())
            assert not snap["items"][close_index]["blocked_by"]
            PA._target(frames, PA.AuthAction(thought="Dismiss the blocking notice", action="click",
                                             element=close_index)).click()
            after = page.evaluate(PA.SNAPSHOT_JS)
            assert not next(x for x in after["items"] if x["label"] == "Sign In")["blocked_by"]
        finally:
            browser.close()


class Driver:
    """Stands in for the model: follows a script of (action, label-fragment, value, frame text condition)."""
    def __init__(self, script):
        self.script, self.calls, self.views = list(script), 0, []

    def __call__(self, agent, schema, msgs, first=None, log=None):
        view = msgs[-1].content
        self.views.append(view)
        self.calls += 1
        step = self.script.pop(0) if self.script else ("failed", None, "")
        action, frag, value = step
        element = None
        if frag is not None:
            for m in re.finditer(r'^\s*\[(\d+)\] (\S+) "([^"]*)"', view, re.M):
                if frag.lower() in m.group(3).lower():
                    element = int(m.group(1))
                    break
            assert element is not None, f"no element {frag!r} in:\n{view}"
        return schema(thought="scripted", action=action, element=element, frame=0, value=value)


def run_portal(script, account="false", monkeypatch=None, hitl=None):
    asked = []
    hitl = hitl or SimpleNamespace(ask=lambda q, **k: asked.append(q) or "done")
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(channel="chrome", headless=True)
        except Exception:
            pytest.skip("no Chrome here")
        try:
            page = browser.new_page()
            page.route("**/*", lambda r: r.fulfill(status=200, content_type="text/html", body=PORTAL % {"account": account})
                       if r.request.resource_type == "document" else r.fulfill(status=401, body="no") if r.request.method == "POST"
                       else r.abort())
            page.goto("https://portal.test/")
            real = page.wait_for_timeout
            page.wait_for_timeout = lambda ms: real(min(ms, 60))
            driver = Driver(script)
            monkeypatch.setattr(llm, "writer", lambda *a, **k: object())
            monkeypatch.setattr(llm, "structured_invoke", driver)
            monkeypatch.setattr(PA, "signed_in_evidence", lambda page, values: "Signed in as" in page.locator("body").inner_text())
            out = PA.run(page, "https://portal.test/", dict(VALUES), None, hitl, "portal.test", log=lambda m: None)
            return out, driver, asked
        finally:
            browser.close()


LOGIN = [("fill", "Email", "{EMAIL}"), ("fill", "Password", "{PASSWORD}"), ("click", "Sign In", "")]


def test_one_unchanged_rejection_read_many_times_counts_once_and_registration_can_use_the_password(monkeypatch):
    script = LOGIN + [("wait", None, ""), ("wait", None, ""), ("wait", None, ""),      # the SAME error text, read three more times
                      ("click", "Create Account", ""),
                      ("fill", "First Name", "{FIRST_NAME}"), ("fill", "Last Name", "{LAST_NAME}"), ("fill", "Email", "{EMAIL}"),
                      ("fill", "Country", "United States (+1)"),                       # a native list: filled as if it were a text box
                      ("fill", "Phone", "{PHONE}"), ("fill", "Password", "{PASSWORD}"), ("fill", "Confirm", "{PASSWORD}"),
                      ("click", "Create Account", ""), ("signed_in", None, "")]
    out, driver, _ = run_portal(script, monkeypatch=monkeypatch)
    steps = " | ".join(out["steps"])
    assert out["state"] == "signed_in", steps
    assert "refused: the password was already rejected" not in steps                    # the registration form could set its password
    assert "CHOICES=" in " ".join(driver.views)                                          # the snapshot showed the list's options
    assert any(s.startswith("select United States (+1)") for s in out["steps"])         # and the fill became a select


def test_two_real_rejected_login_submissions_stop_further_login_password_fills(monkeypatch):
    script = LOGIN + [("fill", "Password", "{PASSWORD}"), ("click", "Sign In", ""),           # a second real submission, rejected again
                      ("fill", "Password", "{PASSWORD}"), ("failed", None, "")]
    out, _, _ = run_portal(script, monkeypatch=monkeypatch)
    steps = " | ".join(out["steps"])
    assert "refused: the password was already rejected twice on sign-in" in steps


def test_a_single_rejection_does_not_ban_the_password(monkeypatch):
    script = LOGIN + [("fill", "Password", "{PASSWORD}"), ("failed", None, "")]
    out, _, _ = run_portal(script, monkeypatch=monkeypatch)
    assert "refused: the password was already rejected" not in " | ".join(out["steps"])


def test_alternating_between_the_two_forms_is_bounded_and_ends_in_a_clear_handoff(monkeypatch):
    script = [("click", "Create Account", ""), ("click", "Sign In", "")] * 6
    out, _, _ = run_portal(script, monkeypatch=monkeypatch)
    assert out["state"] == "failed" and "alternating between its sign-in and create-account" in out["reason"]


def test_an_existing_account_with_unknown_credentials_is_not_silently_reset(monkeypatch):
    script = LOGIN + [("click", "Create Account", ""), ("fill", "First Name", "{FIRST_NAME}"), ("fill", "Last Name", "{LAST_NAME}"),
                      ("fill", "Email", "{EMAIL}"), ("fill", "Country", "United States (+1)"), ("fill", "Password", "{PASSWORD}"),
                      ("fill", "Confirm", "{PASSWORD}"), ("click", "Create Account", ""), ("wait", None, "")]
    out, _, _ = run_portal(script, account="true", monkeypatch=monkeypatch)
    assert out["state"] == "failed" and "already exists" in out["reason"] and "not done automatically" in out["reason"]
    assert not any("reset" in s.lower() and s.startswith("click") for s in out["steps"])


def test_a_loading_page_is_waited_out_not_handed_to_the_user_as_a_puzzle():
    class Page:
        def __init__(self):
            self.n = 0

        def wait_for_timeout(self, ms):
            self.n += 1

    calls = {"n": 0}
    from jobagent.tools import challenge

    def detect(page):
        calls["n"] += 1
        return challenge.Challenge("unknown", "u", "Page is blank or transitioning") if calls["n"] < 4 else None
    real = challenge.detect
    challenge.detect = detect
    try:
        assert PA._settle_unknown(Page(), seconds=10) is None and calls["n"] == 4          # it settled; nobody was asked
        calls["n"] = -100
        assert PA._settle_unknown(Page(), seconds=3).kind == "unknown"                       # one that never settles is still reported
    finally:
        challenge.detect = real


def test_registration_is_recognised_by_its_form_not_by_a_link_to_it():
    login = "FRAME 0 u\nTEXT: Sign in | Create Account\nELEMENTS:\n  [0] input/email \"Email\"\n  [1] input/password \"Password\"\n  [2] button \"Sign In\""
    reg = "FRAME 0 u\nTEXT: x\nELEMENTS:\n  [0] input/text \"First Name\"\n  [1] input/password \"Password\"\n  [2] input/password \"Confirm Password\""
    assert not PA.is_registration(login) and PA.is_registration(reg)


def test_a_non_submit_click_after_a_password_fill_is_not_another_rejected_login(monkeypatch):
    """Codex's counterexample: one rejected Sign In, refill the password, press an inert 'Show Password' button, refill: the old
    banner is still on the page and must not be counted again."""
    global PORTAL
    original = PORTAL
    PORTAL = original.replace("<button id=go>Sign In</button>", "<button type=button id=show onclick=\"fetch('/analytics', {method: 'POST', body: 'clicked=show'})\">Show Password</button><button id=go>Sign In</button>")
    try:
        script = LOGIN + [("fill", "Password", "{PASSWORD}"), ("click", "Show Password", ""), ("fill", "Password", "{PASSWORD}"),
                          ("failed", None, "")]
        out, _, _ = run_portal(script, monkeypatch=monkeypatch)
    finally:
        PORTAL = original
    assert not any("refused: the password was already rejected" in s for s in out["steps"]), out["steps"]


def test_unrelated_successful_page_traffic_is_not_a_second_login(monkeypatch):
    """Codex's round-4 case: Show Password sends only POST /analytics (HTTP 204); the old rejection banner is unchanged."""
    global PORTAL
    original = PORTAL
    PORTAL = original.replace("<button id=go>Sign In</button>", "<button type=button id=show onclick=\"fetch('/analytics', {method: 'POST', body: 'x'})\">Show Password</button><button id=go>Sign In</button>")
    try:
        script = LOGIN + [("fill", "Password", "{PASSWORD}"), ("click", "Show Password", ""), ("fill", "Password", "{PASSWORD}"), ("failed", None, "")]
        out, _, _ = run_portal(script, monkeypatch=monkeypatch)
    finally:
        PORTAL = original
    assert not any("refused: the password was already rejected" in s for s in out["steps"]), out["steps"]


def test_a_failed_unrelated_request_and_a_late_old_banner_are_not_a_second_login(monkeypatch):
    global PORTAL
    original = PORTAL
    PORTAL = original.replace("<button id=go>Sign In</button>", "<button type=button id=show onclick=\"fetch('/beacon', {method: 'POST', body: 'x'}).catch(() => {})\">Show Password</button><button id=go>Sign In</button>")
    try:
        script = LOGIN + [("fill", "Password", "{PASSWORD}"), ("click", "Show Password", ""), ("wait", None, ""), ("wait", None, ""),
                          ("fill", "Password", "{PASSWORD}"), ("failed", None, "")]
        out, _, _ = run_portal(script, monkeypatch=monkeypatch)
    finally:
        PORTAL = original
    assert not any("refused: the password was already rejected" in s for s in out["steps"]), out["steps"]


def test_the_listener_records_only_whether_credentials_were_sent_never_the_password(monkeypatch):
    out, driver, _ = run_portal(LOGIN + [("failed", None, "")], monkeypatch=monkeypatch)
    assert not any(VALUES["PASSWORD"] in s for s in out["steps"])                     # masked: the history holds placeholders only
    assert not any(VALUES["PASSWORD"] in v for v in driver.views)
