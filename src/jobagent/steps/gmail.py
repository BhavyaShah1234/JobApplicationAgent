"""Step 1: the agent's own Gmail account.

`setup()` is idempotent: it creates the account (agent fills forms; the user handles phone/QR
verification, CAPTCHAs and accepting Google's terms in noVNC), obtains an app password, and stores
everything in the vault. `check()` is the step's end-to-end verification.
"""

import re
import secrets
import string
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from urllib.parse import urlparse

import yaml
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from jobagent import db
from jobagent.config import Settings, get_settings
from jobagent.tools.browser import pause, persistent_context, plain_chrome, screenshot, type_human
from jobagent.tools.hitl import Hitl, open_hitl
from jobagent.tools.inbox import Inbox
from jobagent.tools.vault import Vault, open_vault

SIGNUP_URL = "https://accounts.google.com/signup"
APP_PASSWORDS_URL = "https://myaccount.google.com/apppasswords"
TWO_SV_URL = "https://myaccount.google.com/signinoptions/twosv"
BLOCKED = re.compile(r"(?i)this browser or app may not be secure|couldn.t create your google account|couldn.t sign you in")
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]


@dataclass
class Identity:
    first_name: str
    last_name: str
    birthday: date
    gender: str
    desired_username: str
    recovery_email: str = ""


class ManualTakeover(Exception):
    """Google refused the automated browser; continue in plain Chrome driven by the user."""


def load_identity(s: Settings, hitl: Hitl) -> Identity:
    path = s.data_dir / "agent_account.yaml"
    while True:
        raw = yaml.safe_load(path.read_text()) if path.exists() else {}
        raw = raw or {}
        missing = [k for k in ("first_name", "birthday", "desired_username") if not str(raw.get(k) or "").strip()]
        if not missing:
            bday = raw["birthday"] if isinstance(raw["birthday"], date) else date.fromisoformat(str(raw["birthday"]))
            return Identity(str(raw["first_name"]).strip(), str(raw.get("last_name") or "").strip(), bday,
                            str(raw.get("gender") or "Rather not say"), str(raw["desired_username"]).strip(),
                            str(raw.get("recovery_email") or "").strip())
        hitl.ask(f"Fill {', '.join(missing)} in data/agent_account.yaml (host folder), then answer 'done'.")


def _generate_password(n: int = 20) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#%^*-_=+"
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(n))
        if all(any(c in group for c in pw) for group in (string.ascii_lowercase, string.ascii_uppercase, string.digits)):
            return pw


# --- signup page handlers (each returns True if it filled the page) --------------------------------

def _visible(page: Page, selector: str) -> bool:
    loc = page.locator(selector)
    return loc.count() > 0 and loc.first.is_visible()


def _choose(page: Page, field_id: str, option_text: str) -> None:
    """Google uses native <select> on some variants and custom comboboxes on others."""
    native = page.locator(f"select#{field_id}")
    if native.count():
        native.select_option(label=option_text)
        return
    page.locator(f"#{field_id}").click()
    pause(0.3, 0.8)
    page.get_by_role("option", name=option_text, exact=True).first.click()


def _fill_name(page: Page, ident: Identity, _: Vault) -> bool:
    if not _visible(page, "input[name=firstName]"):
        return False
    type_human(page.locator("input[name=firstName]"), ident.first_name)
    if ident.last_name and _visible(page, "input[name=lastName]"):
        type_human(page.locator("input[name=lastName]"), ident.last_name)
    return True


def _fill_birthday(page: Page, ident: Identity, _: Vault) -> bool:
    if not (_visible(page, "#day") and _visible(page, "#year")):
        return False
    _choose(page, "month", MONTHS[ident.birthday.month - 1])
    type_human(page.locator("#day"), str(ident.birthday.day))
    type_human(page.locator("#year"), str(ident.birthday.year))
    if page.locator("#gender").count():
        _choose(page, "gender", ident.gender)
    return True


def _fill_username(page: Page, ident: Identity, vault: Vault) -> bool:
    own = page.get_by_text(re.compile(r"(?i)create your own gmail address"))
    if own.count():
        own.first.click()
        pause(0.3, 0.8)
    if not _visible(page, "input[name=Username]"):
        return False
    username = (vault.get("gmail") or {}).get("username") or ident.desired_username
    type_human(page.locator("input[name=Username]"), username)
    vault.set("gmail", username=username, email=f"{username}@gmail.com")
    return True


