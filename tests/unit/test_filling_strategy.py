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


@pytest.mark.parametrize('resume',[False,True])
def test_optin_runs_inside_real_workflow_and_preserves_approval_boundary(world,monkeypatch,resume):
    world.setup(CONTACT)
    if resume:
        (world.job/'application').mkdir()
        (world.job/'application/application.json').write_text('{"filling_strategy":"experimental"}')
    monkeypatch.setattr(A,'fill_page',lambda *a,**k:pytest.fail('original filler must not run'))
    result=A.run(world.page,world.job,'fixture',model=SourceModel(),filling_strategy=None if resume else 'experimental',log=lambda _:None)
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


def test_literal_rule_does_not_start_duplicate_legacy_planner(world,monkeypatch):
    world.data['Preferences']=[{'question':'Desired salary','answer':'','instructions':'Use 125000.'}]
    world.setup('<label for=e>Email</label><input id=e required><label for=s>Desired salary</label><input id=s type=number required><button>Apply</button>')
    planned=[];checked=[]
    def plan(fields,*a,**k):
        planned.extend(f.label for f in fields)
        return {f.id:A.Answer(id=f.id,value='125000',source='applicant') for f in fields},[]
    monkeypatch.setattr(A,'plan',plan);monkeypatch.setattr(A,'check',lambda fields,*a,**k:checked.extend(f.label for f in fields))
    result=A.run(world.page,world.job,'fixture',model=SourceModel(),filling_strategy='experimental',log=lambda _:None)
    assert result['ready_to_submit']
    assert planned==checked==[] and world.page.locator('#s').input_value()=='125000'


@pytest.mark.parametrize('hint,expected',[('MM-YYYY','05-2026'),('YYYY/MM','2026/05'),('YYYY','2026'),('MM/YYYY','05/2026')])
def test_date_tokens_preserve_requested_order_and_separators(hint,expected):
    f=forms.Field(id=0,kind='text',label='Start',format=hint)
    assert from_fact(f,Fact('d',['Start'],'May 2026','owner')).value==expected


def test_country_dial_code_rendering_and_unavailable_numeric_choice():
    f=forms.Field(id=0,kind='combobox',label='Phone Country',options=['United States +1','India +91'])
    assert from_fact(f,Fact('c',['Phone Country'],'United States','owner')).value=='United States +1'
    f.kind='select';f.label='Internships';f.options=['1','2+']
    assert from_fact(f,Fact('i',['Internships'],'0','owner')).value is None


def test_compiler_retains_conditional_rules_complete_records_and_extra_components():
    facts=compile_facts({'Profile':[{'question':'Honours','answer':'Yes','instructions':'Only if resume lists honours.'},
        {'question':'Work history','answer':[{'company':'Alpha','title':'Engineer','current':True,'description':'Built systems'}]}]},'')
    assert any(f.instructions for f in facts)
    assert any(f.record and 'Built systems' in f.value for f in facts)
    assert any('current' in [a.lower() for a in f.aliases] for f in facts)


def test_employment_row_audits_use_same_identity_as_the_filled_controls(world):
    world.data['History']=[{'question':'Work history','answer':[{'title':'Alpha Engineer'},{'title':'Beta Engineer'}]}]
    world.setup('<h2>Work Experience 1</h2><label for=a>Job Title</label><input id=a required>'
                '<h2>Work Experience 2</h2><label for=b>Job Title</label><input id=b required><button>Apply</button>')
    result=A.run(world.page,world.job,'fixture',model=SourceModel(),filling_strategy='experimental',log=lambda _:None)
    assert result['ready_to_submit']
    assert [(f['section'],f['answer'],f['readback']) for f in result['fields']]==[
        ('Work Experience 1','Alpha Engineer','Alpha Engineer'),('Work Experience 2','Beta Engineer','Beta Engineer')]


def test_retry_preserves_verified_answers_and_document_hash(world):
    from jobagent.filling.adapter import fill_page
    world.setup(CONTACT+'<label for=resume>Resume</label><input id=resume type=file>')
    world.page.goto('https://fixture.test/apply')
    path=world.job/'resume.pdf';path.write_bytes(b'%PDF-1.4 retained bytes')
    ctx=A.context(world.job,'fixture');env={};files={'resume':str(path)}
    first=[];fill_page(world.page,ctx,files,SourceModel(),lambda _:None,first,1,env)
    second=[];fill_page(world.page,ctx,files,SourceModel(),lambda _:None,second,1,env,known={},errors=['Some unrelated warning'])
    assert not env['_experimental_blockers']
    assert len(first)==len(second)==4 and all(f['verified'] for f in second)
    assert first[-1]['held_file_sha256']==second[-1]['held_file_sha256']


