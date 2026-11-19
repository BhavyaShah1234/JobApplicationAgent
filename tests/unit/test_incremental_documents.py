"""Dependency caching and final-only ATS must preserve actual document identity."""
import json
from types import SimpleNamespace
import pytest
from pydantic import BaseModel
from langchain_core.messages import HumanMessage

from jobagent.tools.artifact_cache import ArtifactCache
from jobagent.tools.incremental_documents import invoke, review, final_panel
from jobagent.tools import letter_attribution as L
from jobagent.tools.cover_letter import AttributedParagraph as Paragraph, Letter


class Output(BaseModel):
    text: str


def test_success_survives_restart_but_prompt_schema_model_and_code_are_dependencies(tmp_path,monkeypatch):
    from jobagent.tools import incremental_documents as I
    calls=[]
    monkeypatch.setattr(I.llm,'structured_invoke',lambda *a,**kw:calls.append(1) or Output(text='done'))
    monkeypatch.setattr(I.job_context,'model_version',lambda *args:'v1')
    def call(model='m',prompt='a'):
        return invoke(ArtifactCache(tmp_path),'experience',model,object(),Output,[HumanMessage(prompt)])
    call();call()
    assert len(calls)==1
    call(prompt='b');call(model='other')
    monkeypatch.setattr(I.job_context,'model_version',lambda *args:'v2')
    call()
    assert len(calls)==4


def test_document_clock_is_stable_within_local_day_and_is_sent_as_cached(tmp_path,monkeypatch):
    from datetime import date
    from langchain_core.messages import SystemMessage
    from jobagent import clock
    from jobagent.tools import incremental_documents as I
    seen=[]
    def generate(model,schema,messages,**kwargs):
        seen.append(messages)
        return Output(text='done')
    monkeypatch.setattr(I.llm,'structured_invoke',generate)
    monkeypatch.setattr(clock,'today',lambda:date(2026,11,19))
    suffix=". Judge 'present', 'current', 'latest' and 'recent' against this date."
    def call(time):
        messages=[SystemMessage('Write.\n\nToday is Thursday, November 19, 2026, '+time+
                                ' MST (America/Phoenix)'+suffix),
                  HumanMessage('Source date: Today is November 19, 2026, 10:01 AM'+suffix)]
        return invoke(ArtifactCache(tmp_path),'experience','m',object(),Output,messages)
    call('10:01 AM');call('10:02 AM')
    assert len(seen)==1
    assert seen[0][0].content==('Write.\n\nToday is 2026-11-19 (America/Phoenix). '
        "Judge 'present', 'current', 'latest' and 'recent' against this local date.")
    assert '10:01 AM' in seen[0][1].content
    monkeypatch.setattr(clock,'today',lambda:date(2026,11,20))
    call('10:02 AM')
    assert len(seen)==2 and '2026-11-20' in seen[1][0].content


def test_failure_and_corruption_never_become_cache_hits(tmp_path,monkeypatch):
    from jobagent.tools import incremental_documents as I
    def fail(*a,**kw):raise RuntimeError('provider down')
    monkeypatch.setattr(I.llm,'structured_invoke',fail)
    with pytest.raises(RuntimeError):invoke(ArtifactCache(tmp_path),'x','m',None,Output,[HumanMessage('q')])
    assert not list(tmp_path.glob('*.json'))
    cache=ArtifactCache(tmp_path);cache.put('x',{}, {'text':'good'})
    path=next(tmp_path.glob('*.json'));doc=json.loads(path.read_text());doc['result']['text']='corrupt';path.write_text(json.dumps(doc))
    assert cache.get('x',{}) is None


