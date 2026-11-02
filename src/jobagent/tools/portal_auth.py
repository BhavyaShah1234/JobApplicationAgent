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


def _shot(page: Page, what: str) -> None:
    """A screenshot shortly after a submit, for the portal notes and debugging (state/logs/portal-<host>-<what>.png)."""
    from jobagent.tools.browser import screenshot
    page.wait_for_timeout(3000)
    screenshot(page, f"/app/state/logs/portal-{urlparse(page.url).netloc}-{what}.png")


def _wd_signed_in(page: Page) -> bool:
    """No Sign In button, and the header shows the account (its email, Candidate Home or Settings)."""
    if page.locator('[data-automation-id="utilityButtonSignIn"]').filter(visible=True).count():
        return False
    bar = page.locator('[data-automation-id="utilityButtonBar"], header').first
    text = bar.inner_text() if bar.count() else ""
    return bool(re.search(r"(?i)@|candidate home|settings|sign out", text))


def _wd_open_email_form(page: Page) -> None:
    page.get_by_role("button", name=re.compile(r"^sign in$", re.I)).filter(visible=True).first.click()
    page.wait_for_timeout(2500)
    by_email = page.get_by_role("button", name=re.compile(r"sign in with email", re.I))
    if by_email.count():
        by_email.first.click()
        page.wait_for_timeout(2000)
    page.locator('[data-automation-id="email"]').first.wait_for(timeout=15000)


def _wd_error(page: Page) -> str:
    """Error text inside the sign-in / create-account dialog (the page's own live region says things like
    'Search for Jobs page is loaded', which is not an error)."""
    box = page.locator('[data-automation-id="signInContent"]')
    if not box.count():
        return ""
    errs = box.first.locator('[data-automation-id="errorMessage"], [data-automation-id*="Alert" i], [role="alert"], '
                             '[data-automation-id*="error" i]')
    return " ".join(t for e in errs.filter(visible=True).all() if (t := e.inner_text().strip()))[:300]


def _wd_sign_in(page: Page, email: str, password: str) -> str:
    """'' if signed in, else the page's error text."""
    _wd_open_email_form(page)
    page.locator('[data-automation-id="email"]').first.fill(email)
    page.locator('[data-automation-id="password"]').first.fill(password)
    _wd_click(page, "signInSubmitButton")
    _shot(page, "signin")
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
    for attempt in range(2):
        for _ in range(15):
            page.wait_for_timeout(1000)
            if _wd_signed_in(page):
                return ""
            dialog = page.locator('[data-automation-id="signInContent"]')
            text = dialog.first.inner_text() if dialog.count() else ""
            # accepted: Workday goes back to the sign-in choices (and mails a verification link) or says so
            if not dialog.count() or re.search(r"(?i)sign in with email|verif(y|ication).{0,60}(email|account)|"
                                               r"check your (e-?mail|inbox)", text):
                _shot(page, "create")
                return ""
            if err := _wd_error(page):
                return err
        if attempt == 0:  # the click didn't take: submit the form with Enter
            page.locator('[data-automation-id="verifyPassword"]').first.press("Enter")
    _shot(page, "create")
    return "no result after creating the account"


# --- the flow (platform-independent) --------------------------------------------------------------------------

ADAPTERS = {"workday": (_wd_site, _wd_sign_in, _wd_create, _wd_signed_in)}


def _confirm_email(page: Page, inbox, host: str, since: datetime, log, purpose: str, timeout_s: float = 300) -> bool:
    """Use the portal's verification mail (a link opened in this browser, or a code typed into the page)."""
    try:
        mail, v = inbox.wait_for_verification(f"https://{host}", since, kind="portal", timeout_s=timeout_s)
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


def portal_password(vault: Vault) -> str:
    """The password for every portal account: the vault's `portal_default` entry (the user's choice, so the agent
    always signs in on the first try), else a generated one."""
    return (vault.get("portal_default") or {}).get("password") or new_password()


NO_ACCOUNT = re.compile(r"(?i)no account|not (registered|found)|doesn.?t exist|does not exist|create an account|"
                        r"invalid (email|user)|wrong email|incorrect (email|user)|couldn.?t find")
EXISTS = re.compile(r"(?i)already (exists|in use|registered|have an account)|account with this email")


def candidate_values(email: str, password: str) -> dict:
    """What the general portal agent may type: the agent's email, the shared password, the user's name and phone."""
    import yaml

    from jobagent.config import get_settings
    info = yaml.safe_load((get_settings().data_dir / "personal_info.yaml").read_text()) or {}
    name, contact = info.get("name") or {}, info.get("contact") or info
    return {"EMAIL": email, "PASSWORD": password, "FIRST_NAME": name.get("preferred_first") or name.get("first", ""),
            "LAST_NAME": name.get("last", ""), "PHONE": str(contact.get("phone", ""))}


