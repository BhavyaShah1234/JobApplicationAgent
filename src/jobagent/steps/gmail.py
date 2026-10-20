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
from jobagent.tools import google_auth
from jobagent.tools.browser import pause, persistent_context, plain_chrome, screenshot, type_human
from jobagent.tools.hitl import Hitl, open_hitl
from jobagent.tools.inbox import Inbox
from jobagent.tools.vault import Vault, open_vault

SIGNUP_URL = "https://accounts.google.com/signup"
APP_PASSWORDS_URL = "https://myaccount.google.com/apppasswords"
TWO_SV_URL = "https://myaccount.google.com/signinoptions/twosv"
BLOCKED = re.compile(
    r"(?i)this browser or app may not be secure|could(?:n.t| not) create your google account|"
    r"could(?:n.t| not) sign you in|sorry, we could not"
)
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
    own = page.get_by_text(re.compile(r"create your own gmail address", re.I))
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
    # Host only: signup URLs carry `continue=...ManageAccount`, so substring checks give false positives.
    return urlparse(url).netloc in ("myaccount.google.com", "mail.google.com")


def _visible_errors(page: Page) -> list[str]:
    """Visible, short error-like messages on the page (hidden stale messages are ignored)."""
    return page.evaluate(r"""() => {
        const out = new Set();
        for (const el of document.querySelectorAll('[aria-live], [role=alert], div, span')) {
            // Leaf-ish text nodes only, so containers (e.g. the suggestion list) don't match.
            if ([...el.children].some(c => (c.innerText || '').trim())) continue;
            const t = (el.innerText || '').trim();
            if (!t || t.length > 200) continue;
            const r = el.getBoundingClientRect(), cs = getComputedStyle(el);
            if (!r.width || !r.height || cs.visibility === 'hidden' || cs.display === 'none') continue;
            if (/taken|not allowed|isn.t allowed|try another|between 6 and 30|only letters|can.t|invalid|already/i.test(t)) out.add(t);
        }
        return [...out];
    }""")


_SUGGESTION = re.compile(r"^([a-z0-9.]{6,30})@gmail\.com$", re.I)


def _suggested_usernames(page: Page) -> list[str]:
    """Usernames Google offers as radio options on the username page (available at that moment)."""
    texts = page.get_by_role("radio").evaluate_all(
        "els => els.map(e => (e.closest('[data-value]')?.innerText || e.parentElement?.innerText || e.getAttribute('aria-label') || '').trim())"
    )
    texts += page.locator("text=/@gmail\\.com$/").all_inner_texts()
    seen, out = set(), []
    for t in texts:
        m = _SUGGESTION.match(t.strip())
        if m and m.group(1).lower() not in seen:
            seen.add(m.group(1).lower())
            out.append(m.group(1))
    return out


def _username_diagnosis(page: Page) -> str:
    shot = str(get_settings().state_dir / "logs" / "gmail-username.png")
    screenshot(page, shot)
    field = page.locator("input[name=Username]")
    value = field.input_value() if field.count() else "<no username field>"
    errors = _visible_errors(page)
    return (f"field value {value!r}; visible errors: {errors or 'none'}; "
            f"url path {urlparse(page.url).path}; screenshot {shot}")


def _capture(page: Page, name: str, log=print) -> tuple[str, str]:
    """Screenshot + short visible-text excerpt, logged, so unknown pages can be diagnosed without noVNC."""
    shot = str(get_settings().state_dir / "logs" / f"{name}-{datetime.now(UTC):%H%M%S}.png")
    screenshot(page, shot)
    text = " ".join(page.locator("body").inner_text(timeout=10_000).split())
    excerpt = text[:300]
    log(f"signup: page '{page.title()}' {urlparse(page.url).path} | {excerpt} | screenshot {shot}")
    return shot, excerpt


