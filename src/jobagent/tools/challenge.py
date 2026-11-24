"""Bot-protection challenges: detect them, hand them to the user, never solve them automatically.

`goto()` is the agent's navigation entry point: it backs off on 429/5xx responses and, when a
challenge page (Cloudflare, reCAPTCHA, hCaptcha, Google's "unusual traffic", PerimeterX, DataDome, …)
appears, captures it and waits for the user to resolve it in noVNC, then continues on its own.
"""

import random
import re
import time
import json
import weakref
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from dataclasses import dataclass
from datetime import UTC, datetime

from playwright.sync_api import Page, Response
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from jobagent.tools.hitl import Hitl


def get_settings():
    from jobagent.config import get_settings as settings
    return settings()


def screenshot(page, path):
    from jobagent.tools.browser import screenshot as capture
    return capture(page, path)


class Blocked(RuntimeError):
    pass


class DesktopFallback(Blocked):
    """The Docker application encountered a challenge and should open desktop Chrome."""


def safe_url(url):
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, p.path, "", ""))

# (name, text pattern on the page / title)
_TEXT_SIGNS = [
    ("cloudflare", re.compile(r"(?i)just a moment\.\.\.|checking (if the site connection is secure|your browser)|"
                              r"verify(ing)? you are (a )?human|performing security verification|cf-chl|ray id:.*cloudflare|performance & security by cloudflare")),
    ("google-unusual-traffic", re.compile(r"(?i)our systems have detected unusual traffic|/sorry/index")),
    ("human-verification", re.compile(r"(?is)\bverification required\b.{0,1800}\b(?:slide right to secure your access|"
                                       r"we detected unusual activity from your (?:device|network))\b")),
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

# This notice can appear below a long job description only after Submit. It
# requires human action in the same attempt, rather than another automatic click.
_APPLICATION_EMAIL_CODE = re.compile(
    r"(?is)\b(?:verification|security) code (?:was|has been) sent\b.{0,400}"
    r"\bto (?:submit|complete) (?:your|the) application\b.{0,200}\benter\b.{0,100}\bcode\b"
)


@dataclass
class Challenge:
    kind: str
    url: str
    excerpt: str


# A blank page whose only content is a verification provider's frame (SmartRecruiters shows DataDome's slider from
# geo.captcha-delivery.com in an iframe, with an empty top-level body): named by the frame's address.
_FRAME_KINDS = [
    ("datadome", re.compile(r"(?i)captcha-delivery\.com/captcha")),
    ("hcaptcha", re.compile(r"(?i)hcaptcha\.com/captcha")),
    ("cloudflare", re.compile(r"(?i)challenges\.cloudflare\.com/cdn-cgi/challenge-platform/.*/(?:turnstile|orchestrate)|/cdn-cgi/challenge-platform/h/[bg]/orchestrate")),
]


def _verification_frame(page: Page) -> str | None:
    try:
        urls = [f.url for f in page.frames if f.url and f is not page.main_frame]
    except Exception:
        return None
    for kind, pattern in _FRAME_KINDS:
        if any(pattern.search(u) for u in urls):
            return kind
    return None


def detect(page: Page, *, include_application_email: bool = True) -> Challenge | None:
    """Return the challenge on the page, if any. Visible widgets count; hidden/invisible reCAPTCHA badges don't."""
    try:
        title = page.title()
        text = page.locator("body").inner_text(timeout=5_000)
    except Exception:
        return Challenge("unknown", safe_url(page.url), "Page inspection failed")
    if not text.strip():
        if kind := _verification_frame(page):
            return Challenge(kind, safe_url(page.url), "Verification frame on a blank page")
        return Challenge("unknown", safe_url(page.url), "Page is blank or transitioning")
    if include_application_email and _APPLICATION_EMAIL_CODE.search(text):
        return Challenge("application-email-code", safe_url(page.url), "Application requires an emailed security code")
    blob = f"{title}\n{text[:4000]}\n{page.url}"
    for kind, pattern in _TEXT_SIGNS:
        if pattern.search(blob):
            return Challenge(kind, safe_url(page.url), "Verification detected")
    # Widgets can sit inside a portal's own iframe (iCIMS puts hCaptcha there): look in every frame.
    for frame in page.frames:
        for kind, selector in _WIDGET_SIGNS:
            try:
                response = frame.locator('input[name="cf-turnstile-response"], textarea[name="g-recaptcha-response"], textarea[name="h-captcha-response"]')
                if response.count() and any(response.nth(i).input_value() for i in range(response.count())):
                    continue  # presence only, never log or export the response token
                widget = frame.locator(selector)
                for i in range(min(widget.count(), 3)):
                    el = widget.nth(i)
                    if kind == 'recaptcha' and el.evaluate("e => !!e.closest('.grecaptcha-badge')"):
                        # The provider's branding badge can contain a 256x60 frame
                        # mostly outside the viewport. It is not an interactive
                        # challenge; the actual checkbox/challenge frames remain
                        # subject to the visibility checks below.
                        continue
                    box = el.bounding_box()
                    # Invisible reCAPTCHA v3 / badge-only frames are tiny or off-screen, and portals pre-load a
                    # full-size challenge frame kept hidden (opacity 0) until it's needed: neither is a challenge.
                    if box and box["width"] >= 100 and box["height"] >= 60 and el.is_visible() and \
                            el.evaluate(_SHOWN_JS) and box["y"] + box["height"] > 0:
                        return Challenge(kind, safe_url(page.url), "Visible verification widget")
            except Exception:
                return Challenge("unknown", safe_url(page.url), "Frame inspection failed")
    return None


def page_ready(page):
    return page.locator('input:visible:not([type=hidden]):not([type=password]), button:visible, a[href]:visible').count() > 0


class Controller:
    def __init__(self, hitl, ready=page_ready, out_dir=None, log=print, policy=None, desktop_fallback=False):
        self.hitl, self.ready, self.out_dir, self.log = hitl, ready, out_dir, log
        self.cycles = 0
        self.policy = policy
        self.desktop_fallback = desktop_fallback

    def wait(self, page, block, timeout_s=900):
        from jobagent.tools.hitl import HitlTimeout
        if self.desktop_fallback and block.kind != 'unknown':
            raise DesktopFallback(f'{block.kind} detected in Docker; opening the desktop application')
        if self.cycles >= 2 or self.hitl is None:
            raise Blocked("Human verification is unresolved; resolution limit reached or no human channel")
        self.cycles += 1
        out = Path(self.out_dir or get_settings().state_dir / "logs")
        out.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f')
        shot = out / f"challenge-{stamp}.png"
        page.bring_to_front()
        screenshot(page, str(shot))
        # Persist only diagnostic identifiers, never page bodies, tokens or cookie values.
        try:
            text = page.locator("body").inner_text(timeout=5000) if block.kind != "unknown" else ""
        except Exception:
            text = ""
        ray = re.search(r"(?i)ray id\s*:?\s*([a-f0-9]{8,32})", text)
        code = re.search(r"(?i)error(?: code)?\s*:?\s*(\d{3,6})", text)
        diagnostic = {"state": "awaiting_human", "kind": block.kind, "url": safe_url(page.url),
                      "cycle": self.cycles, "screenshot": shot.name, "at": stamp,
                      "ray_id": ray[1] if ray else None, "error_code": code[1] if code else None}
        path = out / f"challenge-{stamp}.json"
        path.write_text(json.dumps(diagnostic, indent=2))
        deadline = time.monotonic() + (900 if timeout_s is None else timeout_s)
        stable, last = 0, float('-inf')

        def resolved():
            nonlocal stable, last
            now = time.monotonic()
            if now >= deadline:
                return False
            if now - last < 2:
                return False
            last = now
            try:
                if self.policy:
                    self.policy(page)
                ok = detect(page) is None and self.ready(page)
            except Exception:
                ok = False
            stable = stable + 1 if ok else 0
            return stable >= 3

        try:
            while time.monotonic() < deadline:
                instructions = ("Enter the emailed security code and complete the verification on the page at "
                                if block.kind == 'application-email-code' else "Solve it in ")
                answer = self.hitl.ask(
                    f"{block.kind} verification at {safe_url(page.url)}. " + instructions
                    + ("the dedicated desktop Chrome window. " if self.policy else "http://localhost:6080/vnc.html. ")
                    + "'done' checks readiness; 'skip' stops safely.",
                    done_when=resolved, timeout_s=max(0, deadline-time.monotonic()), poll_s=2,
                )
                if answer.strip().lower() == "skip":
                    raise Blocked("Human skipped verification")
                # A person who answered 'done' just before the shared deadline still gets time for the three spaced readiness
                # checks (SAP: 'done' came 30 s before expiry, the re-ask had 23 s left and the run ended blocked).
                deadline = max(deadline, time.monotonic() + 90)
                for _ in range(3):
                    if resolved():
                        diagnostic['state'] = 'ready'
                        path.write_text(json.dumps(diagnostic, indent=2))
                        self.log("challenge: expected page verified ready")
                        return
                    page.wait_for_timeout(2000)
            raise HitlTimeout("Verification deadline expired")
        except (HitlTimeout, Blocked) as exc:
            diagnostic.update(state="blocked", reason=str(exc))
            path.write_text(json.dumps(diagnostic, indent=2))
            raise Blocked(str(exc)) from exc


_controllers = weakref.WeakKeyDictionary()


@contextmanager
def session(page, hitl, ready=page_ready, out_dir=None, log=print, policy=None, desktop_fallback=False):
    controller = Controller(hitl, ready, out_dir, log, policy, desktop_fallback)
    _controllers[page] = controller
    try:
        yield controller
    finally:
        _controllers.pop(page, None)


def guard(page, *, ready=None):
    """Preserve the session policy and limits; optionally check a stage-specific
    destination (such as a receipt page) during this human handoff only."""
    controller = _controllers.get(page)
    if controller and controller.policy:
        controller.policy(page)
    if controller and (block := detect(page)):
        previous_ready = controller.ready
        if ready is not None:
            controller.ready = ready
        try:
            controller.wait(page, block)
        finally:
            controller.ready = previous_ready
        return True
    return False


def wait_for_user(page: Page, challenge: Challenge, hitl: Hitl, log=print, timeout_s: float | None = 900,
                  ready=page_ready) -> None:
    controller = _controllers.get(page) or Controller(hitl, ready, log=log)
    controller.wait(page, challenge, timeout_s)


def goto(page: Page, url: str, hitl: Hitl, log=print, attempts: int = 5, base_delay_s: float = 5.0,
         wait_until: str = "domcontentloaded") -> Response | None:
    """Navigate with exponential backoff (+ jitter) on 429/5xx and a user hand-off for challenges."""
    response = None
    for attempt in range(attempts):
        response = page.goto(url, wait_until=wait_until)
        page.wait_for_timeout(1500)
        block = detect(page)
        if block and block.kind != "unknown":
            wait_for_user(page, block, hitl, log)
            return response
        status = response.status if response else 0
        if status == 429 or 500 <= status < 600:
            retry_after = response.headers.get("retry-after", "") if response else ""
            delay = float(retry_after) if retry_after.isdigit() else base_delay_s * 2**attempt
            delay *= random.uniform(1.0, 1.3)
            log(f"navigate: {safe_url(url)} answered {status}; retrying in {delay:.0f}s ({attempt + 1}/{attempts})")
            time.sleep(delay)
            continue
        page.wait_for_timeout(1500)  # challenge interstitials often swap in right after load
        challenge = detect(page)
        if challenge:
            wait_for_user(page, challenge, hitl, log)
        return response
    raise RuntimeError(f"{safe_url(url)} kept answering {response.status if response else 'nothing'} after {attempts} attempts")
