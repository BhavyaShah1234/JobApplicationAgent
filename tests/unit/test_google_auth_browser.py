"""Sign-in step logic against local fake pages in headless Chrome (no network, no Google)."""

import shutil

import pytest

from jobagent.tools import google_auth

pytestmark = pytest.mark.skipif(shutil.which("google-chrome") is None, reason="needs Chrome (run in the container)")

EMAIL = "agent@gmail.com"

# Like Google's identifier page: "Loading" first, the email field renders later.
DELAYED_IDENTIFIER = """
<body>Loading
<script>
setTimeout(() => {
  document.body.innerHTML = '<input type="email" id="identifierId"><button>Next</button>';
}, 1500);
</script>
</body>
"""

PASSWORD_FOR_OTHER_ACCOUNT = '<body>someone.else@gmail.com <input type="password"><button>Next</button></body>'

# Shaped like Google's real flow: text field name=identifier, an overlay during the transition, and a
# *hidden* id=identifierId left on the password page. `reject` makes the password page come back.
TWO_PAGE_SIGNIN = """
<body>
<div id="page"><input type="text" name="identifier" id="identifierId"><button id="next">Next</button></div>
<script>
const reject = %s;
let stage = 'email';
document.addEventListener('click', e => {
  if (e.target.id !== 'next') return;
  const overlay = document.createElement('div');
  overlay.style = 'position:fixed;inset:0;z-index:9';
  document.body.appendChild(overlay);
  setTimeout(() => {
    overlay.remove();
    if (stage === 'email' || reject) {
      stage = 'password';
      document.getElementById('page').innerHTML =
        '<p>agent@gmail.com</p>' + (reject && stage === 'password' && window.tried ? '<p>Wrong password</p>' : '') +
        '<input type="hidden" id="identifierId" name="identifierInput">' +
        '<input type="password" name="Passwd"><button id="next">Next</button>';
      if (document.querySelector('input[type=password]')) window.tried = (window.tried || 0) + (window.sawPw ? 1 : 0);
      window.sawPw = true;
    } else {
      document.getElementById('page').innerHTML = '<p>Welcome</p>';
    }
  }, 800);
});
</script>
</body>
"""


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        yield b
        b.close()


@pytest.fixture
def page(browser):
    # A fresh tab per test: set_content keeps the JS realm, so top-level consts would collide.
    p = browser.new_page()
    yield p
    p.close()


def test_waits_for_late_rendering_email_field(page):
    page.set_content(DELAYED_IDENTIFIER)
    did = google_auth._step(page, EMAIL, "pw", None, log=lambda _: None)
    assert did == "entered email"
    assert page.locator("#identifierId").input_value() == EMAIL


def test_email_then_password_without_retyping_across_the_transition(page):
    page.set_content(TWO_PAGE_SIGNIN % "false")
    done: set[str] = set()
    log = lambda _: None  # noqa: E731
    assert google_auth._step(page, EMAIL, "pw", None, log, done) == "entered email"
    assert google_auth._step(page, EMAIL, "pw", None, log, done) == "entered password"
    assert page.locator("body").inner_text().strip() == "Welcome"


def test_rejected_password_is_not_retried(page, monkeypatch):
    monkeypatch.setattr(google_auth, "_capture", lambda *a, **k: ("shot.png", "Wrong password"))
    page.set_content(TWO_PAGE_SIGNIN % "true")
    done: set[str] = set()
    log = lambda _: None  # noqa: E731
    assert google_auth._step(page, EMAIL, "pw", None, log, done) == "entered email"
    assert google_auth._step(page, EMAIL, "pw", None, log, done) == "entered password"
    with pytest.raises(google_auth.PasswordRejected):
        google_auth._step(page, EMAIL, "pw", None, log, done)


def test_never_types_password_for_another_account(page):
    page.set_content(PASSWORD_FOR_OTHER_ACCOUNT)
    with pytest.raises(RuntimeError, match="refusing to type the password"):
        google_auth._step(page, EMAIL, "pw", None, log=lambda _: None)
    assert page.locator("input[type=password]").input_value() == ""