def drive_signup(page: Page, ident: Identity, vault: Vault, hitl: Hitl, log=print) -> None:
    page.goto(SIGNUP_URL, wait_until="domcontentloaded")
    for _ in range(40):
        pause()
        if _signed_in(page.url):
            return
        body = page.locator("body").inner_text(timeout=10_000)
        if BLOCKED.search(body) or page.title().strip().lower() == "error":
            _capture(page, "gmail-signup-blocked", log)
            raise ManualTakeover("Google refused the automated signup")
        url = page.url
        filled = next((h.__name__ for h in HANDLERS if h(page, ident, vault)), None)
        if filled:
            log(f"signup: {filled.removeprefix('_fill_')} filled")
            pause(0.5, 1.2)
            _click_next(page)
            try:
                page.wait_for_url(lambda u: u != url, timeout=15_000)
            except PlaywrightTimeout:
                if filled == "_fill_username":
                    diagnosis = _username_diagnosis(page)
                    log(f"signup: username not accepted: {diagnosis}")
                    if _visible_errors(page):
                        # The user prefers Google's own suggestions over being asked; ask only if there are none.
                        suggestions = _suggested_usernames(page)
                        if suggestions:
                            new = suggestions[0]
                            log(f"signup: '{vault.get('gmail')['username']}' taken; using Google's suggestion '{new}'")
                        else:
                            new = hitl.ask(f"Google didn't accept username '{vault.get('gmail')['username']}' "
                                           f"({diagnosis}) and offered no suggestions. Answer with another username "
                                           "(without @gmail.com).")
                        new = new.strip().removesuffix("@gmail.com")
                        vault.set("gmail", username=new, email=f"{new}@gmail.com")
                        continue
                shot = str(get_settings().state_dir / "logs" / "gmail-signup-stuck.png")
                screenshot(page, shot)
                hitl.ask(f"Signup page didn't advance after filling {filled.removeprefix('_fill_')} "
                         f"(screenshot {shot}). Fix it in noVNC (http://localhost:6080), then answer 'done'.",
                         done_when=lambda: page.url != url)
            continue
        # Verification (phone/QR/CAPTCHA), recovery options, review and Google's terms: the user handles these.
        title = page.title()
        shot, excerpt = _capture(page, "gmail-signup-page", log)
        hitl.ask(f"Google signup needs you on '{title}' ({urlparse(url).path.rsplit('/', 1)[-1]}; page says: {excerpt!r}; "
                 f"screenshot {shot}): complete this page in noVNC (http://localhost:6080). "
                 "Answer 'done' if it doesn't continue on its own.",
                 done_when=lambda: page.url != url)
    raise RuntimeError("signup did not finish within 40 pages")


def _wait_manual_finish(s: Settings, hitl: Hitl, url: str, prompt: str) -> str:
    with plain_chrome(url, s):
        return hitl.ask(prompt)


def use_existing_account(email: str, vault: Vault, log=print) -> None:
    """Point the agent at an already-created Gmail account (signs in instead of signing up)."""
    email = email.strip().lower()
    if "@" not in email:
        email = f"{email}@gmail.com"
    entry = vault.get("gmail") or {}
    if entry.get("email", "").lower() != email:
        # A different account: drop credentials that belonged to the previous one.
        vault.delete("gmail")
        entry = {}
    vault.set("gmail", email=email, username=email.split("@")[0],
              status="ready" if entry.get("status") == "ready" else "existing")
    log(f"gmail: using existing account {email}")