def test_explicit_clear_rule_removes_optional_stale_prefill(world):
    world.data['Links']=[{'question':'Portfolio','answer':'','instructions':'Leave blank.'}]
    world.setup(CONTACT+'<label for=p>Portfolio</label><input id=p type=url value="https://obsolete.test">')
    result=A.run(world.page,world.job,'fixture',model=SourceModel(),filling_strategy='experimental',log=lambda _:None)
    assert result['ready_to_submit'] and world.page.locator('#p').input_value()==''
    assert not result['experimental_questions'] and result['fields'][-1]['verified']


def test_popup_heading_does_not_change_section_or_field_identity(world):
    from jobagent.filling.planned import read_view
    from jobagent.filling.facts import key
    world.setup('<h2>Education 1</h2><label for=x>Start Date</label><input id=x>')
    world.page.goto('https://fixture.test/apply');before=read_view(world.page).fields[0]
    world.page.locator('#x').evaluate("e=>e.insertAdjacentHTML('beforebegin','<div role=dialog><h2>November 2026</h2></div>')")
    after=read_view(world.page).fields[0]
    assert before.section==after.section=='Education 1' and key(before)==key(after)


def test_failed_field_does_not_prevent_independent_answers(world,monkeypatch):
    from jobagent.filling.planned import PlannedFiller
    world.setup('<label for=a>First Name</label><input id=a required><label for=b>Last Name</label><input id=b required><button>Apply</button>')
    world.page.goto('https://fixture.test/apply');fill=forms.fill_field
    def fail_one(page,view,f,*args,**kwargs):
        if f.label=='First Name':raise forms.FillError('deliberate control rejection')
        return fill(page,view,f,*args,**kwargs)
    monkeypatch.setattr(forms,'fill_field',fail_one)
    filler=PlannedFiller(compile_facts(world.data,''),lambda _: {})
    result=filler.fill(world.page)
    assert result['state']=='blocked' and world.page.locator('#b').input_value()=='Morgan'
    assert any(r['label']=='Last Name' and r['outcome']=='verified' for r in result['records'])


def test_source_checker_failure_requires_review(monkeypatch):
    def fail(*a,**k):raise RuntimeError('checker unavailable')
    monkeypatch.setattr(A.llm,'structured_invoke',fail)
    f=forms.Field(id=0,kind='number',label='Desired percentage',required=True)
    answers={0:A.Answer(id=0,value='50',source='resume')}
    A.check([f],answers,{},object(),lambda _:None)
    assert answers[0].ask_human and answers[0].value==''


def test_unvalidated_nonempty_prefill_is_not_verified():
    f=forms.Field(id=0,kind='number',label='Preference',value='50',required=True)
    row={'filled':'','answer':'','verified':False,'required':True,'page':1,'label':'Preference'}
    row['outcome']=A.outcome_of(row,f)
    assert row['outcome']=='unverified prefill' and A.blockers_of([row])


@pytest.mark.parametrize('replace',[False,True])
def test_cleared_or_removed_native_upload_needs_actual_bytes_and_owned_ack(world,replace):
    (world.job/'resume.pdf').write_bytes(b'%PDF-1.4 accepted candidate bytes')
    action='this.remove()' if replace else "this.value=''"
    world.setup(CONTACT+'<div class=form-field><label for=resume>Resume</label><input id=resume type=file '
                f'onchange="this.nextElementSibling.textContent=this.files[0].name;{action}"><span></span></div>')
    payloads=[]
    def review(page,view,result):
        from jobagent.steps import submit as S
        sha,payload=S.freeze_payload(page,view,result['fields']);payloads.append(payload)
        assert payload['identity']['resume_sha256']
        page.locator('.form-field span').evaluate("e=>e.textContent='different.pdf'")
        other_sha,other=S.freeze_payload(page,forms.read_page(page),result['fields'])
        assert other_sha!=sha and other['identity']['resume_sha256'] is None
        return {'state':'test_review_only'}
    result=A.run(world.page,world.job,'fixture',model=SourceModel(),filling_strategy='experimental',at_review=review,log=lambda _:None)
    assert result['ready_to_submit'],result.get('blockers')
    row=next(f for f in result['fields'] if f['kind']=='file')
    assert row['verified'] and row['held_file_sha256'] and row['upload_verification']=='dispatched bytes and widget acknowledgement'
    assert payloads[0]['identity']['resume_sha256']==row['held_file_sha256']
    assert not hasattr(world.page,'_jobagent_upload_proofs') and not world.page.evaluate('!!window.sent')


