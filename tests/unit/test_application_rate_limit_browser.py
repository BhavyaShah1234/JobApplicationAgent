"""Real navigation must expose the status used by the no-challenge rate-limit guard."""
from types import SimpleNamespace
import pytest
from playwright.sync_api import sync_playwright
from jobagent.steps import apply as A


def no_human(*args,**kwargs):
    """A fixture 429 without a visible challenge needs no owner intervention."""
    pytest.fail('HTTP429 must not request human verification')


@pytest.mark.parametrize('already_open',[False,True])
def test_real_navigation_status_and_desktop_timing_stop_429(already_open):
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True)
        page=browser.new_page();url='https://fixture.test/rate-limited'
        page.route(url,lambda r:r.fulfill(status=429,body='<html><body></body></html>'))
        if already_open:page.goto(url)
        with pytest.raises(A.RateLimited):
            A._start(page,url,no_human,lambda _:None,hitl=SimpleNamespace(ask=no_human),already_open=already_open)
        browser.close()