def ensure_account(s: Settings, vault: Vault, hitl: Hitl, log=print) -> None:
    entry = vault.get("gmail") or {}
    if entry.get("status") in ("created", "ready"):
        return
    if entry.get("status") == "existing":
        with persistent_context(s) as ctx:
            how = google_auth.ensure_session(ctx.pages[0], vault, hitl, log)
        log(f"gmail: {entry['email']} {how}")
        vault.set("gmail", status="created")
        return
    ident = load_identity(s, hitl)
    if not entry.get("password"):
        vault.set("gmail", password=_generate_password(), status="signup_in_progress")
    # data/agent_account.yaml is the source of truth at the start of every run; usernames given
    # through HITL during the run override it only for that run.
    vault.set("gmail", username=ident.desired_username, email=f"{ident.desired_username}@gmail.com",
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


def _reauth_if_needed(page: Page, vault: Vault, log=print) -> None:
    """Google re-asks for the password (and maybe 2SV) on sensitive pages; answer with the sign-in steps."""
    entry = vault.get("gmail")
    for _ in range(4):
        did = google_auth._step(page, entry["email"], entry["password"], entry.get("totp_secret"), log)
        if not did:
            return
        log(f"google-auth (re-auth): {did}")
        page.wait_for_load_state("domcontentloaded")
        pause(2, 3)


def ensure_app_password(s: Settings, vault: Vault, hitl: Hitl, log=print) -> None:
    entry = vault.get("gmail")
    if entry.get("app_password"):
        return
    with persistent_context(s) as ctx:
        page = ctx.pages[0]
        google_auth.ensure_session(page, vault, hitl, log)
        page.goto(APP_PASSWORDS_URL, wait_until="domcontentloaded")
        pause(2, 3)
        _reauth_if_needed(page, vault, log)
        name_box = page.get_by_role("textbox", name=re.compile(r"app name", re.I))
        if not name_box.count():
            page.goto(TWO_SV_URL, wait_until="domcontentloaded")
            hitl.ask("App passwords need 2-Step Verification. Turn it on in noVNC (http://localhost:6080) — "
                     "the 2-Step Verification page is open — then answer 'done'.")
            page.goto(APP_PASSWORDS_URL, wait_until="domcontentloaded")
            pause(2, 3)
            _reauth_if_needed(page, vault, log)
            name_box = page.get_by_role("textbox", name=re.compile(r"app name", re.I))
        app_pw = None
        if name_box.count():
            type_human(name_box.first, "jobagent")
            page.get_by_role("button", name=re.compile(r"^create$", re.I)).first.click()
            try:
                page.get_by_text(_APP_PW).first.wait_for(timeout=20_000)
                m = _APP_PW.search(page.locator("body").inner_text())
                app_pw = m.group(1) if m else None
            except PlaywrightTimeout:
                app_pw = None
        if app_pw:
            vault.set("gmail", app_password=app_pw.replace(" ", ""))
            page.get_by_role("button", name=re.compile(r"^done$", re.I)).first.click()
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


def check_browser_session(s: Settings, vault: Vault | None = None, hitl: Hitl | None = None, log=print) -> str:
    """Gmail opens signed in — re-signing in automatically if the session expired."""
    vault, hitl = vault or open_vault(), hitl or open_hitl(log)
    with persistent_context(s) as ctx:
        page = ctx.pages[0]
        how = google_auth.ensure_session(page, vault, hitl, log)
        pause(2, 3)
        shot = s.state_dir / "logs" / "gmail-session.png"
        screenshot(page, str(shot))
        host = urlparse(page.url).netloc
    if host != "mail.google.com":
        raise RuntimeError(f"not in Gmail: landed on {host} (screenshot {shot})")
    return f"Gmail open — {how} (screenshot {shot})"


def setup(existing_email: str | None = None, log=print) -> None:
    s, vault = get_settings(), open_vault()
    hitl = open_hitl(log)
    if existing_email:
        use_existing_account(existing_email, vault, log)
    entry = vault.get("gmail") or {}
    if entry.get("status") == "existing" and not entry.get("password"):
        hitl.ask(f"Store the password of {entry['email']} with `jobagent vault set gmail password` "
                 "(and optionally the authenticator setup key with `jobagent vault set gmail totp_secret` "
                 "for fully automatic 2-Step Verification), then answer 'done'.",
                 done_when=lambda: bool((vault.get("gmail") or {}).get("password")))
    ensure_account(s, vault, hitl, log)
    ensure_app_password(s, vault, hitl, log)
    log(check_imap_roundtrip(vault, log))
    vault.set("gmail", status="ready")