def test_semantically_unbound_outputs_do_not_enter_the_cache(tmp_path,monkeypatch):
    from jobagent.tools import incremental_documents as I
    monkeypatch.setattr(I.llm,'structured_invoke',lambda *a,**kw:Output(text='unbound'))
    def validate(value):
        if value.text=='unbound':raise ValueError('source quote does not belong to this field')
    with pytest.raises(ValueError,match='source quote'):
        invoke(ArtifactCache(tmp_path),'review','m',object(),Output,[HumanMessage('q')],validator=validate)
    assert not list(tmp_path.glob('*.json'))


def test_changed_skills_rechecks_skills_and_summary_only(tmp_path,monkeypatch):
    from jobagent.steps import multi_agent as M
    from jobagent.tools import incremental_documents as I
    monkeypatch.setattr(M.T,'facts_block',lambda:'owner claims')
    calls=[]
    def generate(model,schema,msgs,**kw):
        calls.append(msgs[0].content.split('Review ONLY ')[1].split('.')[0]);return M.Review(findings=[])
    monkeypatch.setattr(I.llm,'structured_invoke',generate)
    monkeypatch.setattr(I.llm,'writer',lambda *a,**kw:object())
    f=M.T.Filled.model_construct(summary='A summary.',roles=[],skills=[M.T.SkillLine(category='Languages',terms=['Python'])])
    ctx={'job_dir':tmp_path,'r':SimpleNamespace(roles=[],skills=[]),'jd':SimpleNamespace(model_dump=lambda **kw:{}),
         'models':{'router':'m'},'cache':ArtifactCache(tmp_path/'cache'),'log':lambda m:None}
    review(ctx,f);review(ctx,f)
    assert len(calls)==4
    f.skills[0].terms=['Rust'];review(ctx,f)
    assert sorted(calls[4:])==['skills','summary']


def test_initial_experience_prompt_is_independent_of_project_thread_timing(tmp_path,monkeypatch):
    from jobagent.steps import multi_agent as M
    from langchain_core.messages import SystemMessage
    monkeypatch.setattr(M.T,'facts_block',lambda:'owner claims')
    monkeypatch.setattr(M.T,'build_fill_prompt',lambda *a,**kw:[SystemMessage('base'),HumanMessage('JD')])
    ctx={'job_dir':tmp_path,'r':SimpleNamespace(skills=[]),'jd':None,'company':'Company','incremental':True}
    before=M._section_messages(ctx,'experience',None,[],'')
    (tmp_path/'projects.json').write_text('This previous output must not be read')
    ctx['candidates']=[(1,{'full_name':'another/project'})]
    after=M._section_messages(ctx,'experience',None,[],'')
    assert [m.content for m in before]==[m.content for m in after]


def test_project_reviewer_never_receives_search_vectors_or_unbounded_readme_bodies(tmp_path,monkeypatch):
    from jobagent.steps import multi_agent as M
    from jobagent.tools import incremental_documents as I
    monkeypatch.setattr(M.T,'facts_block',lambda:'owner claims')
    monkeypatch.setattr(I.llm,'writer',lambda *a,**kw:object())
    seen=[]
    def inspect(cache,stage,model_name,model,schema,messages,**kwargs):
        if stage=='review:projects':seen.append(json.loads(messages[1].content))
        return M.Review(findings=[])
    monkeypatch.setattr(I,'invoke',inspect)
    (tmp_path/'projects.json').write_text(json.dumps({'kept':1,'short':[],
        'ranked':[{'repo':'owner/project','title':'Project','stack':['Python'],'paragraph':'Built a robot.'}]}))
    ctx={'job_dir':tmp_path,'r':SimpleNamespace(roles=[],skills=[]),'jd':SimpleNamespace(model_dump=lambda **kw:{}),
         'models':{'router':'m'},'cache':ArtifactCache(tmp_path/'cache'),'log':lambda _:None,
         'cards':{'owner/project':{'summary':'Built a robot.','embedding':[.1]*4096,'readme':'junk'*100000}}}
    f=M.T.Filled.model_construct(summary='A summary.',roles=[],skills=[])
    review(ctx,f)
    assert seen[0]['own_repo_sources']=={'owner/project':{'summary':'Built a robot.','readme_excerpt':('junk'*100000)[:2500]}}
    assert len(json.dumps(seen[0]))<10000