def test_qualified_employee_source_does_not_select_recruiters():
    from jobagent.tools.referrals import _referral_option
    assert _referral_option(['Company Recruiter','Symbotic Employee'],'Symbotic')=='Symbotic Employee'
    assert _referral_option(['Symbotic Employee Recruiter','Non-employee referral'],'Symbotic')==''


def test_general_experience_prose_uses_one_bound_plan_and_checker(world):
    from jobagent.filling.planned import Mappings,Mapping,Supported
    from jobagent.filling.planned import PlannedFiller
    world.setup('<label for=x>What experiences have you had in the past three years?</label><textarea id=x required></textarea><button>Apply</button>')
    world.page.goto('https://fixture.test/apply');schemas=[]
    class Model(SourceModel):
        def with_structured_output(self,schema,**kw):
            schemas.append(schema)
            def invoke(_):
                if schema is Mappings:return Mappings(mappings=[Mapping(field_id=0,value='Built reliable systems.',source='resume',quote='Built reliable systems.')])
                assert schema is Supported
                return Supported(supported_ids=[0])
            return SimpleNamespace(invoke=invoke)
    filler=PlannedFiller([],lambda _:pytest.fail('supported experience should not need HITL'),Model(),{'resume':'Built reliable systems.'})
    result=filler.fill(world.page)
    assert result['ready'] and schemas==[Mappings,Supported]


def test_chunked_schema_response_without_content_length_is_inspected():
    from jobagent.filling.discovery import Discovery
    page=SimpleNamespace(on=lambda *a:None)
    discovery=Discovery(page)
    response=SimpleNamespace(headers={'content-type':'application/json'},text=lambda:'{"label":"Future question","type":"text","applicable":true}')
    discovery.received(SimpleNamespace(response=lambda:response))
    assert discovery.responses==1 and discovery.delivered[0]['label']=='Future question'


def test_applicable_schema_and_visible_unknowns_share_one_batch(world,monkeypatch):
    world.setup('<label for=x>Favourite editor</label><input id=x required '
                'onchange="document.getElementById(\'future\').innerHTML=\'<label for=y>Favourite color</label><input id=y required>\'">'
                '<div id=future></div><script type="application/json">'
                '{"label":"Favourite color","type":"text","required":true,"applicable":true}</script><button>Apply</button>')
    old_start=A._start
    def start(page,*args,**kwargs):
        assert getattr(page,'_jobagent_discovery',None) is not None
        return old_start(page,*args,**kwargs)
    monkeypatch.setattr(A,'_start',start)
    class Hitl:
        prompts=[]
        def ask(self,prompt,**kw):
            self.prompts.append(prompt);questions=json.loads(prompt.split('\n',1)[1])
            return json.dumps({str(q['id']):'Vim' if 'editor' in q['label'] else 'Blue' for q in questions})
    hitl=Hitl()
    result=A.run(world.page,world.job,'fixture',model=SourceModel(),hitl=hitl,filling_strategy='experimental',log=lambda _:None)
    assert result['ready_to_submit'] and len(hitl.prompts)==1
    assert world.page.locator('#y').input_value()=='Blue'
    assert not hasattr(world.page,'_jobagent_discovery')


def test_schema_does_not_ask_about_inapplicable_branch(world):
    world.setup(CONTACT+'<script type="application/json">{"label":"Referrer SSN","type":"text",'
                '"required":true,"condition":"employee spouse"}</script>')
    class Hitl:
        def ask(self,*a,**k):pytest.fail('irrelevant conditional branch must not prompt')
    result=A.run(world.page,world.job,'fixture',model=SourceModel(),hitl=Hitl(),filling_strategy='experimental',log=lambda _:None)
    assert result['ready_to_submit'] and result['experimental_questions']==0


def test_nonliteral_instruction_is_never_typed_as_an_answer():
    facts=compile_facts({'Contact':[{'question':'City','answer':'Austin',
        'instructions':"Use this job's selected address consistently."}]},'')
    assert resolve(forms.Field(id=0,kind='text',label='City'),facts).value is None


