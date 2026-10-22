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


@pytest.fixture(scope="module")
def page():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        yield browser.new_page()
        browser.close()


def test_waits_for_late_rendering_email_field(page):
    page.set_content(DELAYED_IDENTIFIER)
    did = google_auth._step(page, EMAIL, "pw", None, log=lambda _: None)
    assert did == "entered email"
    assert page.locator("#identifierId").input_value() == EMAIL


def test_never_types_password_for_another_account(page):
    page.set_content(PASSWORD_FOR_OTHER_ACCOUNT)
    with pytest.raises(RuntimeError, match="refusing to type the password"):
        google_auth._step(page, EMAIL, "pw", None, log=lambda _: None)
    assert page.locator("input[type=password]").input_value() == ""