def test_every_scoped_reviewer_receives_the_existing_owner_claims_policy(tmp_path,monkeypatch):
    from jobagent.steps import multi_agent as M
    from jobagent.tools import incremental_documents as I
    policy='Up to 5+ years overall or in any domain the posting asks for.'
    monkeypatch.setattr(M.T,'facts_block',lambda:policy)
    monkeypatch.setattr(I.llm,'writer',lambda *a,**kw:object())
    seen=[]
    def inspect(cache,stage,model_name,model,schema,messages,**kwargs):
        seen.append(json.loads(messages[1].content)['owner_claims_policy']);return M.Review(findings=[])
    monkeypatch.setattr(I,'invoke',inspect)
    ctx={'job_dir':tmp_path,'r':SimpleNamespace(roles=[],skills=[]),'jd':SimpleNamespace(model_dump=lambda **kw:{}),
         'models':{'router':'m'},'cache':ArtifactCache(tmp_path/'cache'),'log':lambda _:None}
    review(ctx,M.T.Filled.model_construct(summary='A summary.',roles=[],skills=[]))
    assert len(seen)==4 and all(v.startswith(policy) for v in seen)
    assert all('standing confirmation' in v for v in seen)


def test_summary_review_receives_current_date_and_target_company_context(tmp_path,monkeypatch):
    from jobagent.steps import multi_agent as M
    from jobagent.tools import incremental_documents as I
    monkeypatch.setattr(M.T,'facts_block',lambda:'Owner policy')
    monkeypatch.setattr(I.llm,'writer',lambda *a,**kw:object())
    seen=[]
    def inspect(cache,stage,model_name,model,schema,messages,**kwargs):
        if stage=='review:summary':seen.extend(messages)
        return M.Review(findings=[])
    monkeypatch.setattr(I,'invoke',inspect)
    ctx={'job_dir':tmp_path,'r':SimpleNamespace(roles=[],skills=[]),'jd':SimpleNamespace(model_dump=lambda **kw:{}),
         'models':{'router':'m'},'cache':ArtifactCache(tmp_path/'cache'),'log':lambda _:None,
         'company':'Beta builds robots.'}
    review(ctx,M.T.Filled.model_construct(summary='Engineer interested in Beta.',roles=[],skills=[]))
    assert 'Today is ' in seen[0].content and 'not a claim of previous employment' in seen[0].content
    assert json.loads(seen[1].content)['company_context']=='Beta builds robots.'


@pytest.mark.parametrize('changed',['resume.tex','jd.json','company'])
def test_cover_letter_refuses_changed_inputs_without_discarding_owner_edits(tmp_path,monkeypatch,changed):
    from jobagent.steps import tailor as S
    from jobagent.tools import cover_letter as CL,documents
    from jobagent.state import JobDescription
    data=tmp_path/'data';state=tmp_path/'state';job=tmp_path/'job'
    (data/'templates').mkdir(parents=True);(state/'companies').mkdir(parents=True);job.mkdir()
    company=state/'companies'/'fixture.md';company.write_text('Original company')
    (job/'jd.json').write_text(JobDescription(company='Fixture',title='Engineer').model_dump_json())
    (job/'resume.tex').write_text('Final resume')
    template=data/'templates'/'cover_letter.tex';template.write_text('Template')
    monkeypatch.setattr(S,'get_settings',lambda:SimpleNamespace(data_dir=data,state_dir=state))
    monkeypatch.setattr(CL,'template_path',lambda:template)
    monkeypatch.setattr(S.llm,'writer',lambda *a,**kw:object())
    target=company if changed=='company' else job/changed
    def generate(*a,**kw):
        target.write_text('Owner edit during generation')
        return {'valid':True}
    monkeypatch.setattr(CL,'write',generate)
    with pytest.raises(RuntimeError,match='source inputs changed'):
        S.cover_letter(job,'fixture',incremental=True)
    assert target.read_text()=='Owner edit during generation'
    assert documents.load(job)['valid'] is False
    assert not (job/'changes.md').exists()