def ensure_with_agent(page: Page, url: str, vault: Vault, inbox, hitl: Hitl, email: str, log=print,
                      fresh: bool = False) -> PortalLogin:
    """Any portal without an adapter: the general agent (tools/portal_agent.py) signs in or registers."""
    from jobagent.tools import portal_agent
    host = urlparse(url).netloc.lower()
    key = f"portal:{host}"
    res = PortalLogin(host, platform_of(url))
    mem = PortalMemory(portal_key(url))
    db.record_agent_domain(inbox.conn, f"https://{host}", "portal")
    creds = vault.get(key) or {}
    password = creds.get("password") or portal_password(vault)
    if not creds:
        vault.set(key, email=email, password=password, platform=res.platform,
                  attempted=datetime.now(UTC).isoformat(), verified=False)  # saved before anything is submitted
    if fresh:
        page.context.clear_cookies(domain=host)
    challenge.goto(page, url, hitl, log=log)
    out = portal_agent.run(page, url, candidate_values(email, password), inbox, hitl, host, log=log,
                           notes=mem.data.get("notes", []))
    res.created, res.verified_mail = out["registered"], out["verified_mail"]
    res.signed_in = out["state"] in ("signed_in", "no_account_needed")
    res.notes = out["steps"][-8:]
    if out["state"] == "no_account_needed":
        res.notes.append("no account needed: the posting's form takes applications directly")
    old = vault.get(key) or {}
    vault.set(key, **{**old, "account": old.get("account") or (res.signed_in and out["state"] == "signed_in"),
                      "verified": res.signed_in, "no_account": out["state"] == "no_account_needed",
                      **({"created": datetime.now(UTC).isoformat()} if res.created else {})})
    mem.note(f"{out['state']}: " + " > ".join(s[:40] for s in out["steps"][-6:]))
    mem.record_visit(url, res.signed_in, platform=res.platform)
    log(f"portal {host}: state={out['state']} signed in={res.signed_in} registered={res.created} "
        f"verified_mail={res.verified_mail}")
    return res


def ensure_account(page: Page, url: str, vault: Vault, inbox, hitl: Hitl, email: str, log=print,
                   fresh: bool = False) -> PortalLogin:
    """Signed in to the portal of `url` with the agent's account: sign in first (stored or shared password); register
    only when the portal has no account for the email; never a password reset. Workday has its own adapter; every
    other portal goes to the general agent."""
    platform = platform_of(url)
    if platform not in ADAPTERS:
        return ensure_with_agent(page, url, vault, inbox, hitl, email, log=log, fresh=fresh)
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

    def save(password: str, **extra):
        old = vault.get(key) or {}
        vault.set(key, **{**old, "email": email, "password": password, "platform": platform, **extra})

    if fresh:  # test a real sign-in: forget this portal's session first
        page.context.clear_cookies(domain=host)
    visit()
    if signed_in(page):
        res.signed_in = True
        res.notes.append("session still signed in")
    creds = vault.get(key) or {}
    password = creds.get("password") or portal_password(vault)
    err = ""
    if not res.signed_in:  # 1. sign in (an account made for another posting at this employer is reused)
        err = sign_in(page, email, password)
        if not err:
            res.signed_in = True
            save(password, verified=True, account=True)
        elif creds.get("attempted") or creds.get("account"):
            # registered earlier but the email isn't confirmed yet (Workday then says "wrong email or password or
            # your account might be locked"): use that verification mail before ever registering again
            since = datetime.fromisoformat(creds.get("created") or creds["attempted"])
            if _confirm_email(page, inbox, host, since, log, "portal_verify", timeout_s=90):
                res.verified_mail = True
                for _ in range(2):  # Workday can take a moment after activation
                    visit()
                    if signed_in(page) or not sign_in(page, email, password):
                        res.signed_in = True
                        break
                    page.wait_for_timeout(10000)
            # a used verification mail proves the account exists: never register again
            save(password, verified=res.signed_in, account=res.verified_mail or res.signed_in,
                 created=since.isoformat())
            creds = vault.get(key) or {}
    if not res.signed_in and not creds.get("account"):  # 2. register (only if no account is known to exist)
        since = datetime.now(UTC)
        save(password, attempted=since.isoformat(), verified=False)  # saved before submitting
        log(f"portal {host}: no account yet (sign-in said: {err or 'nothing'}); registering {email}")
        visit()
        cerr = create(page, email, password)
        if cerr and EXISTS.search(cerr):
            res.notes.append("an account already existed with another password")
            raise PortalError(f"{host}: an account for {email} exists with a different password; reset it once "
                              "(or set it to the shared password) and run again")
        if cerr:
            raise PortalError(f"account creation failed: {cerr}")
        res.created = True
        save(password, account=True, created=since.isoformat())
        if not signed_in(page):
            if _confirm_email(page, inbox, host, since, log, "portal_verify"):
                res.verified_mail = True
            visit()
            serr = sign_in(page, email, password)
            res.signed_in = not serr
            if serr:
                res.notes.append(f"sign-in after creation: {serr}")
        else:
            res.signed_in = True
        save(password, verified=res.signed_in)
    if not res.signed_in:
        raise PortalError(f"{host}: could not sign in ({err or 'unknown'})")
    mem.note(f"{platform} account for {email}: signed in")
    mem.record_visit(url, res.signed_in, platform=platform)
    log(f"portal {host}: signed in={res.signed_in} created={res.created} verified_mail={res.verified_mail}")
    return res
