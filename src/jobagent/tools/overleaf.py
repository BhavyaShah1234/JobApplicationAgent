"""Overleaf through the agent's browser session (free-plan backend).

Login uses "Log in with Google" with the Google session already in the profile (step 2). Projects are
created with Overleaf's official "Open in Overleaf" endpoint (POST /docs), compiled with the project's
compile endpoint and read back as PDF / zip — all with the browser's own cookies, no extra library.
"""

import json
import re
from datetime import UTC, datetime
from urllib.parse import urlparse

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from jobagent.config import get_settings
from jobagent.tools.browser import pause, plain_chrome, screenshot
from jobagent.tools.google_auth import BLOCKED as GOOGLE_BLOCKED
from jobagent.tools.hitl import Hitl

BASE = "https://www.overleaf.com"


class OverleafError(RuntimeError):
    pass


def meta(page: Page, name: str) -> str:
    loc = page.locator(f'meta[name="{name}"]')
    return (loc.first.get_attribute("content") or "") if loc.count() else ""


def _capture(page: Page, name: str, log) -> tuple[str, str]:
    shot = str(get_settings().state_dir / "logs" / f"{name}-{datetime.now(UTC):%H%M%S}.png")
    screenshot(page, shot)
    excerpt = " ".join(page.locator("body").inner_text(timeout=10_000).split())[:250]
    log(f"overleaf: page '{page.title()}' {urlparse(page.url).netloc}{urlparse(page.url).path} | {excerpt} | {shot}")
    return shot, excerpt