def test_review_only_code_changes_preserve_writer_cache_versions(tmp_path,monkeypatch):
    from jobagent.tools import job_context as J
    (tmp_path/'steps').mkdir();(tmp_path/'tools').mkdir()
    (tmp_path/'steps/multi_agent.py').write_text('writer')
    (tmp_path/'tools/tailor.py').write_text('policy')
    review_code=tmp_path/'tools/incremental_documents.py';review_code.write_text('review v1')
    monkeypatch.setattr(J,'Path',lambda _:SimpleNamespace(parents={1:tmp_path}))
    writer,reviewer=J.model_version('experience'),J.model_version('review:experience')
    review_code.write_text('review v2')
    assert J.model_version('experience')==writer and J.model_version('review:experience')!=reviewer
    (tmp_path/'tools/tailor.py').write_text('changed policy')
    assert J.model_version('experience')!=writer


def test_targeted_bullet_repair_uses_a_repair_budget_not_a_full_draft_budget(monkeypatch):
    from jobagent.steps import multi_agent as M
    seen=[]
    monkeypatch.setattr(M.llm,'writer',lambda budget,**kwargs:seen.append(budget) or object())
    monkeypatch.setattr(M.T,'build_repair_prompt',lambda *a:[])
    monkeypatch.setattr(M.llm,'structured_invoke',lambda *a,**kw:M.T.Repair.model_construct(fixes=[]))
    ctx={'models':{'experience':'m'},'r':None,'jd':None,'log':lambda _:None}
    M._run_agent(ctx,'experience',object(),['role_0 bullet 1 states no result'],'')
    assert seen==[3000]


def test_title_prefix_is_deterministic_and_nonfatal_policy_remains_unchanged(tmp_path,monkeypatch):
    from jobagent.steps import multi_agent as M
    assert M._title_prefix('Autonomy Platform Engineer with 1+ years of experience.','Autonomy Platform Engineer')
    assert not M._title_prefix('Platform Engineer with experience.','Autonomy Platform Engineer')
    findings=[M.Finding(agent='summary',severity='error',issue='Incorrect critic claim about title or lower-bound years')]
    monkeypatch.setattr(M,'content_review',lambda *a:findings)
    f=M.T.Filled.model_construct(summary='Autonomy Platform Engineer with 1+ years.',roles=[],skills=[])
    reviewed=M._reviewed_content({'job_dir':tmp_path,'incremental':True},f,{})
    assert reviewed['semantic_hard']==[]
    assert reviewed['content_findings']==findings


def test_incremental_draft_schema_matches_the_actual_new_bullet_limit():
    from jobagent.steps import multi_agent as M
    r=SimpleNamespace(roles=[SimpleNamespace(title='Engineer',company='Fixture')])
    compact=M.experience_schema(r,compact=True).model_json_schema()
    ordinary=M.experience_schema(r).model_json_schema()
    assert compact['$defs']['CompactFillBullet']['properties']['text']['maxLength']==M.T.BULLET_MAX
    assert ordinary['$defs']['FillBullet']['properties']['text']['maxLength']==M.T.BULLET_VERBATIM_MAX


