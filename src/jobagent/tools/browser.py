"""Playwright persistent Chrome context (real Chrome, headful under Xvfb by default).

Step 0 only needs launch; navigation/snapshot/form tools land in step 2.
"""

import os
import random
import signal
import subprocess
import time
from contextlib import contextmanager
from collections.abc import Iterator

from playwright.sync_api import BrowserContext, Locator, Page, sync_playwright
from playwright.sync_api import Error as PlaywrightError

from jobagent.config import Settings, get_settings


@contextmanager
def persistent_context(settings: Settings | None = None) -> Iterator[BrowserContext]:
    s = settings or get_settings()
    s.chrome_profile_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=str(s.chrome_profile_dir),
            channel=s.chrome_channel or None,
            headless=s.headless,
            viewport=None,  # use the real window size, like a normal user
            # --start-maximized makes Page.captureScreenshot fail under Xvfb + fluxbox; size the window explicitly.
            args=[
                "--window-position=0,0",
                "--window-size=1920,1080",
                # Under Xvfb, Chrome can consider its window occluded and stop painting, which makes
                # screenshots fail intermittently. Keep rendering on for the (only) visible window.
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
                "--disable-background-timer-throttling",
            ],
        )
        # The initial blank tab of a headful persistent context reports a bogus window size under
        # Xvfb (outerHeight < innerHeight) and can't be screenshotted; replace it with a fresh tab.
        stale = [p for p in ctx.pages if p.url == "about:blank"]
        ctx.new_page()
        for p in stale:
            p.close()
        try:
            yield ctx
        finally:
            ctx.close()


def type_human(locator: Locator, text: str) -> None:
    """Type with human-like, slightly varied keystroke timing."""
    locator.click()
    locator.fill("")
    for ch in text:
        locator.press_sequentially(ch, delay=0)
        time.sleep(random.uniform(0.05, 0.16))


def pause(lo: float = 0.6, hi: float = 1.8) -> None:
    time.sleep(random.uniform(lo, hi))


@contextmanager
def plain_chrome(url: str, settings: Settings | None = None) -> Iterator[subprocess.Popen]:
    """Open regular Chrome (no automation) on the agent's profile, for the user to drive in noVNC.

    The Playwright context must be closed first: a profile can only be open in one Chrome at a time.
    Chrome is closed gracefully afterwards so cookies and the session are flushed to the profile.
    """
    s = settings or get_settings()
    proc = subprocess.Popen(
        ["google-chrome", f"--user-data-dir={s.chrome_profile_dir}", "--no-first-run",
         "--no-default-browser-check", "--window-position=0,0", "--window-size=1920,1080", url],
        env={**os.environ, "DISPLAY": s.display},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        yield proc
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()


def screenshot(page: Page, path: str, attempts: int = 3) -> None:
    """Screenshot with a short retry for transient 'Unable to capture screenshot' errors."""
    for i in range(attempts):
        try:
            page.bring_to_front()
            page.screenshot(path=path)
            return
        except PlaywrightError:
            if i == attempts - 1:
                raise
            page.wait_for_timeout(500 * (i + 1))
