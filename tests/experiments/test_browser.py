import pytest
from playwright.sync_api import sync_playwright
from jobagent.experiments.engine import Candidate
from jobagent.experiments.discovery import Discovery
from jobagent.experiments.resolution import Fact
from jobagent.tools import shadow


@pytest.fixture
def page():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True);ctx=browser.new_context();shadow.install(pw,ctx)
        yield ctx.new_page()
        browser.close()


def test_never_submits_and_does_not_ask_known_facts(page):
    page.set_content('<form onsubmit="window.sent=true;return false"><label>Email<input type=email required></label><button>Apply</button></form>')
    def ask(q):pytest.fail('known fact was asked')
    result=Candidate([Fact('email',['Email'],'person@example.test','applicant')],ask).fill(page)
    assert result['ready'] and result['questions']==0
    assert not page.evaluate('!!window.sent')


def test_hidden_and_embedded_definitions_are_inventoried_but_not_filled(page):
    page.set_content('<label style="display:none">Employee Name<input></label><script type="application/json">{"questions":[{"label":"Clearance","type":"select","condition":"eligible"}]}</script>')
    d=Discovery(page)
    try:
        fields=d.inventory()['fields']
        assert any(x['label']=='Employee Name' and not x['visible'] for x in fields)
        assert any(x['label']=='Clearance' and x['potential'] for x in fields)
        assert page.locator('input').input_value()==''
    finally:d.close()


def test_unknown_questions_are_batched_once_including_optional(page):
    page.set_content('<label>Favorite tool<input required></label><label>Comment<input></label><button type=button>Apply</button>')
    calls=[]
    def ask(q):calls.append(q);return {str(x['id']):'Answer' for x in q}
    r=Candidate([],ask).fill(page)
    assert r['ready'] and len(calls)==1 and len(calls[0])==2


def test_validation_failure_does_not_report_ready_or_repeat_question(page):
    page.set_content('<label>Start<input required pattern="[0-9]{2}/[0-9]{2}/[0-9]{4}"></label><button type=button>Apply</button>')
    calls=[]
    def ask(q):calls.append(q);return {str(q[0]['id']):'01/2026'}
    r=Candidate([],ask).fill(page)
    assert not r['ready'] and len(calls)==1


def test_dom_conditional_branch_uses_known_fact(page):
    page.set_content('<label>Country<select onchange="document.getElementById(\'extra\').innerHTML=\'<label>State<input required></label>\'"><option>Choose</option><option>United States</option></select></label><div id=extra></div><button type=button>Apply</button>')
    r=Candidate([Fact('country',['Country'],'United States','applicant'),Fact('state',['State'],'Texas','applicant')],lambda q:pytest.fail(str(q))).fill(page)
    assert r['ready'] and page.get_by_label('State',exact=True).input_value()=='Texas'


def test_native_uploads_have_separate_roles_and_bytes(page,tmp_path):
    page.set_content('<label for=r>Resume</label><input id=r type=file required><label for=c>Cover Letter</label><input id=c type=file required><button type=button>Apply</button>')
    files={}
    for role in ('resume','cover_letter'):
        p=tmp_path/(role+'.pdf');p.write_bytes(('%PDF-1.4 '+role).encode());files[role]=str(p)
    r=Candidate([],lambda q:pytest.fail(str(q))).fill(page,files)
    assert r['ready']
    assert page.locator('#r').evaluate('e=>e.files[0].name')=='resume.pdf'
    assert page.locator('#c').evaluate('e=>e.files[0].name')=='cover_letter.pdf'


def test_model_cannot_supply_a_nonexistent_fact(page):
    from jobagent.experiments.engine import Mappings
    class Fake:
        model_name='fake'
        def with_structured_output(self,schema,**kwargs):
            from types import SimpleNamespace
            return SimpleNamespace(invoke=lambda _:Mappings(mappings=[{'field_id':0,'fact_id':'invented'}]))
    page.set_content('<label for=e>Clearance</label><input id=e required><button type=button>Apply</button>')
    r=Candidate([],lambda q:{},Fake()).fill(page)
    assert not r['ready'] and page.locator('#e').input_value()==''


def test_existing_schema_with_future_required_field_blocks_false_readiness(page):
    page.set_content('<script type=application/json>{"question":"Unresolved future field","type":"text","required":true}</script><button type=button>Apply</button>')
    r=Candidate([],lambda q:{}).fill(page)
    assert not r['ready']


def test_authentication_is_not_a_missing_personal_fact(page):
    page.set_content('<label>Password<input type=password></label><button>Sign In</button>')
    r=Candidate([],lambda q:pytest.fail('must not ask for/store a password')).fill(page)
    assert not r['ready'] and 'authentication' in r['blockers'][0]


def test_future_schema_delivered_after_a_known_answer(page):
    page.route('https://fixture.example/questions',lambda route:route.fulfill(headers={'access-control-allow-origin':'*'},content_type='application/json',body='{"questions":[{"label":"State","type":"text","required":true}]}'))
    page.set_content('<label for=c>Country</label><select id=c onchange="fetch(\'https://fixture.example/questions\').then(r=>r.json()).then(d=>document.getElementById(\'new\').innerHTML=\'<label for=s>State</label><input id=s required>\')"><option>Choose</option><option>United States</option></select><div id=new></div><button type=button>Apply</button>')
    r=Candidate([Fact('country',['Country'],'United States','fixture'),Fact('state',['State'],'Texas','fixture')],lambda q:pytest.fail(str(q))).fill(page)
    assert r['ready'] and page.locator('#s').input_value()=='Texas'
