import copy
import json
from types import SimpleNamespace
import pytest
import yaml
from playwright.sync_api import sync_playwright
from typer.testing import CliRunner
from jobagent.config import get_settings,Settings
from jobagent.filling import select_strategy
from jobagent.filling.planned import Mappings
from jobagent.filling.facts import Fact,from_fact,compile_facts,resolve
from jobagent.steps import apply as A
from jobagent.tools import forms,shadow


def test_default_and_recorded_strategy_are_explicit(monkeypatch,tmp_path):
    monkeypatch.delenv('FILLING_STRATEGY',raising=False);get_settings.cache_clear()
    assert Settings(_env_file=None).filling_strategy=='original'
    assert select_strategy()=='original'
    (tmp_path/'application').mkdir();(tmp_path/'application/application.json').write_text('{"filling_strategy":"experimental"}')
    assert select_strategy(resume_job=tmp_path)=='experimental'
    assert select_strategy('original',resume_job=tmp_path)=='original'
    with pytest.raises(ValueError):select_strategy('mystery')
    get_settings.cache_clear()


def test_bad_strategy_is_rejected_before_credentials_or_browser():
    from jobagent.main import app
    result=CliRunner().invoke(app,['apply','/no/job','fixture','--filling-strategy','mystery'])
    assert result.exit_code==2 and 'filling-strategy' in result.output


def test_date_conversion_preserves_precision_without_assuming_a_day():
    f=forms.Field(id=0,kind='date',label='Start')
    assert from_fact(f,Fact('date',['Start'],'01/04/2027','user')).value=='2027-01-04'
    assert from_fact(f,Fact('date',['Start'],'January 2027','user')).value is None
    f=forms.Field(id=0,kind='date_parts',label='Start',format='MM/YYYY')
    assert from_fact(f,Fact('date',['Start'],'January 2027','user')).value=='01/2027'


def test_conditional_saved_literal_cannot_bypass_its_instruction():
    facts=compile_facts({'Qualifications':[{'question':'Honours','answer':'Yes',
                                          'instructions':'Yes only if the resume lists an honour; otherwise No.'}]},'')
    assert resolve(forms.Field(id=0,kind='text',label='Honours'),facts).value is None


class SourceModel:
    model_name='synthetic-model'
    def __init__(self,fail=False):self.callbacks=[];self.fail=fail
    def with_structured_output(self,schema,**kwargs):
        def invoke(_):
            if self.fail:raise RuntimeError('deliberate unavailable model')
            assert schema is Mappings
            return Mappings(mappings=[])
        return SimpleNamespace(invoke=invoke)


@pytest.fixture
def world(monkeypatch,tmp_path):
    monkeypatch.setenv('DATA_DIR',str(tmp_path/'data'));(tmp_path/'data').mkdir()
    monkeypatch.setenv('STATE_DIR',str(tmp_path/'state'));(tmp_path/'state').mkdir()
    get_settings.cache_clear()
    job=tmp_path/'job';job.mkdir()
    data={'Contact':[{'question':'Legal name','answer':{'first':'Casey','last':'Morgan'}},
                     {'question':'Email','answer':'casey@example.test'}]}
    def context(*_):
        d=copy.deepcopy(data)
        return {'_applicant':d,'APPLICANT':yaml.safe_dump(d),'_jd':{'company':'Fixture','apply_url':'https://fixture.test/apply'},
                '_jd_text':'','JD':'','RESUME':'','REFERRAL':{},'ANSWERS_FOR_THIS_APPLICATION':'(none)'}
    monkeypatch.setattr(A,'context',context)
    monkeypatch.setattr(A,'consult_learned',lambda *a,**k:None)
    def start(page,url,*args,**kwargs):
        page.goto(url);return forms.read_page(page)
    monkeypatch.setattr(A,'_start',start)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True);ctx=browser.new_context();shadow.install(pw,ctx);page=ctx.new_page()
        def setup(html):
            page.route('**/*',lambda r:r.fulfill(content_type='text/html',body=html))
        yield SimpleNamespace(page=page,job=job,data=data,setup=setup)
        browser.close()
    get_settings.cache_clear()


