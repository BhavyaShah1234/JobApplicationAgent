"""Step 11: sign in to a job portal with the agent's email, creating the account the first time.

    ensure_account(page, url) -> PortalLogin

Credentials live in the vault as `portal:<host>` (email, password, platform, created, verified). With them the agent
signs in; without them it creates an account with a generated password (saved before the form is submitted, so a
crash can't lose it), confirms the email (the verification mail is matched by sender and time, used, then moved to
Trash with an audit row in verification_log), and signs in. A challenge (CAPTCHA, bot check) is handed to the user
in noVNC; nothing is solved automatically. Only the fields a person would fill are filled.

Platforms: Workday (per-employer tenants, e.g. adobe.wd5.myworkdayjobs.com). Others are added as adapters.
"""

import re
import secrets
import string
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urlparse

from playwright.sync_api import Page

from jobagent import db
from jobagent.tools import challenge
from jobagent.tools.hitl import Hitl
from jobagent.tools.memory import PortalMemory, portal_key
from jobagent.tools.vault import Vault


class PortalError(RuntimeError):
    pass


@dataclass
class PortalLogin:
    host: str
    platform: str
    created: bool = False          # a new account was made in this run
    verified_mail: bool = False    # a verification mail was used (and trashed)
    reset: bool = False            # the password had to be reset
    signed_in: bool = False
    notes: list[str] = field(default_factory=list)