def _fill_password(page: Page, _: Identity, vault: Vault) -> bool:
    if not _visible(page, "input[name=Passwd]"):
        return False
    password = vault.get("gmail")["password"]
    type_human(page.locator("input[name=Passwd]"), password)
    if _visible(page, "input[name=PasswdAgain]"):
        type_human(page.locator("input[name=PasswdAgain]"), password)
    return True


HANDLERS = [_fill_name, _fill_birthday, _fill_username, _fill_password]


def _click_next(page: Page) -> None:
    page.get_by_role("button", name=re.compile(r"^(Next|Continue)$")).first.click()


def _signed_in(url: str) -> bool:
    host = urlparse(url).netloc
    return host in ("myaccount.google.com", "mail.google.com") or "ManageAccount" in url


def _username_rejected(page: Page) -> bool:
    return page.get_by_text(re.compile(r"(?i)that username is taken|username .*(not allowed|isn.t allowed)")).count() > 0


def drive_signup(page: Page, ident: Identity, vault: Vault, hitl: Hitl, log=print) -> None:
    page.goto(SIGNUP_URL, wait_until="domcontentloaded")
    for _ in range(40):
        pause()
        if _signed_in(page.url):
            return
        if BLOCKED.search(page.locator("body").inner_text(timeout=10_000)):
            raise ManualTakeover("Google refused the automated browser")
        url = page.url
        filled = next((h.__name__ for h in HANDLERS if h(page, ident, vault)), None)
        if filled:
            log(f"signup: {filled.removeprefix('_fill_')} filled")
            pause(0.5, 1.2)
            _click_next(page)
            try:
                page.wait_for_url(lambda u: u != url, timeout=15_000)
            except PlaywrightTimeout:
                if filled == "_fill_username" and _username_rejected(page):
                    new = hitl.ask(f"Gmail username '{vault.get('gmail')['username']}' is taken or not allowed. "
                                   "Answer with another username (without @gmail.com).")
                    vault.set("gmail", username=new.strip(), email=f"{new.strip()}@gmail.com")
                    continue
                shot = str(get_settings().state_dir / "logs" / "gmail-signup-stuck.png")
                screenshot(page, shot)
                hitl.ask(f"Signup page didn't advance after filling {filled.removeprefix('_fill_')} "
                         f"(screenshot {shot}). Fix it in noVNC (http://localhost:6080), then answer 'done'.",
                         done_when=lambda: page.url != url)
            continue
        # Verification (phone/QR/CAPTCHA), recovery options, review and Google's terms: the user handles these.
        title = page.title()
        hitl.ask(f"Google signup needs you on '{title}' ({urlparse(url).path.rsplit('/', 1)[-1]}): "
                 "complete this page in noVNC (http://localhost:6080). Answer 'done' if it doesn't continue on its own.",
                 done_when=lambda: page.url != url)
    raise RuntimeError("signup did not finish within 40 pages")


def _wait_manual_finish(s: Settings, hitl: Hitl, url: str, prompt: str) -> str:
    with plain_chrome(url, s):
        return hitl.ask(prompt)


def ensure_account(s: Settings, vault: Vault, hitl: Hitl, log=print) -> None:
    entry = vault.get("gmail") or {}
    if entry.get("status") in ("created", "ready"):
        return
    ident = load_identity(s, hitl)
    if not entry.get("password"):
        vault.set("gmail", password=_generate_password(), status="signup_in_progress",
                  username=ident.desired_username, email=f"{ident.desired_username}@gmail.com",
                  first_name=ident.first_name, last_name=ident.last_name)
    try:
        with persistent_context(s) as ctx:
            drive_signup(ctx.pages[0], ident, vault, hitl, log)
    except ManualTakeover as e:
        log(f"signup: {e}; switching to plain Chrome for you to finish")
        answer = _wait_manual_finish(
            s, hitl, SIGNUP_URL,
            "Google refused the automated browser. Plain Chrome is open in noVNC (http://localhost:6080): create the "
            "account there using the generated password (`jobagent vault show gmail`). If you pick a different "
            "username, tell me with `jobagent hitl answer <id> <username>`; otherwise answer 'done'.",
        )
        username = answer.strip().removesuffix("@gmail.com")
        if username and username.lower() != "done":
            vault.set("gmail", username=username, email=f"{username}@gmail.com")
    vault.set("gmail", status="created", created_at=datetime.now(UTC).isoformat(timespec="seconds"))
    log(f"signup: account {vault.get('gmail')['email']} created")


