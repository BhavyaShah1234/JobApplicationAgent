"""Recover one fieldless service error, without retrying challenges, rate limits or filled forms."""
import pytest
from playwright.sync_api import sync_playwright
from jobagent.steps import apply
from jobagent.tools import challenge

ERROR = '<p>Due to a technical issue, the application form is temporarily unavailable. Please try again later.</p><button>Reload</button>'
FORM = '<h1>Application</h1><input placeholder=Name><input type=email placeholder=Email><input type=tel placeholder=Phone><button>Submit Application</button>'


@pytest.mark.parametrize('scenario', ['recovers', 'persists', 'shadow_recovers', 'shadow_persists', 'filled', 'rate_limit', 'challenge'])
def test_temporary_application_error_has_a_single_safe_recovery(monkeypatch, scenario):
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=True)
        try:
            page = browser.new_page()
            source = 'https://jobs.example.test/application'
            requests = []
            def serve(route):
                if route.request.url != source:
                    return route.fulfill(status=200, content_type='text/html', body='<p>Verification fixture</p>')
                requests.append(route.request.method)
                html = ERROR
                if scenario.startswith('shadow_'):
                    html = '<h1>Developer</h1><div id=component></div><script>' + \
                        'component.attachShadow({mode:"open"}).innerHTML=' + repr(ERROR) + '</script>'
                if scenario == 'filled':
                    html += FORM
                elif scenario in ('recovers', 'shadow_recovers') and len(requests) > 1:
                    html = FORM
                if scenario == 'challenge':
                    html += '<iframe src="https://geo.captcha-delivery.com/captcha/" style="width:360px;height:260px"></iframe>'
                route.fulfill(status=429 if scenario == 'rate_limit' else 200,
                              content_type='text/html', body=html)
            page.route('**/*', serve)
            wait = page.wait_for_timeout
            page.wait_for_timeout = lambda ms: wait(min(ms, 10))
            monkeypatch.setattr(challenge, 'wait_for_user', lambda *a, **kw: (_ for _ in ()).throw(challenge.Blocked('human required')))
            if scenario in ('persists', 'shadow_persists'):
                with pytest.raises(apply.FormNotReady, match='temporarily unavailable after one reload'):
                    apply._start(page, source, None, lambda msg: None)
            elif scenario == 'rate_limit':
                with pytest.raises(apply.RateLimited):
                    apply._start(page, source, None, lambda msg: None)
            elif scenario == 'challenge':
                with pytest.raises(challenge.Blocked, match='human required'):
                    apply._start(page, source, None, lambda msg: None, hitl=object())
            else:
                view = apply._start(page, source, None, lambda msg: None)
                assert len(view.fields) == 3
            assert requests == ['GET'] * (2 if scenario in ('recovers', 'persists', 'shadow_recovers', 'shadow_persists') else 1)
        finally:
            browser.close()
