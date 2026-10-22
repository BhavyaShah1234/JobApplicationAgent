"""Step 2: the agent's persistent Chrome profile — Google session, cookies, warm-up, challenge hand-off."""

import random
import time
from datetime import UTC, datetime
from urllib.parse import urlparse

from jobagent.config import Settings, get_settings
from jobagent.tools import challenge
from jobagent.tools.browser import clear_stale_lock, pause, persistent_context, plain_chrome, screenshot
from jobagent.tools.google_auth import GMAIL_URL, SIGNIN_URL
from jobagent.tools.hitl import Hitl, open_hitl
from jobagent.tools.vault import Vault, open_vault

PROBE_COOKIE = "jobagent_profile_probe"
PROBE_DOMAIN = ".example.com"

WARM_UP_SITES = [
    "https://en.wikipedia.org/wiki/Special:Random",
    "https://news.ycombinator.com/",
    "https://www.bbc.com/news",
    "https://www.weather.gov/",
    "https://github.com/explore",
    "https://www.reuters.com/technology/",
]


# --- Google session (signed in by the user, in plain Chrome) ----------------------------------------

def google_session_state(s: Settings) -> str:
    """'ok' if the agent's browser opens Gmail signed in, else 'signed-out'."""
    with persistent_context(s) as ctx:
        page = ctx.pages[0]
        page.goto(GMAIL_URL, wait_until="domcontentloaded")
        pause(2, 3)
        return "ok" if urlparse(page.url).netloc == "mail.google.com" else "signed-out"


def google_login(vault: Vault, hitl: Hitl, log=print) -> str:
    """Open plain (non-automated) Chrome on the agent's profile for the user to sign in once, then verify."""
    s = get_settings()
    email = (vault.get("gmail") or {}).get("email", "the agent's account")
    with plain_chrome(SIGNIN_URL, s):
        hitl.ask(f"Plain Chrome (not automated) is open in noVNC (http://localhost:6080). Sign in as {email} like on "
                 "any computer, wait until Gmail's inbox shows, then answer 'done'. Chrome is closed for you afterwards.")
    state = google_session_state(s)
    vault.set("gmail", browser_session=state, browser_session_checked=datetime.now(UTC).isoformat(timespec="seconds"))
    log(f"google session in the agent's browser: {state}")
    return state


# --- warm-up --------------------------------------------------------------------------------------

def warm_up(minutes: float, hitl: Hitl, log=print) -> int:
    """Ordinary browsing so the profile accumulates normal cookies and history. Returns pages visited."""
    deadline = time.monotonic() + minutes * 60
    visited = 0
    with persistent_context() as ctx:
        page = ctx.pages[0]
        sites = WARM_UP_SITES[:]
        random.shuffle(sites)
        for url in sites * 3:
            if time.monotonic() > deadline:
                break
            try:
                challenge.goto(page, url, hitl, log, attempts=2)
            except Exception as e:  # one unreachable site shouldn't stop the warm-up
                log(f"warm-up: skipped {url}: {type(e).__name__}")
                continue
            visited += 1
            for _ in range(random.randint(2, 5)):  # read the page a little
                page.mouse.wheel(0, random.randint(300, 900))
                pause(1.0, 3.0)
            links = page.locator(f'a[href^="https://{urlparse(url).netloc}"], a[href^="/"]')
            if links.count() > 5 and random.random() < 0.6:
                try:
                    links.nth(random.randint(0, min(links.count(), 40) - 1)).click(timeout=5_000)
                    page.wait_for_load_state("domcontentloaded", timeout=15_000)
                    visited += 1
                    pause(2, 4)
                except Exception:
                    pass
            log(f"warm-up: {urlparse(page.url).netloc} ({visited} pages)")
        cookies = len(ctx.cookies())
    log(f"warm-up done: {visited} pages, {cookies} cookies in the profile")
    return visited


# --- status / e2e check -------------------------------------------------------------------------------

def status(s: Settings | None = None) -> dict:
    s = s or get_settings()
    profile = s.chrome_profile_dir
    size_mb = sum(f.stat().st_size for f in profile.rglob("*") if f.is_file()) / 1e6 if profile.exists() else 0.0
    with persistent_context(s) as ctx:
        cookies = ctx.cookies()
    return {
        "profile": str(profile),
        "size_mb": round(size_mb, 1),
        "cookies": len(cookies),
        "cookie_domains": len({c["domain"].lstrip(".") for c in cookies}),
        "google_session": google_session_state(s),
    }