# --- app password -----------------------------------------------------------------------------------

_APP_PW = re.compile(r"\b([a-z]{4} [a-z]{4} [a-z]{4} [a-z]{4})\b")


def _reauth_if_needed(page: Page, password: str) -> None:
    if _visible(page, "input[name=Passwd]"):
        type_human(page.locator("input[name=Passwd]"), password)
        _click_next(page)
        page.wait_for_load_state("domcontentloaded")
        pause(2, 3)


def ensure_app_password(s: Settings, vault: Vault, hitl: Hitl, log=print) -> None:
    entry = vault.get("gmail")
    if entry.get("app_password"):
        return
    with persistent_context(s) as ctx:
        page = ctx.pages[0]
        page.goto(APP_PASSWORDS_URL, wait_until="domcontentloaded")
        pause(2, 3)
        _reauth_if_needed(page, entry["password"])
        name_box = page.get_by_role("textbox", name=re.compile(r"(?i)app name"))
        if not name_box.count():
            page.goto(TWO_SV_URL, wait_until="domcontentloaded")
            hitl.ask("App passwords need 2-Step Verification. Turn it on in noVNC (http://localhost:6080) — "
                     "the 2-Step Verification page is open — then answer 'done'.")
            page.goto(APP_PASSWORDS_URL, wait_until="domcontentloaded")
            pause(2, 3)
            _reauth_if_needed(page, entry["password"])
            name_box = page.get_by_role("textbox", name=re.compile(r"(?i)app name"))
        app_pw = None
        if name_box.count():
            type_human(name_box.first, "jobagent")
            page.get_by_role("button", name=re.compile(r"(?i)^create$")).first.click()
            try:
                page.get_by_text(_APP_PW).first.wait_for(timeout=20_000)
                m = _APP_PW.search(page.locator("body").inner_text())
                app_pw = m.group(1) if m else None
            except PlaywrightTimeout:
                app_pw = None
        if app_pw:
            vault.set("gmail", app_password=app_pw.replace(" ", ""))
            page.get_by_role("button", name=re.compile(r"(?i)^done$")).first.click()
            log("app password created and stored in the vault")
            return
    hitl.ask("Couldn't create the app password automatically. In noVNC, create one named 'jobagent' at "
             f"{APP_PASSWORDS_URL}, then paste it with: jobagent hitl answer <id>",
             secret=True, vault_target=["gmail", "app_password"])


# --- end-to-end check -------------------------------------------------------------------------------

def check_imap_roundtrip(vault: Vault, log=print) -> str:
    entry = vault.get("gmail")
    token = f"jobagent-selftest-{uuid.uuid4().hex[:10]}"
    with Inbox(entry["email"], entry["app_password"], db.connect()) as inbox:
        started = time.monotonic()
        inbox.send(entry["email"], f"Self-test {token}", f"Harness inbox self-test.\nToken: {token}\n")
        mail = inbox.wait_for(lambda m: token in m.subject, criteria=f'SUBJECT "{token}"', timeout_s=180)
        took = time.monotonic() - started
        inbox.consume_and_trash(mail, "self_test")
        if inbox.search(f'SUBJECT "{token}"'):
            raise RuntimeError("self-test mail still in INBOX after trashing")
        if not inbox.search(f'SUBJECT "{token}"', folder=inbox.trash_folder()):
            raise RuntimeError("self-test mail not found in Trash")
    row = db.connect().execute("SELECT count(*) FROM verification_log WHERE purpose='self_test'").fetchone()[0]
    return f"sent → received in {took:.0f}s → trashed; audit rows (self_test) = {row}"


def check_browser_session(s: Settings) -> str:
    with persistent_context(s) as ctx:
        page = ctx.pages[0]
        page.goto("https://mail.google.com/mail/u/0/", wait_until="domcontentloaded")
        pause(3, 4)
        shot = s.state_dir / "logs" / "gmail-session.png"
        screenshot(page, str(shot))
        host = urlparse(page.url).netloc
    if host != "mail.google.com":
        raise RuntimeError(f"not signed in: landed on {host} (screenshot {shot})")
    return f"signed in, Gmail open without a new sign-in (screenshot {shot})"


def setup(log=print) -> None:
    s, vault = get_settings(), open_vault()
    hitl = open_hitl(log)
    ensure_account(s, vault, hitl, log)
    ensure_app_password(s, vault, hitl, log)
    log(check_imap_roundtrip(vault, log))
    vault.set("gmail", status="ready")
