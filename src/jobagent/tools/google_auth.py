"""Keep the agent's Google account signed in inside the persistent Chrome profile.

`ensure_session()` checks the session and, if needed, signs in with the vault credentials:
account chooser / email → password (only on a page that shows the agent's own address) →
2-Step Verification with a TOTP code generated from the authenticator setup key in the vault.
Challenges it can't answer itself (phone prompt, "verify it's you", Google refusing the browser)
are captured and handed to the user through HITL.
"""

import base64
import hashlib
import hmac
import re
import struct
import time
from datetime import UTC, datetime
from urllib.parse import urlparse

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from jobagent.config import get_settings
from jobagent.tools.browser import pause, screenshot, type_human
from jobagent.tools.hitl import Hitl
from jobagent.tools.vault import Vault

GMAIL_URL = "https://mail.google.com/mail/u/0/"
# Signed out, GMAIL_URL redirects to Google's Gmail marketing page, not to a sign-in form, so sign-in
# starts here instead. hl=en keeps page text English for the text-based checks below.
SIGNIN_URL = ("https://accounts.google.com/ServiceLogin?service=mail&hl=en"
              "&continue=https%3A%2F%2Fmail.google.com%2Fmail%2Fu%2F0%2F")
BLOCKED = re.compile(
    r"(?i)this browser or app may not be secure|could(?:n.t| not) sign you in|could(?:n.t| not) create your google account"
)


class SignInBlocked(RuntimeError):
    """Google refused to sign in from this browser; the agent does not work around this."""