def logged_in(page: Page) -> bool:
    page.goto(f"{BASE}/project", wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    return urlparse(page.url).path.startswith("/project") and bool(meta(page, "ol-user_id"))


def login_with_google(page: Page, email: str, hitl: Hitl, log=print, max_steps: int = 10) -> str:
    """Log in via 'Log in with Google' using the profile's Google session. Returns how it went."""
    if logged_in(page):
        return "already logged in"
    page.goto(f"{BASE}/auth/google?intent=sign_in", wait_until="domcontentloaded")
    signed_up = False
    for _ in range(max_steps):
        page.wait_for_timeout(2500)
        url = urlparse(page.url)
        if url.netloc == "www.overleaf.com" and url.path.startswith("/project") and meta(page, "ol-user_id"):
            return "signed up with Google" if signed_up else "logged in with Google"
        body = page.locator("body").inner_text(timeout=10_000)
        if url.netloc == "www.overleaf.com" and (url.path.startswith("/registration/") or "try-premium" in url.path):
            # Post-sign-up onboarding/upsell pages: stay on the free plan and go to the project list.
            log(f"overleaf: skipping {url.path} (staying on the free plan)")
            page.goto(f"{BASE}/project", wait_until="domcontentloaded")
            continue
        if url.netloc == "www.overleaf.com" and re.search(r"(?i)not registered", body) and not signed_up:
            # No Overleaf account for this Google account yet: "sign up with Google" creates one.
            log("overleaf: no account for this Google account yet; signing up with Google")
            signed_up = True
            page.goto(f"{BASE}/auth/google?intent=sign_up", wait_until="domcontentloaded")
            continue
        if url.netloc == "accounts.google.com":
            if GOOGLE_BLOCKED.search(body):
                raise OverleafError("Google refused the sign-in from the automated browser")
            account = page.get_by_text(email, exact=False)
            if re.search(r"(?i)choose an account", body) and account.count():
                account.first.click()
                log("overleaf: chose the agent's Google account")
                continue
            consent = page.get_by_role("button", name=re.compile(r"^(continue|allow)$", re.I))
            if consent.count():
                consent.first.click()  # share basic profile (name, email) with Overleaf, as the user asked
                log("overleaf: confirmed 'Sign in to Overleaf' on Google's consent screen")
                continue
        # Overleaf's first-login registration / terms, or anything unexpected: the user decides.
        shot, excerpt = _capture(page, "overleaf-login", log)
        current = page.url
        hitl.ask(f"Overleaf login needs you: {excerpt!r} (screenshot {shot}). Complete it in noVNC "
                 "(http://localhost:6080) — e.g. accept Overleaf's terms — then answer 'done'.",
                 done_when=lambda: page.url != current)
    if logged_in(page):
        return "logged in with Google (with help)"
    raise OverleafError("could not log in to Overleaf")


def login_by_hand(hitl: Hitl, log=print) -> None:
    """Fallback: the user logs in once in plain (non-automated) Chrome on the agent's profile."""
    with plain_chrome(f"{BASE}/login"):
        hitl.ask("Plain Chrome is open in noVNC (http://localhost:6080) at Overleaf. Click 'Log in with Google', "
                 "choose the agent's account, accept anything Overleaf asks, wait for the project list, then answer 'done'.")
    log("overleaf: manual login finished")


def account_info(page: Page) -> dict:
    """User id, email and whether Git integration (needed for the MCP backend) is available."""
    page.goto(f"{BASE}/project", wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    git = None
    for name in ("ol-user", "ol-userFeatures", "ol-features"):
        raw = meta(page, name)
        if raw and "gitBridge" in raw:
            m = re.search(r'"gitBridge"\s*:\s*(true|false)', raw)
            git = m.group(1) == "true" if m else None
            break
    return {"user_id": meta(page, "ol-user_id"), "email": meta(page, "ol-usersEmail"), "git_bridge": git}


def create_project(page: Page, files: dict[str, str], name: str) -> str:
    """Create a project from in-memory files via POST /docs ("Open in Overleaf"). Returns the project id."""
    page.goto(f"{BASE}/project", wait_until="domcontentloaded")  # same-site POST: session cookies are sent
    page.evaluate(
        """([base, files, name]) => {
            const f = document.createElement('form');
            f.method = 'POST'; f.action = base + '/docs';
            const add = (k, v) => { const i = document.createElement('input'); i.type = 'hidden'; i.name = k; i.value = v; f.appendChild(i); };
            for (const [fname, text] of Object.entries(files)) { add('encoded_snip[]', encodeURIComponent(text)); add('snip_name[]', fname); }
            add('project_name', name);
            document.body.appendChild(f); f.submit();
        }""",
        [BASE, files, name],
    )
    try:
        page.wait_for_url(re.compile(r"/project/[0-9a-f]{24}"), timeout=60_000)
    except PlaywrightTimeout as e:
        raise OverleafError(f"project creation didn't open a project (at {page.url})") from e
    return re.search(r"/project/([0-9a-f]{24})", page.url).group(1)


def _csrf(page: Page, project_id: str) -> str:
    if f"/project/{project_id}" not in page.url:
        page.goto(f"{BASE}/project/{project_id}", wait_until="domcontentloaded")
    token = meta(page, "ol-csrfToken")
    if not token:
        raise OverleafError("no CSRF token on the project page")
    return token


def compile_pdf(page: Page, project_id: str) -> bytes:
    token = _csrf(page, project_id)
    resp = page.request.post(
        f"{BASE}/project/{project_id}/compile?auto_compile=true",
        data=json.dumps({"rootDoc_id": None, "draft": False, "check": "silent", "incrementalCompilesEnabled": True,
                         "stopOnFirstError": False}),
        headers={"X-Csrf-Token": token, "Content-Type": "application/json", "Accept": "application/json"},
        timeout=180_000,
    )
    if not resp.ok:
        raise OverleafError(f"compile request failed: HTTP {resp.status}")
    result = resp.json()
    if result.get("status") != "success":
        raise OverleafError(f"compile status {result.get('status')!r}")
    pdf = next((f for f in result.get("outputFiles", []) if f.get("path") == "output.pdf"), None)
    if not pdf:
        raise OverleafError("compile produced no output.pdf")
    host = result.get("pdfDownloadDomain") or BASE
    url = host + pdf["url"] + (f"?clsiserverid={result['clsiServerId']}" if result.get("clsiServerId") else "")
    got = page.request.get(url, timeout=120_000)
    if not got.ok or not got.body().startswith(b"%PDF"):
        raise OverleafError(f"PDF download failed: HTTP {got.status}")
    return got.body()


def download_zip(page: Page, project_id: str) -> bytes:
    got = page.request.get(f"{BASE}/project/{project_id}/download/zip", timeout=120_000)
    if not got.ok or not got.body().startswith(b"PK"):
        raise OverleafError(f"zip download failed: HTTP {got.status}")
    return got.body()


def delete_project(page: Page, project_id: str) -> bool:
    token = _csrf(page, project_id)
    for method in ("delete",):
        resp = page.request.fetch(f"{BASE}/project/{project_id}", method=method.upper(),
                                  headers={"X-Csrf-Token": token})
        if resp.ok:
            return True
    return False
