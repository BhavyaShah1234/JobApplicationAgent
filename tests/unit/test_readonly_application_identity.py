"""Account proof on an application and goal-bound account navigation."""
import pytest
from playwright.sync_api import sync_playwright

from jobagent import llm
from jobagent.tools import portal_agent as auth

VALUES = {"EMAIL": "jamie@example.org", "FIRST_NAME": "Jamie"}


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        yield b
        b.close()


@pytest.mark.parametrize("variant,expected", [
    ("readonly", True), ("disabled", True), ("editable", False),
    ("wrong-email", False), ("hidden", False), ("password", False),
    ("no-profile", False), ("no-applications", False), ("foreign-profile", False),
    ("foreign-applications", False), ("sibling-profile", True),
    ("lookalike-profile", False), ("iframe-only", False), ("status-heading", True),
])
def test_readonly_application_email_requires_account_navigation(browser, variant, expected):
    page = browser.new_page()
    try:
        email = "other@example.org" if variant == "wrong-email" else VALUES["EMAIL"]
        attrs = "disabled" if variant == "disabled" else "" if variant == "editable" else "readonly"
        if variant == "hidden": attrs += ' style="display:none"'
        profile = "https://other.example.net/profile" if variant == "foreign-profile" else \
                  "https://www.recruit.example.com/profile" if variant == "sibling-profile" else \
                  "https://recruit.example.com.evil.net/profile" if variant == "lookalike-profile" else "/profile"
        apps = "https://other.example.net/applications" if variant == "foreign-applications" else "/applications"
        html = '<h1>Apply</h1><h2>Contact information</h2>'
        if variant == "status-heading":
            html = '<h1>Apply</h1><h2>Contact information<br>Auto-save unavailable for this section</h2>'
        if variant != "no-profile": html += f'<a href="{profile}">Profile</a>'
        if variant != "no-applications": html += f'<a href="{apps}">Applications</a>'
        html += f'<label>Email address<input type=email value="{email}" {attrs}></label>'
        if variant == "password": html += '<input type=password>'
        if variant == "iframe-only": html = '<iframe srcdoc="' + html.replace('"', '&quot;') + '"></iframe>'
        page.route("**/*", lambda r: r.fulfill(status=200, content_type="text/html", body=html))
        page.goto("https://account.recruit.example.com/application/123")
        assert auth._shows_account(page, VALUES) is expected
    finally:
        page.close()


@pytest.mark.parametrize("goal,clicks,state", [("sign_in", 0, "failed"), ("sign_out", 1, "signed_out")])
def test_auth_agent_can_only_click_signout_for_a_signout_task(browser, monkeypatch, goal, clicks, state):
    page = browser.new_page()
    try:
        page.route("**/*", lambda r: r.fulfill(status=200, content_type="text/html", body='''
            <h1>Account</h1><button onclick="window.logoutClicks++">Sign out</button>
            <script>window.logoutClicks=0</script>'''))
        page.goto("https://account.recruit.example.com/")
        real_wait = page.wait_for_timeout
        page.wait_for_timeout = lambda ms: real_wait(min(ms, 5))
        actions = iter([auth.AuthAction(thought="Inspect account", action="click", element=0),
                        auth.AuthAction(thought="Stop", action="failed")])
        monkeypatch.setattr(llm, "writer", lambda *a, **k: object())
        monkeypatch.setattr(llm, "structured_invoke", lambda *a, **k: next(actions))
        monkeypatch.setattr(auth, "_settle_unknown", lambda *a, **k: None)
        out = auth.run(page, page.url, VALUES, None, None, "account.recruit.example.com", goal=goal, log=lambda _: None)
        assert page.evaluate("window.logoutClicks") == clicks
        assert out["state"] == state
    finally:
        page.close()