def test_targeted_project_edit_changes_only_the_bound_visible_variant_and_title(tmp_path,monkeypatch):
    from jobagent.steps import multi_agent as M
    from jobagent.steps import ats as A
    st={'kept':1,'short':['owner/one'],'ranked':[
        {'repo':'owner/one','title':'one','paragraph':'Full variant retained.','short':'Old short.'},
        {'repo':'owner/two','title':'two','paragraph':'Not on the page.','short':'Hidden.'}]}
    fixes=[M.ProjectEdit(project=1,title='Robotic Maze Solver',short='Repaired short. '*12),
           M.ProjectEdit(project=2,title='Should not change',paragraph='z'*250)]
    changed=json.loads(A.apply_project_fixes(json.dumps(st),fixes))
    assert changed['ranked'][0]['short'].startswith('Repaired short.')
    assert changed['ranked'][0]['title']=='Robotic Maze Solver'
    assert changed['ranked'][0]['paragraph']==st['ranked'][0]['paragraph']
    assert changed['ranked'][1]==st['ranked'][1]
    (tmp_path/'projects.json').write_text(json.dumps(st))
    monkeypatch.setattr(M.P,'known_entries',lambda _: {'owner/one':('Owner Title','2026')})
    f=M.T.Filled.model_construct(roles=[],skills=[],summary='Summary')
    M._merge({'job_dir':tmp_path,'r':SimpleNamespace(tex='',roles=[]),'incremental':True},f,
             {'projects':M.IncrementalProjectsOut(projects=fixes)})
    assert json.loads((tmp_path/'projects.json').read_text())['ranked'][0]['title']=='Owner Title'


def test_a_critic_cannot_block_a_supported_project_claim_without_a_bound_contradiction(tmp_path,monkeypatch):
    from jobagent.steps import multi_agent as M
    from jobagent.tools import incremental_documents as I
    monkeypatch.setattr(I.llm,'writer',lambda *a,**kw:object())
    monkeypatch.setattr(I,'invoke',lambda *a,**kw:I.ProjectVerdicts(verdicts=[I.ProjectVerdict(
        finding=0,project=1,status='rejected',claim_quote='Built quality inspection workflows.',
        source_quote='inventory/quality-inspection management system',reason='The own source explicitly supports the capability.')]))
    payload={'displayed':[{'repo':'owner/project','text':'Built quality inspection workflows.'}],
             'own_repo_sources':{'owner/project':{'summary':'An inventory/quality-inspection management system.'}},
             'owner_claims_policy':'Work impact metrics are allowed.'}
    error=M.Finding(agent='projects',severity='error',issue='Quality inspection is not in the source')
    assert I.adjudicate_projects({'adjudicator_model':'judge','cache':ArtifactCache(tmp_path),'log':lambda _:None},payload,[error])==[]


def test_adjudication_cannot_borrow_another_projects_source(tmp_path,monkeypatch):
    from jobagent.steps import multi_agent as M
    from jobagent.tools import incremental_documents as I
    monkeypatch.setattr(I.llm,'writer',lambda *a,**kw:object())
    monkeypatch.setattr(I,'invoke',lambda *a,**kw:I.ProjectVerdicts(verdicts=[I.ProjectVerdict(
        finding=0,project=1,status='rejected',claim_quote='Built a camera driver.',source_quote='Supports cameras.',
        reason='Unbound quote from another repository')]))
    payload={'displayed':[{'repo':'a','text':'Built a camera driver.'},{'repo':'b','text':'Built a robot.'}],
             'own_repo_sources':{'a':{'summary':'Path planning only.'},'b':{'summary':'Supports cameras.'}},
             'owner_claims_policy':'Owner policy'}
    with pytest.raises(ValueError,match='unbound'):
        I.adjudicate_projects({'adjudicator_model':'judge','cache':ArtifactCache(tmp_path),'log':lambda _:None},payload,
            [M.Finding(agent='projects',severity='error',issue='Unsupported camera claim')])


def panel_world(tmp_path,monkeypatch,scores):
    from jobagent.steps import multi_agent as M
    (tmp_path/'resume.pdf').write_bytes(b'valid-pdf-fixture')
    seen=[]
    def evaluate(job,jd,text,rnd,sites,log,**kwargs):
        seen.append((rnd,sites))
        return {'scores':scores,'missing':{},'advice':[],'completed':list(scores)}
    monkeypatch.setattr(M.A,'evaluate',evaluate)
    from jobagent.state import JobDescription
    ctx={'job_dir':tmp_path,'jd':JobDescription(company='Fixture',title='Engineer'),'jd_text':'Exact JD',
         'log':lambda _:None,'sites':(),'incremental':True}
    state={'ctx':ctx,'best':{'scores':{},'f':None,'hard':[],'layout':0}}
    return ctx,state,seen