def new_password(length: int = 18) -> str:
    """Upper, lower, digit and a common special character (portals' usual rules), no ambiguous characters."""
    pools = [string.ascii_uppercase, string.ascii_lowercase, string.digits, "!@#$%^*"]
    chars = [secrets.choice(p) for p in pools]
    alphabet = "".join(pools)
    chars += [secrets.choice(alphabet) for _ in range(length - len(chars))]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def platform_of(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if "myworkdayjobs.com" in host or "myworkdaysite.com" in host:
        return "workday"
    if "icims.com" in host:
        return "icims"
    return "unknown"


# --- Workday ---------------------------------------------------------------------------------------------------

def _wd_site(url: str) -> str:
    """The tenant's career site root: https://adobe.wd5.myworkdayjobs.com/en-US/external_experienced."""
    u = urlparse(url)
    path = u.path.split("/job/")[0].rstrip("/")
    return f"{u.scheme}://{u.netloc}{path}"


def _wd_click(page: Page, automation_id: str) -> None:
    """Workday covers its submit buttons with a 'click_filter' layer; click that layer within the button's wrapper."""
    btn = page.locator(f'[data-automation-id="{automation_id}"]').first
    layer = btn.locator("xpath=..").locator('[data-automation-id="click_filter"]')
    (layer.first if layer.count() else btn).click()


def _wd_signed_in(page: Page) -> bool:
    return page.locator('[data-automation-id="utilityButtonSignIn"]').filter(visible=True).count() == 0 and \
        page.locator('[data-automation-id="accountSettingsButton"], [data-automation-id="utilityButtonAccount"], '
                     'button[aria-label*="Account" i]').filter(visible=True).count() > 0


def _wd_open_email_form(page: Page) -> None:
    page.get_by_role("button", name=re.compile(r"^sign in$", re.I)).filter(visible=True).first.click()
    page.wait_for_timeout(2500)
    by_email = page.get_by_role("button", name=re.compile(r"sign in with email", re.I))
    if by_email.count():
        by_email.first.click()
        page.wait_for_timeout(2000)
    page.locator('[data-automation-id="email"]').first.wait_for(timeout=15000)


def _wd_error(page: Page) -> str:
    errs = page.locator('[data-automation-id="errorMessage"], [data-automation-id*="Alert" i], [role="alert"]')
    return " ".join(e.inner_text().strip() for e in errs.filter(visible=True).all())[:300]


def _wd_sign_in(page: Page, email: str, password: str) -> str:
    """'' if signed in, else the page's error text."""
    _wd_open_email_form(page)
    page.locator('[data-automation-id="email"]').first.fill(email)
    page.locator('[data-automation-id="password"]').first.fill(password)
    _wd_click(page, "signInSubmitButton")
    for _ in range(20):
        page.wait_for_timeout(1000)
        if _wd_signed_in(page):
            return ""
        if err := _wd_error(page):
            return err
    return "no result after signing in"


def _wd_create(page: Page, email: str, password: str) -> str:
    """Submit Create Account; '' when accepted, else the error text."""
    _wd_open_email_form(page)
    page.locator('[data-automation-id="createAccountLink"]').first.click()
    page.locator('[data-automation-id="verifyPassword"]').first.wait_for(timeout=15000)
    page.locator('[data-automation-id="email"]').first.fill(email)
    page.locator('[data-automation-id="password"]').first.fill(password)
    page.locator('[data-automation-id="verifyPassword"]').first.fill(password)
    box = page.locator('[data-automation-id="createAccountCheckbox"]')  # some tenants ask to accept terms
    if box.count() and box.first.is_visible() and not box.first.is_checked():
        box.first.check()
    _wd_click(page, "createAccountSubmitButton")
    for _ in range(25):
        page.wait_for_timeout(1000)
        if _wd_signed_in(page):
            return ""
        text = page.locator("body").inner_text()
        if re.search(r"(?i)verif(y|ication).{0,60}(email|account)|check your (e-?mail|inbox)", text):
            return ""
        if err := _wd_error(page):
            return err
    return "no result after creating the account"


# --- the flow (platform-independent) --------------------------------------------------------------------------

ADAPTERS = {"workday": (_wd_site, _wd_sign_in, _wd_create, _wd_signed_in)}


def _confirm_email(page: Page, inbox, host: str, since: datetime, log, purpose: str) -> bool:
    """Use the portal's verification mail (a link opened in this browser, or a code typed into the page)."""
    try:
        mail, v = inbox.wait_for_verification(f"https://{host}", since, kind="portal", timeout_s=300)
    except TimeoutError:
        log(f"portal {host}: no verification mail within 5 min")
        return False
    if v.link:
        page.goto(v.link, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
    elif v.code:
        box = page.locator('input[autocomplete="one-time-code"], input[name*="code" i], input[id*="code" i]')
        if not box.count():
            log(f"portal {host}: got a code but found no field for it")
            return False
        box.first.fill(v.code)
        page.keyboard.press("Enter")
        page.wait_for_timeout(3000)
    inbox.consume_and_trash(mail, purpose)
    log(f"portal {host}: email confirmed; verification mail trashed ({mail.subject!r})")
    return True


def ensure_account(page: Page, url: str, vault: Vault, inbox, hitl: Hitl, email: str, log=print) -> PortalLogin:
    """Signed in to the portal of `url` with the agent's account (created the first time)."""
    platform = platform_of(url)
    if platform not in ADAPTERS:
        raise PortalError(f"no adapter for {urlparse(url).netloc} ({platform})")
    site, sign_in, create, signed_in = ADAPTERS[platform]
    host = urlparse(url).netloc.lower()
    key = f"portal:{host}"
    res = PortalLogin(host, platform)
    mem = PortalMemory(portal_key(url))
    db.record_agent_domain(inbox.conn, f"https://{host}", "portal")  # its mail is the agent's from now on

    def visit():
        challenge.goto(page, site(url), hitl, log=log)
        page.wait_for_timeout(3000)
        if (c := challenge.detect(page)) is not None:
            challenge.wait_for_user(page, c, hitl, log=log)

    visit()
    if signed_in(page):
        res.signed_in = True
        res.notes.append("session still signed in")
    creds = vault.get(key)
    if not res.signed_in and creds:
        err = sign_in(page, creds["email"], creds["password"])
        if not err:
            res.signed_in = True
        elif re.search(r"(?i)verif", err) and not creds.get("verified"):
            since = datetime.fromisoformat(creds["created"])
            if _confirm_email(page, inbox, host, since, log, "portal_verify"):
                res.verified_mail = True
                vault.set(key, **{**creds, "verified": True})
                visit()
                res.signed_in = not sign_in(page, creds["email"], creds["password"])
        else:
            res.notes.append(f"stored credentials failed: {err}")
            log(f"portal {host}: stored credentials failed ({err}); a reset is needed")
            raise PortalError(f"sign-in failed with stored credentials: {err}")
    if not res.signed_in and not creds:
        password = new_password()
        since = datetime.now(UTC)
        vault.set(key, email=email, password=password, platform=platform, created=since.isoformat(), verified=False)
        log(f"portal {host}: creating an account for {email}")
        err = create(page, email, password)
        if err:
            if re.search(r"(?i)already (exists|in use|registered)", err):
                vault.set(key, email=email, password="", platform=platform, created=since.isoformat(), verified=False,
                          note="account existed before; password unknown (reset needed)")
            raise PortalError(f"account creation failed: {err}")
        res.created = True
        if not signed_in(page):
            if _confirm_email(page, inbox, host, since, log, "portal_verify"):
                res.verified_mail = True
            visit()
            err = sign_in(page, email, password)
            res.signed_in = not err
            if err:
                res.notes.append(f"sign-in after creation: {err}")
        else:
            res.signed_in = True
        vault.set(key, email=email, password=password, platform=platform, created=since.isoformat(),
                  verified=res.verified_mail or res.signed_in)
    mem.note(f"{platform} account for {email}: signed in {res.signed_in}")
    mem.record_visit(url, res.signed_in, platform=platform)
    log(f"portal {host}: signed in={res.signed_in} created={res.created} verified_mail={res.verified_mail}")
    return res
