import os
import shutil
import subprocess

import pytest

from jobagent.tools.browser import ProfileInUse, clear_stale_lock


def _lock(profile, pid: int):
    os.symlink(f"somehost-{pid}", profile / "SingletonLock")
    for name in ("SingletonSocket", "SingletonCookie"):
        (profile / name).write_text("x")


def test_no_lock(tmp_path):
    assert clear_stale_lock(tmp_path) == "none"


def test_stale_lock_from_dead_chrome_is_removed(tmp_path):
    dead = subprocess.Popen(["true"])
    dead.wait()
    _lock(tmp_path, dead.pid)
    assert clear_stale_lock(tmp_path) == "stale lock removed"
    assert not any((tmp_path / n).exists() or (tmp_path / n).is_symlink()
                   for n in ("SingletonLock", "SingletonSocket", "SingletonCookie"))


def test_live_lock_is_refused(tmp_path):
    _lock(tmp_path, os.getpid())
    with pytest.raises(ProfileInUse):
        clear_stale_lock(tmp_path)
    assert (tmp_path / "SingletonLock").is_symlink()


@pytest.mark.skipif(shutil.which("google-chrome") is None, reason="needs Chrome (run in the container)")
def test_challenge_detection_on_fake_pages():
    from playwright.sync_api import sync_playwright

    from jobagent.steps.browser import _FAKE_CHALLENGES, _NORMAL_PAGES
    from jobagent.tools import challenge

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page()
        for kind, html in _FAKE_CHALLENGES.items():
            page.set_content(html)
            assert (found := challenge.detect(page)) and found.kind == kind, kind
        for html in _NORMAL_PAGES:
            page.set_content(html)
            assert challenge.detect(page) is None, html
        browser.close()
