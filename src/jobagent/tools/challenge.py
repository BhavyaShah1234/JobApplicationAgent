"""Bot-protection challenges: detect them, hand them to the user, never solve them automatically.

`goto()` is the agent's navigation entry point: it backs off on 429/5xx responses and, when a
challenge page (Cloudflare, reCAPTCHA, hCaptcha, Google's "unusual traffic", PerimeterX, DataDome, …)
appears, captures it and waits for the user to resolve it in noVNC, then continues on its own.
"""

import random
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from playwright.sync_api import Page, Response
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from jobagent.config import get_settings
from jobagent.tools.browser import screenshot
from jobagent.tools.hitl import Hitl

# (name, text pattern on the page / title)
_TEXT_SIGNS = [
    ("cloudflare", re.compile(r"(?i)just a moment\.\.\.|checking (if the site connection is secure|your browser)|"
                              r"verify(ing)? you are (a )?human|cf-chl|ray id:.*cloudflare|performance & security by cloudflare")),
    ("google-unusual-traffic", re.compile(r"(?i)our systems have detected unusual traffic|/sorry/index")),
    ("perimeterx", re.compile(r"(?i)press (&|and) hold|px-captcha|please verify you are a human")),
    ("datadome", re.compile(r"(?i)datadome|captcha-delivery\.com")),
    ("access-denied", re.compile(r"(?i)^\s*access denied\b|you don.t have permission to access|request blocked|"
                                 r"blocked by .{0,40}security|are you a robot\??")),
]
# (name, CSS selector for an embedded challenge widget)
_WIDGET_SIGNS = [
    ("recaptcha", 'iframe[src*="recaptcha/"], iframe[title*="reCAPTCHA" i]'),
    ("hcaptcha", 'iframe[src*="hcaptcha.com"]'),
    ("turnstile", 'iframe[src*="challenges.cloudflare.com"], .cf-turnstile'),
    ("arkose", 'iframe[src*="arkoselabs"], iframe[src*="funcaptcha"]'),
]


# Is the element really shown: no ancestor hidden or (nearly) transparent, and inside the viewport horizontally
_SHOWN_JS = """e => { for (let n = e; n && n.nodeType === 1; n = n.parentElement) { const s = getComputedStyle(n);
  if (s.visibility === 'hidden' || s.display === 'none' || parseFloat(s.opacity) < 0.1) return false; }
  const r = e.getBoundingClientRect(); return r.right > 0 && r.left < innerWidth && r.top < innerHeight; }"""


@dataclass
class Challenge:
    kind: str
    url: str
    excerpt: str


def detect(page: Page) -> Challenge | None:
    """Return the challenge on the page, if any. Visible widgets count; hidden/invisible reCAPTCHA badges don't."""
    try:
        title = page.title()
        text = page.locator("body").inner_text(timeout=5_000)
    except PlaywrightTimeout:
        return None
    blob = f"{title}\n{text[:4000]}\n{page.url}"
    for kind, pattern in _TEXT_SIGNS:
        if pattern.search(blob):
            return Challenge(kind, page.url, " ".join(text.split())[:200])
    # Widgets can sit inside a portal's own iframe (iCIMS puts hCaptcha there): look in every frame.
    for frame in page.frames:
        for kind, selector in _WIDGET_SIGNS:
            try:
                widget = frame.locator(selector)
                for i in range(min(widget.count(), 3)):
                    el = widget.nth(i)
                    box = el.bounding_box()
                    # Invisible reCAPTCHA v3 / badge-only frames are tiny or off-screen, and portals pre-load a
                    # full-size challenge frame kept hidden (opacity 0) until it's needed: neither is a challenge.
                    if box and box["width"] >= 100 and box["height"] >= 60 and el.is_visible() and \
                            el.evaluate(_SHOWN_JS) and box["y"] + box["height"] > 0:
                        return Challenge(kind, page.url, " ".join(text.split())[:200])
            except Exception:
                continue  # a frame that went away mid-check
    return None


def wait_for_user(page: Page, challenge: Challenge, hitl: Hitl, log=print, timeout_s: float | None = 1800) -> None:
    """Capture the challenge and block until it's gone (solved in noVNC) or the user answers 'done'."""
    shot = str(get_settings().state_dir / "logs" / f"challenge-{challenge.kind}-{datetime.now(UTC):%H%M%S}.png")
    screenshot(page, shot)
    log(f"challenge: {challenge.kind} at {challenge.url} | {challenge.excerpt} | screenshot {shot}")
    hitl.ask(
        f"A {challenge.kind} check is blocking {challenge.url} (screenshot {shot}). Solve it in noVNC "
        "(http://localhost:6080); the agent continues by itself when it's gone, or answer 'done'.",
        done_when=lambda: detect(page) is None,
        timeout_s=timeout_s,
        poll_s=3,
    )
    log(f"challenge: {challenge.kind} cleared")


def goto(page: Page, url: str, hitl: Hitl, log=print, attempts: int = 5, base_delay_s: float = 5.0,
         wait_until: str = "domcontentloaded") -> Response | None:
    """Navigate with exponential backoff (+ jitter) on 429/5xx and a user hand-off for challenges."""
    response = None
    for attempt in range(attempts):
        response = page.goto(url, wait_until=wait_until)
        status = response.status if response else 0
        if status == 429 or 500 <= status < 600:
            retry_after = response.headers.get("retry-after", "") if response else ""
            delay = float(retry_after) if retry_after.isdigit() else base_delay_s * 2**attempt
            delay *= random.uniform(1.0, 1.3)
            log(f"navigate: {url} answered {status}; retrying in {delay:.0f}s ({attempt + 1}/{attempts})")
            time.sleep(delay)
            continue
        page.wait_for_timeout(1500)  # challenge interstitials often swap in right after load
        challenge = detect(page)
        if challenge:
            wait_for_user(page, challenge, hitl, log)
        return response
    raise RuntimeError(f"{url} kept answering {response.status if response else 'nothing'} after {attempts} attempts")