def test_final_panel_once_without_refinement_and_missing_provider_stays_incomplete(tmp_path,monkeypatch):
    ctx,state,seen=panel_world(tmp_path,monkeypatch,{'hireflow':80})
    best,history=final_panel(ctx,state,('hireflow','atsfriendly'))
    assert seen==[(0,('hireflow','atsfriendly'))]
    assert history[0]['panel']['complete'] is False and history[0]['panel']['average'] is None


@pytest.mark.parametrize('outcome',[90,70,'unavailable'])
def test_optional_refinement_scores_the_selected_bundle_and_restores_rejected_or_failed_versions(tmp_path,monkeypatch,outcome):
    from jobagent.steps import multi_agent as M
    from jobagent.tools import bundle
    ctx,state,_=panel_world(tmp_path,monkeypatch,{})
    def version(label):
        return {'scores':{},'f':M.T.Filled.model_construct(roles=[],skills=[],summary=label),
                'hard':[],'layout':0,'tex':label,'pdf':label.encode(),
                'projects':json.dumps({'version':label}),'filled':json.dumps({'summary':label})}
    old,new=version('old'),version('new');state['best']=old
    bundle.publish(tmp_path,M._version_files(old))
    def graph(**kwargs):
        assert kwargs=={'start':'route'}
        def run(*args,**kw):
            bundle.publish(tmp_path,M._version_files(version('later-rejected-render')))
            return {'best':new}
        return SimpleNamespace(invoke=run)
    monkeypatch.setattr(M,'build_graph',graph)
    scans=[]
    def scan(job,jd,text,rnd,sites,log,**kwargs):
        scans.append((job/'resume.pdf').read_bytes())
        assert kwargs['snapshot']['resume'].read_bytes()==scans[-1]
        if rnd and outcome=='unavailable':raise RuntimeError('provider unavailable')
        return {'scores':{'hireflow':outcome if rnd else 80},'missing':{},'advice':[],'completed':['hireflow']}
    monkeypatch.setattr(M.A,'evaluate',scan)
    if outcome=='unavailable':
        with pytest.raises(RuntimeError,match='provider unavailable'):final_panel(ctx,state,('hireflow',),True)
    else:
        best,history=final_panel(ctx,state,('hireflow',),True)
        assert best['pdf']==(b'new' if outcome==90 else b'old')
        assert len(history)==2
    assert scans==[b'old',b'new']
    expected='new' if outcome==90 else 'old'
    for name,content in M._version_files(version(expected)).items():
        actual=(tmp_path/name).read_bytes() if isinstance(content,bytes) else (tmp_path/name).read_text()
        assert actual==content


def test_ats_detects_mutation_of_the_scored_pdf(tmp_path,monkeypatch):
    from jobagent.steps import multi_agent as M
    ctx,state,seen=panel_world(tmp_path,monkeypatch,{'hireflow':80})
    def mutate(*a,**kwargs):
        (tmp_path/'resume.pdf').write_bytes(b'other')
        return {'scores':{'hireflow':80}}
    monkeypatch.setattr(M.A,'evaluate',mutate)
    with pytest.raises(RuntimeError,match='changed'):final_panel(ctx,state,('hireflow',))


def test_changed_jd_is_rejected_before_an_upload(tmp_path,monkeypatch):
    ctx,state,seen=panel_world(tmp_path,monkeypatch,{'hireflow':80})
    (tmp_path/'jd.txt').write_text('Changed posting')
    with pytest.raises(RuntimeError,match='before'):final_panel(ctx,state,('hireflow',))
    assert not seen