def test_resume_cannot_supply_a_personal_preference_even_when_checker_would_approve(monkeypatch):
    monkeypatch.setattr(A.llm,'structured_invoke',lambda *a,**k:pytest.fail('preference must be sourced to owner first'))
    f=forms.Field(id=0,kind='number',label='What percentage of your time would you like to spend coding?',required=True)
    answers={0:A.Answer(id=0,value='50',source='resume')}
    A.check([f],answers,{},object(),lambda _:None)
    assert answers[0].ask_human and not answers[0].value


def test_matching_filename_outside_uploader_is_not_proof(world,monkeypatch):
    (world.job/'resume.pdf').write_bytes(b'%PDF-1.4 must really attach')
    world.setup(CONTACT+'<div class=form-field><label for=resume>Resume</label><input id=resume type=file '
                'onchange="this.value=\'\'"></div><aside>Documents: resume.pdf</aside>')
    wait=world.page.wait_for_timeout;monkeypatch.setattr(world.page,'wait_for_timeout',lambda ms:wait(min(ms,10)))
    result=A.run(world.page,world.job,'fixture',model=SourceModel(),filling_strategy='experimental',log=lambda _:None)
    assert not result['ready_to_submit'] and result['state']=='blocked'
    assert not any(f['kind']=='file' and f['verified'] for f in result['fields'])


def test_strict_variants_do_not_fall_back_to_similar_institution(world):
    world.setup('<label for=s>School</label><select id=s><option>Choose</option><option>Adams State University</option><option>Other</option></select>')
    world.page.goto('https://fixture.test/apply');view=forms.read_page(world.page)
    with pytest.raises(forms.FillError):forms.fill_field(world.page,view,view.fields[0],'Arizona State University | ASU',{},strict=True)
    assert world.page.locator('#s').input_value()=='Choose'


def test_fields_revealed_by_second_failure_are_still_filled(world,monkeypatch):
    from jobagent.filling.planned import PlannedFiller
    world.setup('<label for=a>First Name</label><input id=a required><button>Apply</button>')
    world.page.goto('https://fixture.test/apply');fill=forms.fill_field;attempts=[]
    def fail_first(page,view,f,*args,**kwargs):
        if f.label=='First Name':
            attempts.append(1)
            if len(attempts)==2:page.evaluate("document.body.insertAdjacentHTML('beforeend','<label for=b>Last Name</label><input id=b required>')")
            raise forms.FillError('deliberate failure')
        return fill(page,view,f,*args,**kwargs)
    monkeypatch.setattr(forms,'fill_field',fail_first)
    result=PlannedFiller(compile_facts(world.data,''),lambda _:{}).fill(world.page)
    assert result['state']=='blocked' and len(attempts)==2
    assert world.page.locator('#b').input_value()=='Morgan'


def test_rejected_conditional_rule_is_not_overridden_by_generic_yes_in_profile(monkeypatch):
    data={'Screening':[{'question':'Honours','answer':'Yes','instructions':'Yes only if resume lists an honour.'}]}
    f=forms.Field(id=0,kind='text',label='Honours')
    monkeypatch.setattr(A.llm,'structured_invoke',lambda *a,**k:SimpleNamespace(field_0=A.Check(id=0,supported=False,ask_human=True)))
    answers={0:A.Answer(id=0,value='Yes',source='applicant')}
    A.check([f],answers,{'_applicant':data},object(),lambda _:None)
    assert answers[0].ask_human and answers[0].value==''


def test_old_acknowledgement_cannot_validate_a_new_cleared_upload(world,monkeypatch):
    (world.job/'resume.pdf').write_bytes(b'%PDF-1.4 new bytes')
    world.setup(CONTACT+'<div class=form-field><label for=resume>Resume</label><input id=resume type=file '
                'onchange="this.value=\'\'"><span>resume.pdf</span></div>')
    wait=world.page.wait_for_timeout;monkeypatch.setattr(world.page,'wait_for_timeout',lambda ms:wait(min(ms,10)))
    result=A.run(world.page,world.job,'fixture',model=SourceModel(),filling_strategy='experimental',log=lambda _:None)
    assert not result['ready_to_submit']


