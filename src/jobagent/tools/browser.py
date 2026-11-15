"""Playwright persistent Chrome context (real Chrome, headful under Xvfb by default) and helpers."""

import os
import random
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path

from playwright.sync_api import BrowserContext, Locator, Page, sync_playwright
from playwright.sync_api import Error as PlaywrightError

from jobagent.config import Settings, get_settings

LOCK_FILES = ("SingletonLock", "SingletonSocket", "SingletonCookie")


class ProfileInUse(RuntimeError):
    """Another live Chrome has the agent's profile open (a profile can be used by one Chrome at a time)."""


def _lock_pid(profile: Path) -> int | None:
    """Chrome's SingletonLock is a symlink to '<hostname>-<pid>'."""
    lock = profile / "SingletonLock"
    try:
        return int(os.readlink(lock).rsplit("-", 1)[1])
    except (OSError, ValueError, IndexError):
        return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def clear_stale_lock(profile: Path) -> str:
    """Remove a lock left by a killed Chrome; refuse if the owner is still running.

    Returns 'none', 'stale lock removed' (raises ProfileInUse if the profile is genuinely open).
    """
    if not (profile / "SingletonLock").is_symlink():
        return "none"
    pid = _lock_pid(profile)
    if pid is not None and _pid_alive(pid):
        raise ProfileInUse(f"the browser profile is open in another Chrome (pid {pid}); close it first")
    for name in LOCK_FILES:
        (profile / name).unlink(missing_ok=True)
    return "stale lock removed"


@contextmanager
def browser_lock(settings: Settings | None = None, log=print, poll_s: float = 5.0) -> Iterator[None]:
    """Exclusive use of the agent's single Chrome profile across processes (flock: released if a run dies).

    A second run waits its turn instead of colliding (collisions close the other run's browser).
    """
    import fcntl

    s = settings or get_settings()
    path = s.state_dir / "browser.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+") as f:
        waited = 0.0
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if waited % 60 == 0:
                    f.seek(0)
                    holder = f.read().strip() or "another run"
                    log(f"browser busy ({holder}); waiting for it to finish")
                time.sleep(poll_s)
                waited += poll_s
        f.seek(0)
        f.truncate()
        f.write(f"pid {os.getpid()}: {' '.join(sys.argv[1:])[:120]}")
        f.flush()
        try:
            yield
        finally:
            f.seek(0)
            f.truncate()
            fcntl.flock(f, fcntl.LOCK_UN)


@contextmanager
def persistent_context(settings: Settings | None = None) -> Iterator[BrowserContext]:
    s = settings or get_settings()
    s.chrome_profile_dir.mkdir(parents=True, exist_ok=True)
    with browser_lock(s), _persistent_context(s) as ctx:
        yield ctx


@contextmanager
def application_context(mode, url, settings=None, *, related_urls=()):
    """Docker by default; opt-in desktop uses the host broker and the same Voyager application code."""
    if mode == 'docker':
        with persistent_context(settings) as ctx:
            yield ctx
        return
    if mode != 'desktop':
        raise ValueError('Unknown browser mode')
    import socket
    import json
    from jobagent.tools import desktop_browser
    if not desktop_browser.allowed(url):
        raise ValueError('Desktop applications require a public HTTPS URL')
    s = settings or get_settings()
    with browser_lock(s):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(25)
            path = s.state_dir / desktop_browser.ROOT / 'bridge.sock'
            if not path.exists():
                path = s.state_dir / 'carvana-desktop' / 'bridge.sock'  # updated legacy serve command
            try:
                client.connect(str(path))
            except OSError as exc:
                raise RuntimeError('Start the host desktop bridge: PYTHONPATH=src python3 -m '
                                   'jobagent.tools.desktop_browser serve') from exc
            client.sendall((json.dumps({'action':'open','protocol':desktop_browser.PROTOCOL,'url':url})+'\n').encode())
            reply = json.loads(client.makefile('rb').readline(8192))
        if reply.get('ok') and reply.get('protocol') != desktop_browser.PROTOCOL:
            raise RuntimeError('Restart the updated host desktop bridge: PYTHONPATH=src python3 -m '
                               'jobagent.tools.desktop_browser serve')
        if not reply.get('ok') or reply.get('protocol') != desktop_browser.PROTOCOL or \
                type(reply.get('port')) is not int or not 1024 <= reply['port'] <= 65535 or reply.get('url') != url:
            raise RuntimeError(reply.get('error', 'Desktop bridge identity mismatch'))
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{reply['port']}")
            fresh = bool(reply.get('fresh', False))
            deadline = time.monotonic()+15
            while True:
                pages = [p for c in browser.contexts for p in c.pages]
                if len(pages) == 1 and desktop_browser.allowed(pages[0].url):
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError('Dedicated application tab missing or ambiguous')
                time.sleep(.25)
            page = pages[0]
            ctx = page.context
            from jobagent.tools.shadow import install
            install(pw, ctx)
            from jobagent.tools.challenge import Blocked
            if not desktop_browser.public_destination(url):
                raise Blocked('The requested application URL is not a public HTTPS destination')
            try:                                              # the browser target this lease is bound to
                target_id = ctx.new_cdp_session(page).send('Target.getTargetInfo')['targetInfo']['targetId']
            except Exception as exc:
                raise Blocked('The identity of the application tab could not be verified; application stopped') from exc
            if not reply.get('guarded'):                      # not started through the public-only proxy: not trusted
                raise Blocked('This desktop browser was started without the public-only network proxy; stop it and restart '
                              'the host desktop bridge (PYTHONPATH=src python3 -m jobagent.tools.desktop_browser serve)')
            policy = desktop_browser.Scope(ctx, page, url, related_urls, fresh=fresh,
                                           store=desktop_browser.agent_profile_root(path.parent, url) / 'approved.json',
                                           target_id=target_id)
            policy(page)
            ctx.jobagent_policy = policy
            ctx.jobagent_page = page
            yield ctx  # disconnect only; leave the dedicated browser available for human review


@contextmanager
def _persistent_context(s: Settings) -> Iterator[BrowserContext]:
    clear_stale_lock(s.chrome_profile_dir)
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
        from jobagent.tools.shadow import install
        install(pw, ctx)
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
    with browser_lock(s), _plain_chrome(url, s) as proc:
        yield proc


@contextmanager
def _plain_chrome(url: str, s: Settings) -> Iterator[subprocess.Popen]:
    clear_stale_lock(s.chrome_profile_dir)
    log_path = s.state_dir / "logs" / "plain-chrome.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = open(log_path, "w")
    proc = subprocess.Popen(
        # --no-sandbox: Chrome's sandbox needs user namespaces, which Docker doesn't allow by default
        # (the automated browser gets the same flag from Playwright). Nothing else is changed.
        ["google-chrome", "--no-sandbox", f"--user-data-dir={s.chrome_profile_dir}", "--no-first-run",
         "--no-default-browser-check", "--window-position=0,0", "--window-size=1920,1080", url],
        env={**os.environ, "DISPLAY": s.display},
        stdout=log_file, stderr=subprocess.STDOUT,
    )
    try:
        # Fail loudly if Chrome dies on start instead of leaving the user an empty noVNC screen.
        try:
            code = proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            code = None
        if code is not None:
            tail = log_path.read_text(errors="replace").strip().splitlines()[-3:]
            raise RuntimeError(f"Chrome exited on start (code {code}): {' | '.join(tail)}")
        yield proc
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
        log_file.close()


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