CONTACT=('<label for=f>Given Name</label><input id=f required><label for=l>Family Name</label><input id=l required>'
         '<label for=e>Email</label><input id=e type=email required><button type=button onclick="window.sent=true">Apply</button>')


def test_optin_runs_inside_real_workflow_and_preserves_approval_boundary(world,monkeypatch):
    world.setup(CONTACT)
    monkeypatch.setattr(A,'fill_page',lambda *a,**k:pytest.fail('original filler must not run'))
    result=A.run(world.page,world.job,'fixture',model=SourceModel(),filling_strategy='experimental',log=lambda _:None)
    assert result['ready_to_submit'] and result['filling_strategy']=='experimental'
    assert result['verified']==3 and result['filling_metrics']['writer_model_starts']==0
    assert all(f['outcome']=='verified' for f in result['fields'])
    assert not world.page.evaluate('!!window.sent')
    assert json.loads((world.job/'application/application.json').read_text())['filling_strategy']=='experimental'


def test_planner_failure_records_fields_and_never_advances(world):
    world.setup('<label for=x>Unprovided qualification</label><input id=x required>'
                '<button type=button onclick="window.advanced=true">Next</button><button>Apply</button>')
    result=A.run(world.page,world.job,'fixture',model=SourceModel(fail=True),filling_strategy='experimental',
                 at_review=lambda *a:pytest.fail('no review hook on failed planning'),log=lambda _:None)
    assert result['state']=='blocked' and not result['ready_to_submit']
    assert len(result['fields'])==1 and result['fields'][0]['outcome']=='unplanned'
    assert not world.page.evaluate('!!window.advanced')


def test_genuinely_missing_answers_are_one_batch_and_counted_as_two_fields(world):
    world.setup('<label for=x>Favourite editor</label><input id=x required>'
                '<label for=y>Preferred contact method</label><select id=y required><option>Choose</option><option>Email</option></select>'
                '<button type=button onclick="window.sent=true">Apply</button>')
    class Hitl:
        prompts=[]
        def ask(self,prompt,**kwargs):
            self.prompts.append(prompt);q=json.loads(prompt.split('\n',1)[1])
            return json.dumps({str(f['id']):'Vim' if f['kind']=='text' else 'Email' for f in q})
    h=Hitl();result=A.run(world.page,world.job,'fixture',model=SourceModel(),hitl=h,filling_strategy='experimental',log=lambda _:None)
    assert result['ready_to_submit'] and len(h.prompts)==1
    assert result['experimental_questions']==2 and result['experimental_question_batches']==1
    assert result['filling_metrics']['human_prompt_events']==1
    assert not world.page.evaluate('!!window.sent')


def test_saved_instruction_uses_common_source_checker_only_for_unresolved_fields(world,monkeypatch):
    world.data['Preferences']=[{'question':'Desired salary','answer':'','instructions':'Use 125000.'}]
    world.setup('<label for=e>Email</label><input id=e required><label for=s>Desired salary</label><input id=s type=number required><button>Apply</button>')
    planned=[];checked=[]
    def plan(fields,*a,**k):
        planned.extend(f.label for f in fields)
        return {f.id:A.Answer(id=f.id,value='125000',source='applicant') for f in fields},[]
    monkeypatch.setattr(A,'plan',plan);monkeypatch.setattr(A,'check',lambda fields,*a,**k:checked.extend(f.label for f in fields))
    result=A.run(world.page,world.job,'fixture',model=SourceModel(),filling_strategy='experimental',log=lambda _:None)
    assert result['ready_to_submit']
    assert planned==checked==['Desired salary'] and world.page.locator('#s').input_value()=='125000'