def test_shadow_roots_with_repeated_author_ids_keep_distinct_identities(world):
    from jobagent.filling.planned import read_view
    from jobagent.filling.facts import key
    world.setup('<div id=a></div><div id=b></div><script>for(const id of ["a","b"])'
                'document.getElementById(id).attachShadow({mode:"open"}).innerHTML="<label for=x>Name</label><input id=x>"</script>')
    world.page.goto('https://fixture.test/apply');fields=read_view(world.page).fields
    assert len(fields)==2 and len({key(f) for f in fields})==2


def test_invalid_cached_field_cannot_keep_a_verified_retry_record(world):
    from jobagent.filling.adapter import fill_page
    world.setup(CONTACT);world.page.goto('https://fixture.test/apply')
    ctx=A.context(world.job,'fixture');env={}
    fill_page(world.page,ctx,{},SourceModel(),lambda _:None,[],1,env)
    world.page.locator('#e').evaluate("e=>e.setAttribute('aria-invalid','true')")
    record=[];fill_page(world.page,ctx,{},SourceModel(),lambda _:None,record,1,env)
    assert env['_experimental_blockers']
    assert not next(f for f in record if f['label']=='Email')['verified']


def test_page_metrics_do_not_subtract_previous_pages_waiting_time(world):
    from jobagent.filling.planned import PlannedFiller
    world.setup(CONTACT);world.page.goto('https://fixture.test/apply')
    filler=PlannedFiller(compile_facts(world.data,''),lambda _: {})
    filler.human_wait_s=1000;filler.questions=5;filler.model_calls=10
    result=filler.fill(world.page)
    assert result['ready'] and result['human_wait_s']==0 and result['active_s']>=0
    assert result['questions']==result['model_calls']==0
    assert filler.questions==5 and filler.model_calls==10


def test_schema_children_keep_their_parents_condition():
    from jobagent.filling.discovery import extract
    fields=extract({'condition':'employee spouse','questions':[{'label':'Referrer SSN','type':'text','required':True,'applicable':True}]},'fixture')
    assert len(fields)==1 and fields[0]['condition']=='employee spouse'


def test_saved_company_answer_is_evidence_without_a_second_planner(world,monkeypatch):
    from jobagent.tools import learned
    monkeypatch.setattr(learned,'load',lambda:[{'question':'Preferred contact method','answer':'Email','company':'Fixture'}])
    monkeypatch.setattr(A,'plan',lambda *a,**k:pytest.fail('legacy planner must not run'))
    world.setup(CONTACT+'<label for=p>Preferred contact method</label><select id=p required><option>Choose</option><option>Email</option><option>Phone</option></select>')
    result=A.run(world.page,world.job,'fixture',model=SourceModel(fail=True),filling_strategy='experimental',log=lambda _:None)
    assert result['ready_to_submit'] and world.page.locator('#p').input_value()=='Email'
    assert result['experimental_questions']==0


def test_another_companys_learned_literal_is_not_a_global_answer(world,monkeypatch):
    from jobagent.tools import learned
    monkeypatch.setattr(learned,'load',lambda:[{'question':'Favourite editor','answer':'Vim','company':'Other Company'}])
    world.setup('<label for=p>Favourite editor</label><input id=p required><button>Apply</button>')
    result=A.run(world.page,world.job,'fixture',model=SourceModel(),filling_strategy='experimental',log=lambda _:None)
    assert not result['ready_to_submit'] and world.page.locator('#p').input_value()==''


@pytest.mark.parametrize('allowed',[False,True])
def test_lookup_requires_owner_tool_permission_and_is_checked_with_its_rule(world,allowed):
    from jobagent.filling.planned import PlannedFiller,Mapping,Supported
    facts=compile_facts({'Preferences':[{'question':'Desired salary','answer':'',
        'instructions':'Use the median salary from a web search.','tools':['web search'] if allowed else []}]},'')
    ident=facts[0].id;request='web: Fixture engineer median salary';lookups=[]
    world.setup('<label for=s>Desired salary</label><input id=s type=number required><button>Apply</button>')
    world.page.goto('https://fixture.test/apply')
    class Model(SourceModel):
        mappings=0
        def with_structured_output(self,schema,**kw):
            def invoke(_):
                if schema is Supported:return Supported(supported_ids=[0])
                self.mappings+=1
                if self.mappings==1:return Mappings(mappings=[Mapping(field_id=0,fact_id=ident,lookup=request)])
                return Mappings(mappings=[Mapping(field_id=0,fact_ids=[ident,'lookup:'+request],value='125000')])
            return SimpleNamespace(invoke=invoke)
    filler=PlannedFiller(facts,lambda _: {},Model())
    filler.lookup_resolver=lambda query:lookups.append(query) or 'Median salary: 125000.'
    result=filler.fill(world.page)
    assert result['ready']==allowed and lookups==([request] if allowed else [])
    assert world.page.locator('#s').input_value()==('125000' if allowed else '')