def test_mutation_cannot_relabel_an_immutable_scan_as_the_new_resume_and_jd(tmp_path,monkeypatch):
    import hashlib,sqlite3
    from jobagent import db
    from jobagent.steps import ats as A
    from jobagent.tools import ats_sites,ats_result
    from jobagent.state import JobDescription
    conn=sqlite3.connect(':memory:');conn.row_factory=sqlite3.Row
    conn.execute('CREATE TABLE ats_runs(id INTEGER PRIMARY KEY,job_dir TEXT,site TEXT,round INTEGER,score REAL,missing TEXT,raw TEXT,resume_sha TEXT)')
    monkeypatch.setattr(db,'connect',lambda:conn)
    jd=JobDescription(company='Fixture',title='Engineer')
    (tmp_path/'jd.json').write_text(jd.model_dump_json());(tmp_path/'jd.txt').write_text('Old posting')
    (tmp_path/'resume.pdf').write_bytes(b'Old PDF')
    frozen=tmp_path/'frozen.pdf';frozen.write_bytes(b'Old PDF')
    sha=hashlib.sha256(b'Old PDF').hexdigest();fingerprint=ats_sites.job_fingerprint('Old posting','Engineer','Fixture')
    def scan(path,*args,**kwargs):
        assert path.read_bytes()==b'Old PDF'
        (tmp_path/'resume.pdf').write_bytes(b'New PDF');(tmp_path/'jd.txt').write_text('New posting')
        yield ats_sites.SiteScore('hireflow',88,True)
    monkeypatch.setattr(ats_sites,'score_all',scan)
    A.evaluate(tmp_path,jd,'Old posting',0,('hireflow',),lambda _:None,
               snapshot={'resume':frozen,'identity':(sha,fingerprint)})
    row=conn.execute('SELECT * FROM ats_runs').fetchone()
    assert row['resume_sha']==sha and json.loads(row['raw'])['job_fingerprint']==fingerprint
    assert ats_result.gather(tmp_path,('hireflow',),conn=conn)['average'] is None


def test_letter_cannot_transfer_metric_to_another_employer(monkeypatch):
    paragraph=Paragraph(text='At Beta I cut latency by 30%.',evidence=[],bindings=[
        L.Binding(claim='At Beta I cut latency by 30%.',source_id='a',quote='Cut latency by 30%.')])
    letter=Letter.model_construct(paragraphs=[paragraph])
    bank={'a':{'entity':'Alpha','text':'Cut latency by 30%.'},'b':{'entity':'Beta','text':'Built APIs.'}}
    issues=L.check(letter,bank,None,'m',lambda _:None)
    assert any('different employer' in i for i in issues)


def test_incremental_letter_attaches_source_and_claim_in_code_without_duplicate_model_prose(monkeypatch):
    from jobagent.tools import cover_letter as CL,incremental_documents as I
    from pydantic import ValidationError
    bank={'a':{'entity':'Alpha','text':'Reduced latency by 30%.','kind':'employment'}}
    schema=CL.sourced_letter_schema(bank)
    data={'salutation':'Dear Hiring Team,','closing':'Sincerely','paragraphs':[
        {'sentences':[{'text':'I reduced latency by 30% at Alpha.','source_id':'a'}]},
        {'sentences':[{'text':'I would value the opportunity.','source_id':''}]},
        {'sentences':[{'text':'Thank you for considering my application.','source_id':''}]}]}
    letter=CL.attach_sources(schema.model_validate(data),bank)
    assert letter.paragraphs[0].bindings[0].claim==letter.paragraphs[0].text
    assert letter.paragraphs[0].bindings[0].quote==bank['a']['text']
    monkeypatch.setattr(I.llm,'writer',lambda *a,**kw:object())
    monkeypatch.setattr(I,'invoke',lambda *a,**kw:L.AttributionReview(issues=[]))
    assert L.check(letter,bank,None,'m',lambda _:None)==[]
    data['paragraphs'][0]['sentences'][0]['source_id']='invented'
    with pytest.raises(ValidationError):schema.model_validate(data)