_FAKE_CHALLENGES = {
    "cloudflare": "<title>Just a moment...</title><body>Checking if the site connection is secure. "
                  "Performance & security by Cloudflare</body>",
    "google-unusual-traffic": "<body>Our systems have detected unusual traffic from your computer network.</body>",
    "recaptcha": '<body>Please complete the check<iframe title="reCAPTCHA" width="304" height="78" '
                 'src="about:blank"></iframe></body>',
    "perimeterx": "<body>Press & Hold to confirm you are a human (and not a bot).</body>",
}
_NORMAL_PAGES = [
    "<title>Software Engineer – Acme</title><body>Apply now. We use reCAPTCHA in our forms.</body>",
    '<body>Careers<iframe title="reCAPTCHA" width="1" height="1" src="about:blank"></iframe></body>',  # invisible v3 badge
]


def check_challenge_detection(page) -> str:
    for kind, html in _FAKE_CHALLENGES.items():
        page.set_content(html)
        found = challenge.detect(page)
        if not found or found.kind != kind:
            raise RuntimeError(f"fake {kind} page detected as {found.kind if found else 'nothing'}")
    for html in _NORMAL_PAGES:
        page.set_content(html)
        if (found := challenge.detect(page)) is not None:
            raise RuntimeError(f"normal page flagged as {found.kind}")
    # A challenge that clears on its own (as when the user solves it) must release the wait.
    page.set_content("<title>Just a moment...</title><body>Checking your browser</body>"
                     "<script>setTimeout(() => { document.title = 'Jobs'; document.body.innerText = 'Open roles'; }, 3000)"
                     "</script>")
    found = challenge.detect(page)
    started = time.monotonic()
    quiet = Hitl(get_settings().state_dir / "hitl-selftest", notify=lambda _: None)
    challenge.wait_for_user(page, found, quiet, log=lambda _: None, timeout_s=60)
    return (f"{len(_FAKE_CHALLENGES)} challenge kinds detected, {len(_NORMAL_PAGES)} normal pages not flagged, "
            f"self-clearing challenge released the wait after {time.monotonic() - started:.0f}s")


def check_profile_persistence(s: Settings, launches: int = 3) -> str:
    """The probe cookie written by the previous run (even in a previous container) must still be there."""
    marker_file = s.state_dir / "browser-probe.txt"
    previous = marker_file.read_text().strip() if marker_file.exists() else None
    seen = None
    for i in range(launches):  # repeated clean launch/close: no lock or corruption errors
        clear_stale_lock(s.chrome_profile_dir)
        with persistent_context(s) as ctx:
            values = {c["value"] for c in ctx.cookies("https://www.example.com") if c["name"] == PROBE_COOKIE}
            if i == 0:
                seen = previous in values if previous else None
            value = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
            ctx.add_cookies([{"name": PROBE_COOKIE, "value": value, "domain": PROBE_DOMAIN, "path": "/",
                              "expires": time.time() + 30 * 86400, "secure": True, "sameSite": "Lax"}])
        marker_file.write_text(value)
        with persistent_context(s) as ctx:
            if value not in {c["value"] for c in ctx.cookies("https://www.example.com") if c["name"] == PROBE_COOKIE}:
                raise RuntimeError(f"probe cookie lost between launches (launch {i + 1})")
    if seen is False:
        raise RuntimeError("the probe cookie from the previous run is gone: the profile did not persist")
    return (f"{launches * 2} clean launches; cookie persisted between launches"
            + ("; cookie from the previous run/container still present" if seen else "; first run (no earlier probe)"))


def check(log=print) -> list[tuple[str, bool, str]]:
    s = get_settings()
    vault = open_vault()
    results = []

    def run(name, fn):
        try:
            results.append((name, True, fn()))
        except Exception as e:
            results.append((name, False, f"{type(e).__name__}: {e}"))

    run("profile persistence", lambda: check_profile_persistence(s))

    def detection():
        with persistent_context(s) as ctx:
            return check_challenge_detection(ctx.pages[0])

    run("challenge hand-off", detection)

    def google():
        state = google_session_state(s)
        vault.set("gmail", browser_session=state, browser_session_checked=datetime.now(UTC).isoformat(timespec="seconds"))
        if state != "ok":
            return "signed-out (run `jobagent browser google-login` once to sign in by hand)"
        with persistent_context(s) as ctx:
            page = ctx.pages[0]
            page.goto(GMAIL_URL, wait_until="domcontentloaded")
            pause(3, 4)
            shot = s.state_dir / "logs" / "browser-gmail.png"
            screenshot(page, str(shot))
        return f"ok — Gmail opens signed in (screenshot {shot})"

    run("google session", google)
    return results