def test_unavailable_checker_correction_does_not_keep_rejected_answer(monkeypatch):
    f=forms.Field(id=0,kind='select',label='Experience level',options=['Basic','Advanced'])
    monkeypatch.setattr(A.llm,'structured_invoke',lambda *a,**k:SimpleNamespace(field_0=A.Check(id=0,supported=False,value='Intermediate')))
    answers={0:A.Answer(id=0,value='Basic',source='resume')}
    A.check([f],answers,{},object(),lambda _:None)
    assert answers[0].ask_human and answers[0].value==''


@pytest.mark.parametrize('value,expected',[(0,'0'),(False,'No'),('','')])
def test_saved_zero_false_and_explicit_blank_are_not_unknown(world,monkeypatch,value,expected):
    from jobagent.tools import learned
    monkeypatch.setattr(learned,'load',lambda:[{'question':'Saved response','answer':value,'company':'Fixture'}])
    world.setup(CONTACT+'<label for=p>Saved response</label><input id=p value=obsolete>')
    result=A.run(world.page,world.job,'fixture',model=SourceModel(fail=True),filling_strategy='experimental',log=lambda _:None)
    assert result['ready_to_submit'] and world.page.locator('#p').input_value()==expected


def test_company_slug_and_researched_company_name_share_saved_answers(world,monkeypatch):
    from jobagent.tools import learned
    context=A.context
    def renamed(*args):
        ctx=context(*args);ctx['_jd']['company']='Fixture Corporation';return ctx
    monkeypatch.setattr(A,'context',renamed)
    monkeypatch.setattr(learned,'load',lambda:[{'question':'Favourite editor','answer':'Vim','company':'fixture'}])
    world.setup(CONTACT+'<label for=p>Favourite editor</label><input id=p required>')
    result=A.run(world.page,world.job,'fixture',model=SourceModel(fail=True),filling_strategy='experimental',log=lambda _:None)
    assert result['ready_to_submit'] and world.page.locator('#p').input_value()=='Vim'


@pytest.mark.parametrize('source,ready',[('resume',True),('jd',False)])
def test_resume_evidence_can_answer_qualification_select_but_jd_requirements_cannot(world,source,ready):
    from jobagent.filling.planned import PlannedFiller,Mapping,Supported
    world.setup('<label for=p>Do you have Python experience?</label><select id=p required><option>Choose</option><option>Yes</option><option>No</option></select><button>Apply</button>')
    world.page.goto('https://fixture.test/apply')
    class Model(SourceModel):
        def with_structured_output(self,schema,**kw):
            return SimpleNamespace(invoke=lambda _:Mappings(mappings=[Mapping(field_id=0,value='Yes',source=source,quote='Built Python services.')])
                                   if schema is Mappings else Supported(supported_ids=[0]))
    result=PlannedFiller([],lambda _: {},Model(),{source:'Built Python services.'}).fill(world.page)
    assert result['ready']==ready
    assert world.page.locator('#p').input_value()==('Yes' if ready else 'Choose')


def test_list_sources_and_history_rules_are_preserved():
    facts=compile_facts({'Profile':[{'question':'Skills','answer':['Python','SQL']},
        {'question':'Work history','answer':[{'title':'Engineer'}],'instructions':'Use only paid roles.'}]},'')
    assert any(f.record and 'Python' in f.value and 'SQL' in f.value for f in facts)
    titles=[f for f in facts if 'Job Title' in f.aliases]
    assert len(titles)==1 and titles[0].instructions=='Use only paid roles.'


