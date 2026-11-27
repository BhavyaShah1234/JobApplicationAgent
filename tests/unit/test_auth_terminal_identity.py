"""A model failure cannot erase independently verified current account identity."""
from types import SimpleNamespace
import pytest
from playwright.sync_api import sync_playwright
from jobagent.tools import portal_agent as PA


@pytest.mark.parametrize('goal',['sign_in','register'])
@pytest.mark.parametrize('html,expected',[
    ('<h2>Profile Information</h2><button>Sign Out</button><input type=email value="owner@example.test">',True),
    ('<h2>Profile Information</h2><button>Sign Out</button><input type=email value="other@example.test">',False),
    ('<h2>Profile Information</h2><button>Sign Out</button><input type=email value="owner@example.test"><input type=password>',False),
    ('<h2>Welcome owner</h2><button>Sign Out</button>',False),
    ('<h2>Profile Information</h2><button hidden>Sign Out</button><input type=email value="owner@example.test">',False),
    ('<h2>Profile Information</h2><button>Sign Out</button><input type=hidden name=email value="owner@example.test">',False),
])
def test_failed_terminal_action_requires_exact_current_profile_identity(monkeypatch,tmp_path,html,expected,goal):
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True);page=browser.new_page()
        page.route('**/*',lambda route:route.fulfill(body=html,content_type='text/html'))
        page.goto('https://portal.test/profile');real=page.wait_for_timeout;page.wait_for_timeout=lambda ms:real(min(ms,5))
        monkeypatch.setattr(PA,'too_many_recent_rejections',lambda *_:None)
        monkeypatch.setattr(PA,'recent_lockout',lambda *_:None)
        monkeypatch.setattr(PA,'_settle_unknown',lambda *_:None)
        monkeypatch.setattr(PA.llm,'writer',lambda *a,**k:object())
        monkeypatch.setattr(PA.llm,'structured_invoke',lambda *a,**k:PA.AuthAction(action='failed',thought='Already signed in; no registration needed'))
        result=PA.run(page,page.url,{'EMAIL':'owner@example.test','FIRST_NAME':'owner'},None,SimpleNamespace(ask=lambda *_:pytest.fail('No human needed')),'portal.test',log=lambda _:None,goal=goal)
        assert (result['state']=='signed_in') is expected
        assert not result['registered']
        browser.close()
