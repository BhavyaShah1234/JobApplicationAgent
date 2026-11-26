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


@pytest.mark.parametrize('case,expected',[
    ('identifier',True),('undeclared',False),('wrong_host',False),('wrong_path',False),
    ('profile_heading',False),('extra_address',False),('hidden_password',False),('file',False),
    ('actual_identifier_structure',True),('provider_extra_address',False),('two_credential_forms',False),
    ('visible_unnamed_password',False),('autofill_wrong_host',False),
])
def test_shared_identifier_handoff_stays_credential_only_and_explicitly_trusted(chrome,monkeypatch,case,expected):
    monkeypatch.setattr(desktop_browser,'public_destination',lambda u:True)
    url='https://login.icims.com/u/login/identifier';related=['https://login.icims.com']
    html='<h1>Welcome</h1><form><label>Username or email address<input name=username></label><button>Continue</button></form>'
    if case=='undeclared':related=[]
    if case=='wrong_host':url=url.replace('login.icims.com','other.example.test');related=[url]
    if case=='wrong_path':url=url.replace('/identifier','/newsletter')
    if case=='profile_heading':html=html.replace('Welcome','Candidate Profile')
    if case=='extra_address':html=html.replace('<button>','<input name=address><button>')
    if case=='hidden_password':html=html.replace('<button>','<input type=password name=password hidden><button>')
    if case=='file':html=html.replace('<button>','<input type=file hidden><button>')
    if case in {'actual_identifier_structure','provider_extra_address','two_credential_forms','visible_unnamed_password','autofill_wrong_host'}:
        html=html.replace('<button>','<input type=password style="display:none"><button>')
        html+=''.join('<form><input type=hidden name=state><input type=hidden name=connection><button type=submit>Continue with '+provider+'</button></form>'
                      for provider in ['Corporate Login','Linkedin','Apple','Facebook','Indeed','Microsoft','Google'])
    if case=='provider_extra_address':html=html.replace('name=connection>','name=connection><input name=address>',1)
    if case=='two_credential_forms':html+='<form><input name=email><button>Continue</button></form>'
    if case=='visible_unnamed_password':html=html.replace('style="display:none"','')
    if case=='autofill_wrong_host':url=url.replace('login.icims.com','other.example.test');related=[url]
    page=chrome.new_page()
    try:
        page.route('**/*',lambda r:r.fulfill(content_type='text/html',body=html));page.goto(url)
        assert browser.handoff_tab_allowed(POSTING,page,related) is expected
    finally:page.close()


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