def test_chooser_element_handle_can_verify_retained_bytes_without_visible_filename(world,monkeypatch):
    world.setup('<div class=upload><label>Resume</label><button type=button onclick="const i=document.createElement(\'input\');'
                'i.type=\'file\';i.style.display=\'none\';document.body.appendChild(i);i.click()">Choose file</button></div>')
    world.page.goto('https://fixture.test/apply')
    path=world.job/'resume.pdf';path.write_bytes(b'%PDF-1.4 chooser bytes')
    wait=world.page.wait_for_timeout;monkeypatch.setattr(world.page,'wait_for_timeout',lambda ms:wait(min(ms,10)))
    view=forms.read_page(world.page);field=next(f for f in view.fields if f.kind=='file')
    assert forms.fill_field(world.page,view,field,'resume',{'resume':str(path)})==str(path)
    assert world.page.locator('input[type=file]').evaluate('e=>e.files[0].name')=='resume.pdf'


def test_original_filler_does_not_verify_an_unsupported_prefill(world,monkeypatch):
    world.setup('<label for=p>Preferred coding percentage</label><input id=p type=number value=50 required><button>Apply</button>')
    monkeypatch.setattr(A,'plan',lambda fields,*args,**kwargs:({f.id:A.Answer(id=f.id,value='',source='none',ask_human=True) for f in fields},[]))
    result=A.run(world.page,world.job,'fixture',model=SourceModel(),filling_strategy='original',log=lambda _:None)
    assert not result['ready_to_submit'] and result['verified']==0
    assert result['fields'][0]['outcome']=='unverified prefill' and not result['fields'][0]['verified']


def test_generic_file_type_label_does_not_hide_the_document_role(world):
    (world.job/'resume.pdf').write_bytes(b'%PDF-1.4 test candidate document')
    world.setup(CONTACT+'<label for=resume>Accepted file types: pdf doc docx txt rtf</label><input id=resume type=file>')
    result=A.run(world.page,world.job,'fixture',model=SourceModel(),filling_strategy='experimental',log=lambda _:None)
    assert result['ready_to_submit']
    upload=next(f for f in result['fields'] if f['kind']=='file')
    assert upload['verified'] and upload['answer']=='resume' and upload['held_file_sha256']


def test_disappearing_upload_without_native_required_marker_cannot_be_ready(world,monkeypatch):
    (world.job/'resume.pdf').write_bytes(b'%PDF-1.4 test candidate document')
    world.setup(CONTACT+'<label for=resume>Resume</label><input id=resume type=file>')
    fill=forms.fill_field
    def replacing_uploader(page,view,field,value,files,*a,**kw):
        if field.kind=='file':
            forms.locate(view,field).evaluate('e=>e.remove()')
            return files[value]
        return fill(page,view,field,value,files,*a,**kw)
    monkeypatch.setattr(forms,'fill_field',replacing_uploader)
    result=A.run(world.page,world.job,'fixture',model=SourceModel(),filling_strategy='experimental',log=lambda _:None)
    assert not result['ready_to_submit'] and result['state']=='blocked'
    assert any('provided resume upload is not verified' in b for b in result['blockers'])
    assert not world.page.evaluate('!!window.sent')


def test_structured_preferred_name_and_explicit_blank_extension_are_preserved(world):
    world.data['Contact'] += [
        {'question':'Preferred name','answer':{'first':'CJ','last':'Morgan','full':'CJ Morgan'}},
        {'question':'Phone','answer':{'number':'2025550109','extension':''}}]
    world.setup(CONTACT+'<label for=p>Preferred Name (if applicable)</label><input id=p value=Wrong>'
                '<label for=x>Phone Extension</label><input id=x value=999>')
    result=A.run(world.page,world.job,'fixture',model=SourceModel(),filling_strategy='experimental',log=lambda _:None)
    assert result['ready_to_submit']
    assert world.page.locator('#p').input_value()=='CJ Morgan'
    assert world.page.locator('#x').input_value()==''
    assert not result['experimental_questions']


@pytest.mark.parametrize('native_id',['id=resume',''])
def test_upload_obligation_survives_filename_added_to_caption(world,native_id):
    (world.job/'resume.pdf').write_bytes(b'%PDF-1.4 test candidate document')
    world.setup(CONTACT+f'<div><label>Resume<input {native_id} type=file '
                'onchange="this.nextElementSibling.textContent=this.files[0].name"><span>Resume</span></label></div>')
    result=A.run(world.page,world.job,'fixture',model=SourceModel(),filling_strategy='experimental',log=lambda _:None)
    assert result['ready_to_submit']
    upload=next(f for f in result['fields'] if f['kind']=='file')
    assert 'resume.pdf' in upload['label'] and upload['verified'] and upload['held_file_sha256']
