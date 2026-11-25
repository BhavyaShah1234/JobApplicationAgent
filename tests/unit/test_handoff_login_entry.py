"""An initial shared-login handoff needs explicit trust and a credential-only form."""
import pytest
from playwright.sync_api import sync_playwright

from jobagent.tools import browser, desktop_browser

POSTING='https://careers.example.test/jobs/42/job'
LOGIN='https://identity.example.test/u/login/password'
FORM='<h1>Enter Your Password</h1><form><input type=password name=password><button>Log in</button></form>'


@pytest.fixture(scope='module')
def chrome():
    with sync_playwright() as pw:
        instance=pw.chromium.launch(channel='chrome',headless=True)
        yield instance
        instance.close()


@pytest.mark.parametrize('case,expected',[
    ('declared_login',True),('undeclared_login',False),('other_job',False),
    ('private_login',False),('profile_heading',False),('resume',False),
    ('address',False),('two_passwords',False),('no_password',False),
    ('hidden_password',False),('separate_form',False),('same_job',True),('fieldset',True),
])
def test_initial_login_exception_cannot_attach_to_an_application_or_untrusted_tab(chrome,monkeypatch,case,expected):
    monkeypatch.setattr(desktop_browser,'public_destination',lambda u:'127.0.0.1' not in u)
    url=LOGIN;related=['https://identity.example.test'];html=FORM
    if case=='undeclared_login':related=[]
    if case=='other_job':url='https://careers.example.test/jobs/99/candidate';related=[url]
    if case=='private_login':url='https://127.0.0.1/login';related=[url]
    if case=='profile_heading':html=FORM.replace('Enter Your Password','Candidate Profile')
    if case=='resume':html=FORM.replace('<button>','<input type=file hidden><button>')
    if case=='address':html=FORM.replace('<button>','<input name=address><button>')
    if case=='two_passwords':html=FORM.replace('<button>','<input type=password name=confirm><button>')
    if case=='no_password':html=FORM.replace('type=password','type=email')
    if case=='hidden_password':html=FORM.replace('type=password','hidden type=password')
    if case=='separate_form':html+='<form><input name=address><button>Submit Application</button></form>'
    if case=='same_job':url='https://careers.example.test/jobs/42/candidate';html='<h1>Candidate Profile</h1>'
    if case=='fieldset':html=FORM.replace('<form>','<form><fieldset>').replace('</form>','</fieldset></form>')
    page=chrome.new_page()
    try:
        page.route('**/*',lambda r:r.fulfill(content_type='text/html',body=html))
        page.goto(url)
        assert browser.handoff_tab_allowed(POSTING,page,related) is expected
    finally:page.close()


def test_unreadable_login_never_gets_the_exception(monkeypatch):
    monkeypatch.setattr(desktop_browser,'public_destination',lambda u:True)
    class Page:
        url=LOGIN
        def evaluate(self,*args):raise RuntimeError('Navigation interrupted inspection')
    assert not browser.handoff_tab_allowed(POSTING,Page(),[LOGIN])


@pytest.mark.parametrize('change,expected',[
    ('none',True),('target',False),('missing_target',False),('task',False),
    ('origin',False),('job',False),('unbound',False),
])
def test_redirected_job_can_resume_only_on_its_previously_approved_task_target(monkeypatch,change,expected):
    monkeypatch.setattr(desktop_browser,'public_destination',lambda u:True)
    class Page:
        url='https://tenant.example.test/jobs/42/role/candidate?private=opaque'
        def evaluate(self,*args):pytest.fail('Not a login-page exception')
    page=Page();target='same-target'
    lease={'url':POSTING,'target_id':target,'origins':['https://tenant.example.test']}
    if change=='target':lease['target_id']='other-target'
    if change=='missing_target':target=None
    if change=='task':lease['url']='https://careers.example.test/jobs/99/job'
    if change=='origin':lease['origins']=['https://unrelated.example.test']
    if change=='job':page.url=page.url.replace('/42/','/99/')
    if change=='unbound':lease={}
    assert browser.handoff_tab_allowed(POSTING,page,lease=lease,target_id=target) is expected
