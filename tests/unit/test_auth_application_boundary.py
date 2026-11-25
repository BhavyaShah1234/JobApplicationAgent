"""The auth model may create accounts, but cannot submit application/profile forms."""
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


@pytest.mark.parametrize('html,blocked',[
    ('<form><input type=email><button>Continue</button></form>',False),
    ('<form><input type=password><button>Sign in</button></form>',False),
    ('<form><input type=email><input type=password><button>Create account</button></form>',False),
    ('<button>Submit Application</button>',True),
    ('<button>Submit Profile</button>',True),
    ('<button>Apply now</button>',True),
    ('<form><input type=file hidden><button>Continue</button></form>',True),
])
def test_auth_application_boundary_is_distinct_from_account_creation(page,html,blocked):
    page.set_content(html)
    assert bool(portal_agent.application_action_refusal(page.locator('button')))==blocked


def test_enter_on_a_credential_cannot_send_its_combined_application_form(page):
    page.set_content('<form><input type=password><button>Submit Profile</button></form>')
    assert portal_agent.application_action_refusal(page.locator('input'))


@pytest.mark.parametrize('action',['click','press_enter'])
def test_auth_model_application_action_is_stopped_before_dispatch(page,monkeypatch,tmp_path,action):
    page.set_content('<h1>Candidate Profile</h1><form onsubmit="window.sent=true;return false">'
                     '<input type=file aria-label=Resume><button>Submit Profile</button></form>')
    page.wait_for_timeout=lambda ms:None
    decision=portal_agent.AuthAction(action=action,element=1,frame=0,thought='Send this form')
    model=SimpleNamespace(with_structured_output=lambda *a,**kw:SimpleNamespace(invoke=lambda msgs:decision))
    monkeypatch.setattr(portal_agent.llm,'writer',lambda *a,**kw:model)
    monkeypatch.setattr(portal_agent.llm,'structured_invoke',lambda *a,**kw:decision)
    monkeypatch.setattr(portal_agent,'_lockout_file',lambda host:tmp_path/'lockout.json')
    monkeypatch.setattr(portal_agent,'_rejections_file',lambda host:tmp_path/'rejections.json')
    result=portal_agent._run(page,page.url,{},None,None,'company.example.test',lambda msg:None,None,[],'register',[])
    assert result['state']=='failed' and 'no application was sent' in result['reason']
    assert not page.evaluate('window.sent===true')