def totp(secret_b32: str, at: float | None = None, digits: int = 6, step: int = 30) -> str:
    """RFC 6238 TOTP (SHA-1), as used by Google Authenticator."""
    key = base64.b32decode(secret_b32.replace(" ", "").upper() + "=" * (-len(secret_b32.replace(" ", "")) % 8))
    counter = int((time.time() if at is None else at) // step)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 10**digits
    return str(code).zfill(digits)


def _visible(page: Page, selector: str) -> bool:
    loc = page.locator(selector)
    return loc.count() > 0 and loc.first.is_visible()


def _click_next(page: Page) -> None:
    page.get_by_role("button", name=re.compile(r"^(Next|Continue|Verify)$", re.I)).first.click()


def _capture(page: Page, name: str, log) -> tuple[str, str]:
    shot = str(get_settings().state_dir / "logs" / f"{name}-{datetime.now(UTC):%H%M%S}.png")
    screenshot(page, shot)
    excerpt = " ".join(page.locator("body").inner_text(timeout=10_000).split())[:300]
    log(f"google-auth: page '{page.title()}' {urlparse(page.url).path} | {excerpt} | screenshot {shot}")
    return shot, excerpt


def signed_in(page: Page) -> bool:
    return urlparse(page.url).netloc in ("mail.google.com", "myaccount.google.com")


# Visible fields only: Google keeps hidden inputs with the same ids/types on later pages (e.g. a hidden
# id=identifierId on the password page), and matching those stalls a click for 30 s.
EMAIL_FIELD = "input[type=email]:visible, input[name=identifier]:not([type=hidden]):visible"
PASSWORD_FIELD = "input[type=password]:visible"
TOTP_FIELD = "input[name=totpPin]:visible, input#totpPin:visible"
SIGNIN_FIELDS = f"{EMAIL_FIELD}, {PASSWORD_FIELD}, {TOTP_FIELD}"


class PasswordRejected(RuntimeError):
    """Google showed the password field again after the password was submitted; never retry blindly."""


def _wait_gone(page: Page, selector: str, timeout_ms: int = 20_000) -> bool:
    """After submitting a field, wait until it is no longer visible (the page moved on)."""
    try:
        page.locator(selector).first.wait_for(state="hidden", timeout=timeout_ms)
        return True
    except PlaywrightTimeout:
        return False


def _wait_for_form(page: Page, timeout_ms: int = 10_000) -> None:
    """Google's sign-in pages render their fields after a 'Loading' phase; checking too early
    makes a normal page look unknown. Wait for a known field (or give up quietly)."""
    try:
        page.wait_for_load_state("domcontentloaded")
        page.locator(SIGNIN_FIELDS).first.wait_for(state="visible", timeout=timeout_ms)
    except PlaywrightTimeout:
        pass  # not a form page (chooser, challenge, landing page): the caller inspects it


def _step(page: Page, email: str, password: str, totp_secret: str | None, log, done: set[str] | None = None) -> str:
    """Perform at most one sign-in action on the current page; returns what it did ('' if nothing).

    `done` holds the actions already taken in this sign-in, so a page that comes back (rejected
    email/password) is reported instead of retried.
    """
    done = set() if done is None else done
    _wait_for_form(page)
    body = page.locator("body").inner_text(timeout=10_000)
    if BLOCKED.search(body):
        _capture(page, "google-signin-blocked", log)
        raise SignInBlocked("Google refused to sign in from this browser")
    # Account chooser (only on the actual chooser page: other pages also display the address).
    if re.search(r"(?i)choose an account", body):
        chooser = page.get_by_text(email, exact=False)
        if chooser.count():
            chooser.first.click()
            return "chose account"
    if _visible(page, EMAIL_FIELD):
        if "entered email" in done:
            return ""  # Google didn't accept the address: let the caller capture the message and ask
        type_human(page.locator(EMAIL_FIELD).first, email)
        _click_next(page)
        _wait_gone(page, EMAIL_FIELD)
        done.add("entered email")
        return "entered email"
    if _visible(page, PASSWORD_FIELD):
        # Only ever type the password where Google shows our own address.
        if email.lower() not in body.lower():
            raise RuntimeError(f"password prompt is not for {email}; refusing to type the password")
        if "entered password" in done:
            _capture(page, "google-signin-password-rejected", log)
            raise PasswordRejected(f"Google asked for the password of {email} again after it was entered; "
                                   "check it with `jobagent vault set gmail password` (not retrying, to avoid a lockout)")
        type_human(page.locator(PASSWORD_FIELD).first, password)
        _click_next(page)
        _wait_gone(page, PASSWORD_FIELD)
        done.add("entered password")
        return "entered password"
    if _visible(page, TOTP_FIELD):
        if not totp_secret or "entered authenticator code" in done:
            return ""
        type_human(page.locator(TOTP_FIELD).first, totp(totp_secret))
        _click_next(page)
        _wait_gone(page, TOTP_FIELD)
        done.add("entered authenticator code")
        return "entered authenticator code"
    # A product/landing page (e.g. the Gmail marketing page) with a "Sign in" link: follow it.
    if urlparse(page.url).netloc != "accounts.google.com":
        sign_in = page.get_by_role("link", name=re.compile(r"^\s*sign in\s*$", re.I))
        if sign_in.count() and sign_in.first.is_visible():
            sign_in.first.click()
            return "clicked Sign in on a landing page"
    if totp_secret:
        # 2SV defaulted to another method (phone prompt, SMS): switch to the authenticator app.
        another = page.get_by_role("button", name=re.compile(r"try another way", re.I))
        if not another.count():
            another = page.get_by_text(re.compile(r"try another way", re.I))
        if another.count():
            another.first.click()
            pause(1.5, 2.5)
            option = page.get_by_text(re.compile(r"authenticator app", re.I))
            if option.count():
                option.first.click()
                return "switched to authenticator app"
    return ""


def ensure_session(page: Page, vault: Vault, hitl: Hitl, log=print, max_steps: int = 12) -> str:
    """Make sure the agent's Google account is signed in; returns how (already / automatically / with help)."""
    entry = vault.get("gmail") or {}
    email, password = entry.get("email"), entry.get("password")
    if not email:
        raise RuntimeError("no gmail entry in the vault")
    page.goto(GMAIL_URL, wait_until="domcontentloaded")
    pause(2, 3)
    if signed_in(page):
        return "already signed in"
    page.goto(SIGNIN_URL, wait_until="domcontentloaded")
    pause(1.5, 2.5)
    if not password:
        hitl.ask(f"The agent needs the password of {email} to sign in. Store it with: "
                 "jobagent vault set gmail password — then answer 'done'.")
        password = vault.get("gmail").get("password")
    helped = False
    done: set[str] = set()
    for _ in range(max_steps):
        if signed_in(page):
            break
        url = page.url
        did = _step(page, email, password, entry.get("totp_secret"), log, done)
        if did:
            log(f"google-auth: {did}")
            pause(1, 2)  # _step already waited for its field to go away; let the next page settle
            continue
        # A challenge the agent can't answer itself.
        shot, excerpt = _capture(page, "google-signin-challenge", log)
        helped = True
        hitl.ask(f"Google sign-in for {email} needs you: {excerpt!r} (screenshot {shot}). Complete it in noVNC "
                 "(http://localhost:6080); it continues by itself, or answer 'done'.",
                 done_when=lambda: page.url != url)
    if not signed_in(page):
        _capture(page, "google-signin-failed", log)
        raise RuntimeError(f"could not sign in to {email} within {max_steps} steps")
    vault.set("gmail", last_signin=datetime.now(UTC).isoformat(timespec="seconds"))
    return "signed in with help" if helped else "signed in automatically"
