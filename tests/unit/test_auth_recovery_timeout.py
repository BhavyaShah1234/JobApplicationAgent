"""Recovery stops after one missing-email wait; auth snapshots omit opaque URL query values."""
from types import SimpleNamespace
import pytest
from playwright.sync_api import sync_playwright
from jobagent.tools import portal_agent


@pytest.fixture
def page():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True)
        try:yield browser.new_page()
        finally:browser.close()


def test_snapshot_does_not_export_auth_state_or_ticket_query_values(page):
    page.route('https://auth.example.test/**',lambda route:route.fulfill(status=200,content_type='text/html',
        body='<h1>Sign in</h1><input aria-label=Email>'))
    page.goto('https://auth.example.test/login?state=private-state-fixture&ticket=private-ticket-fixture')
    view,_=portal_agent.snapshot(page)
    assert 'https://auth.example.test/login' in view
    assert 'private-state-fixture' not in view and 'private-ticket-fixture' not in view


def test_missing_reset_email_ends_the_flow_instead_of_waiting_or_requesting_again(page,monkeypatch,tmp_path):
    page.set_content('<h1>Check Your Email</h1><p>Check your email for password reset instructions.</p>')
    page.wait_for_timeout=lambda ms:None
    calls=[]
    def missing(*args,**kwargs):
        calls.append(1)
        raise TimeoutError('fixture: no email')
    decision=portal_agent.AuthAction(action='link_sent',thought='A reset link was requested')
    monkeypatch.setattr(portal_agent.llm,'writer',lambda *a,**kw:object())
    monkeypatch.setattr(portal_agent.llm,'structured_invoke',lambda *a,**kw:decision)
    monkeypatch.setattr(portal_agent,'MAX_STEPS',2)
    monkeypatch.setattr(portal_agent,'_lockout_file',lambda host:tmp_path/'lockout.json')
    monkeypatch.setattr(portal_agent,'_rejections_file',lambda host:tmp_path/'rejections.json')
    result=portal_agent._run(page,page.url,{},SimpleNamespace(wait_for_verification=missing),None,
        'company.example.test',lambda msg:None,None,[],'reset_password',[])
    assert calls==[1] and result['state']=='failed' and 'No recovery email' in result['reason']
    assert not result['registered'] and not result['verified_mail']