def test_attribution_uses_the_same_dash_normalization_as_letter_rendering(monkeypatch):
    from jobagent.tools import incremental_documents as I
    monkeypatch.setattr(I.llm,'writer',lambda *a,**kw:object())
    monkeypatch.setattr(I,'invoke',lambda *a,**kw:L.AttributionReview(issues=[]))
    paragraph=Paragraph(text='I built tooling, improving speed by 30%.',evidence=[],bindings=[
        L.Binding(claim='I built tooling — improving speed by 30%.',source_id='a',quote='Improved speed by 30%.')])
    assert L.check(Letter.model_construct(paragraphs=[paragraph]),
                   {'a':{'entity':'Alpha','text':'Improved speed by 30%.'}},None,'m',lambda _:None)==[]


def test_target_company_and_posting_title_are_not_confused_with_candidate_employer_attribution(monkeypatch):
    from jobagent.tools import incremental_documents as I
    monkeypatch.setattr(I.llm,'writer',lambda *a,**kw:object())
    monkeypatch.setattr(I,'invoke',lambda *a,**kw:L.AttributionReview(issues=[]))
    text='I am interested in the Engineer position at Beta.'
    paragraph=Paragraph(text=text,evidence=[],bindings=[
        L.Binding(claim=text,source_id='posting',quote='Engineer at Beta')])
    bank={'posting':{'entity':'Job posting','text':'Engineer at Beta','kind':'posting'},
          'company':{'entity':'Beta','text':'Beta builds robots.','kind':'company'},
          'a':{'entity':'Alpha','text':'Built robots.','kind':'employment'}}
    assert L.check(Letter.model_construct(paragraphs=[paragraph]),bank,None,'m',lambda _:None)==[]


def test_opt_in_project_prompt_has_no_fake_achievement_example_or_contradictory_feature_permission():
    from jobagent.tools import projects as P
    from jobagent.state import JobDescription
    original=P.build_prompt([],JobDescription(title='Engineer',company='Fixture'),{})[0].content
    incremental=P.build_prompt([],JobDescription(title='Engineer',company='Fixture'),{},grounded=True)[0].content
    assert 'City traffic engineers' in original and 'invent plausible features' in original
    assert 'City traffic engineers' not in incremental and 'invent plausible features' not in incremental
    assert "repository's own supplied features" in incremental and 'impact estimates' in incremental


def test_jd_metric_cannot_supply_a_candidate_achievement():
    paragraph=Paragraph(text='I cut latency by 40%.',evidence=[],bindings=[
        L.Binding(claim='I cut latency by 40%.',source_id='a',quote='Cut latency by 30%.')])
    issues=L.check(Letter.model_construct(paragraphs=[paragraph]),{'a':{'entity':'Alpha','text':'Cut latency by 30%.'}},None,'m',lambda _:None)
    assert any('40' in i for i in issues)


def test_company_number_can_be_quoted_without_becoming_candidate_work(tmp_path,monkeypatch):
    from jobagent.tools import incremental_documents as I
    monkeypatch.setattr(I.llm,'writer',lambda *a,**kw:object())
    monkeypatch.setattr(I.llm,'structured_invoke',lambda *a,**kw:L.AttributionReview(issues=[]))
    paragraph=Paragraph(text='Beta launched its platform in 2025.',evidence=[],bindings=[
        L.Binding(claim='Beta launched its platform in 2025.',source_id='company',quote='Beta launched its platform in 2025.')])
    bank={'company':{'entity':'Beta','text':'Beta launched its platform in 2025.','kind':'company'}}
    assert L.check(Letter.model_construct(paragraphs=[paragraph]),bank,ArtifactCache(tmp_path),'m',lambda _:None)==[]
