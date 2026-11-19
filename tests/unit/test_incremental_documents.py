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


@pytest.mark.parametrize('tasks,changed',[
    (['Remove the unsupported medical-device domain claim.'],False),
    (['Remove the unsupported medical-device domain claim.'],True),
    (None,False),
])
def test_explicit_summary_repair_cannot_cache_unchanged_text_but_reconciliation_can(tmp_path,monkeypatch,tasks,changed):
    from jobagent.steps import multi_agent as M
    from jobagent.tools import incremental_documents as I
    from langchain_core.messages import SystemMessage
    before='Software engineer with experience developing Windows and web applications for medical devices and cloud platforms.'
    after='Software engineer with experience developing Windows and web applications, backend services and cloud platforms.'
    f=M.T.Filled.model_construct(summary=before,roles=[],skills=[])
    ctx={'job_dir':tmp_path,'r':SimpleNamespace(roles=[],skills=[]),'jd':None,'company':'Company',
         'models':{'summary':'m'},'cache':ArtifactCache(tmp_path/'cache'),'log':lambda _:None,'incremental':True}
    monkeypatch.setattr(M.llm,'writer',lambda *a,**kw:SimpleNamespace())
    monkeypatch.setattr(M.T,'build_fill_prompt',lambda *a,**kw:[SystemMessage('Write from the sources.'),HumanMessage('JD')])
    monkeypatch.setattr(M,'_context',lambda *a,**kw:'Current summary: '+before)
    monkeypatch.setattr(I.llm,'structured_invoke',lambda *a,**kw:M.SummaryOut(summary=after if changed else before))
    _,out=M._run_agent(ctx,'summary',f,tasks or [M.SUMMARY_RECONCILE],'')
    entries=list((tmp_path/'cache').glob('*.json'))
    if tasks and not changed:
        assert out is None and not entries
    else:
        assert out.summary==(after if changed else before) and len(entries)==1


def test_incremental_summary_schema_rejects_outputs_that_merge_would_discard():
    from pydantic import ValidationError
    from jobagent.steps import multi_agent as M
    with pytest.raises(ValidationError):M.IncrementalSummaryOut(summary='Engineer.')
    assert M.SummaryOut(summary='Engineer.').summary=='Engineer.'


@pytest.mark.parametrize('preposition',['at','for','with'])
def test_aspirational_company_destination_stays_reviewable_as_company_business(preposition):
    from jobagent.tools.incremental_documents import summary_review_parts
    candidate='Engineer with Windows and web application experience.'
    mission=f'Committed to advancing science through medical technology {preposition} Fixture Corp.'
    assert summary_review_parts(candidate+' '+mission,'Fixture Corp')==(
        candidate,['advancing science through medical technology'])
    # An unsupported business proposition is still present for factual checking.
    assert summary_review_parts('Committed to supporting retail banking at Fixture Corp.','Fixture Corp')==(
        '',['supporting retail banking'])
    assert summary_review_parts("Aligned with Fixture Corp's mission to deliver software as a service.",'Fixture Corp')==(
        '',['deliver software as a service'])


@pytest.mark.parametrize('text',[
    'Currently employed at Fixture Corp, advancing medical technology.',
    'Committed to advancing technology at Fixture Corp since 2020.',
    'Committed to advancing technology after I led a clinical team at Fixture Corp.',
    'Committed to advancing technology as CTO at Fixture Corp.',
    'Committed to advancing technology having built a clinical team at Fixture Corp.',
    'Committed to advancing technology for 3 years at Fixture Corp.',
    'Committed to advancing technology at Another Corp.',
])
def test_company_interest_projection_never_hides_history_or_another_company(text):
    from jobagent.tools.incremental_documents import summary_review_parts
    assert summary_review_parts(text,'Fixture Corp')==(text,[])


def test_company_business_criticism_cannot_be_reclassified_as_candidate_work(tmp_path,monkeypatch):
    from jobagent.steps import multi_agent as M
    from jobagent.tools import incremental_documents as I
    monkeypatch.setattr(M.T,'facts_block',lambda:'Owner policy')
    monkeypatch.setattr(I.llm,'writer',lambda *a,**kw:SimpleNamespace())
    def inspect(cache,stage,model_name,model,schema,messages,**kwargs):
        """Misclassify a real projected company phrase to exercise source type binding."""
        if stage=='review:summary':
            out=I.SummaryReview(findings=[I.SummaryFinding(kind='work',claim_quote='advancing medical technology',
                issue='Candidate lacks employment at the target company.',severity='error')])
            kwargs['validator'](out)
        return M.Review(findings=[])
    monkeypatch.setattr(I,'invoke',inspect)
    ctx={'job_dir':tmp_path,'r':SimpleNamespace(roles=[],skills=[],tex=''),
         'jd':SimpleNamespace(model_dump=lambda **kw:{'company':'Fixture Corp'}),
         'models':{'router':'m'},'cache':ArtifactCache(tmp_path/'cache'),'log':lambda _:None}
    f=M.T.Filled.model_construct(summary='Committed to advancing medical technology at Fixture Corp.',roles=[],skills=[])
    with pytest.raises(I.ReviewUnavailable):review(ctx,f)


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


def test_document_transport_preference_is_a_cache_dependency(tmp_path,monkeypatch):
    from jobagent.tools import incremental_documents as I
    calls=[]
    monkeypatch.setattr(I.llm,'structured_invoke',lambda *a,**kw:calls.append(kw['first']) or Output(text='done'))
    for preference in ('function_calling','function_calling','json_mode'):
        invoke(ArtifactCache(tmp_path),'experience','m',object(),Output,[HumanMessage('q')],first=preference)
    assert calls==['function_calling','json_mode']


def test_review_sampling_does_not_change_drafting_or_other_model_families(monkeypatch):
    from jobagent.tools import incremental_documents as I
    made=[]
    def writer(*args,**kwargs):
        model=SimpleNamespace(temperature=0,top_p=None,presence_penalty=None,extra_body=None)
        made.append(model)
        return model
    monkeypatch.setattr(I.llm,'writer',writer)
    qwen=I.review_model('qwen35-397b-a17b')
    assert qwen.temperature==.7 and qwen.top_p==.8 and qwen.presence_penalty==1.5
    assert qwen.extra_body=={'top_k':20,'chat_template_kwargs':{'enable_thinking':False}}
    other=I.review_model('another-reviewer')
    draft=I.llm.writer(model='qwen35-397b-a17b')
    assert other.temperature==draft.temperature==0
    assert other.extra_body is draft.extra_body is None
    assert I._review_method('qwen35-397b-a17b')=='json_mode'
    assert I._review_method('Qwen3.5-397B')=='json_mode'
    assert I._review_method('another-reviewer')==I.get_settings().llm_structured_method


def test_inference_parameters_invalidate_review_cache(tmp_path,monkeypatch):
    from jobagent.tools import incremental_documents as I
    calls=[]
    monkeypatch.setattr(I.llm,'structured_invoke',lambda *a,**kw:calls.append(1) or Output(text='done'))
    for thinking in (False,False,True):
        model=SimpleNamespace(temperature=.7,extra_body={'chat_template_kwargs':{'enable_thinking':thinking}})
        invoke(ArtifactCache(tmp_path),'review','m',model,Output,[HumanMessage('q')])
    assert len(calls)==2


def test_unbound_review_output_is_corrected_inside_the_shared_bounded_loop(tmp_path):
    from jobagent.tools import incremental_documents as I
    class Model:
        model_name='test-reviewer'
        def __init__(self):self.seen=[]
        def with_structured_output(self,schema,**kwargs):self.schema=schema;return self
        def invoke(self,messages):
            self.seen.append(messages)
            return self.schema.model_validate({'text':'invented quote' if len(self.seen)==1 else 'exact source'})
    def bind(result):
        if result.text!='exact source':raise ValueError('quote must match the exact source')
    model=Model();cache=ArtifactCache(tmp_path)
    out=invoke(cache,'review','test-reviewer',model,Output,[HumanMessage('Review the source')],validator=bind)
    assert out.text=='exact source' and len(model.seen)==2
    assert 'quote must match the exact source' in model.seen[1][-1].content
    assert invoke(cache,'review','test-reviewer',model,Output,[HumanMessage('Review the source')],validator=bind)==out
    assert len(model.seen)==2


def test_cache_hits_do_not_wait_for_a_reasoner_dispatch_slot(tmp_path,monkeypatch):
    from jobagent.tools import incremental_documents as I
    monkeypatch.setattr(I.llm,'structured_invoke',lambda *a,**kw:Output(text='done'))
    invoke(ArtifactCache(tmp_path),'review','m',object(),Output,[HumanMessage('q')])
    class Occupied:
        def __enter__(self):raise AssertionError('a cache hit must not acquire the provider dispatch lock')
        def __exit__(self,*args):pass
    assert invoke(ArtifactCache(tmp_path),'review','m',object(),Output,[HumanMessage('q')],request_lock=Occupied()).text=='done'


def test_long_reasoner_reviews_dispatch_serially_while_other_reviews_remain_independent(tmp_path,monkeypatch):
    import threading,time
    from jobagent.steps import multi_agent as M
    from jobagent.tools import incremental_documents as I
    monkeypatch.setattr(M.T,'facts_block',lambda:'Owner policy')
    monkeypatch.setattr(I.llm,'writer',lambda *a,**kw:SimpleNamespace(model_name=kw['model']))
    lock=threading.Lock();active=0;peak=0;calls=[]
    def generate(model,schema,messages,**kwargs):
        nonlocal active,peak
        if model.model_name==I.get_settings().model_planner:
            with lock:active+=1;peak=max(peak,active);calls.append(model.model_name)
            time.sleep(.02)
            with lock:active-=1
        return M.Review(findings=[])
    monkeypatch.setattr(I.llm,'structured_invoke',generate)
    ctx={'job_dir':tmp_path,'r':SimpleNamespace(roles=[],skills=[]),'jd':SimpleNamespace(model_dump=lambda **kw:{}),
         'models':{'router':'cheap-reviewer'},'cache':ArtifactCache(tmp_path/'cache'),'log':lambda _:None}
    review(ctx,M.T.Filled.model_construct(summary='Engineer.',roles=[],skills=[]))
    assert peak==1 and len(calls)==3  # projects, summary and skills share the bounded reasoner


def test_failed_review_cancels_queued_reasoner_calls_and_reports_the_actual_failed_section(tmp_path,monkeypatch):
    from jobagent.steps import multi_agent as M
    from jobagent.tools import incremental_documents as I
    monkeypatch.setattr(M.T,'facts_block',lambda:'Owner policy')
    monkeypatch.setattr(I.llm,'writer',lambda *a,**kw:SimpleNamespace(model_name=kw['model']))
    calls=[]
    def generate(model,schema,messages,**kwargs):
        if model.model_name==I.get_settings().model_planner:
            calls.append(messages[0].content.split('Review ONLY ')[1].split('.')[0])
            raise I.llm.StructuredError('provider timed out','timeout')
        return M.Review(findings=[])
    monkeypatch.setattr(I.llm,'structured_invoke',generate)
    cache=ArtifactCache(tmp_path/'cache')
    ctx={'job_dir':tmp_path,'r':SimpleNamespace(roles=[],skills=[]),'jd':SimpleNamespace(model_dump=lambda **kw:{}),
         'models':{'router':'cheap-reviewer'},'cache':cache,'log':lambda _:None}
    with pytest.raises(I.ReviewUnavailable) as error:
        review(ctx,M.T.Filled.model_construct(summary='Engineer.',roles=[],skills=[]))
    assert len(calls)==1
    assert str(error.value).startswith(calls[0]+' content review unavailable')
    assert 'timeout' in str(error.value)
    assert all(e.get('ok') is not True for e in cache.events if e.get('cancelled_before_dispatch'))


def test_title_only_repairs_cannot_generate_paragraphs_or_edit_other_projects(tmp_path,monkeypatch):
    from jobagent.steps import multi_agent as M,ats as A
    from pydantic import ValidationError
    (tmp_path/'projects.json').write_text('{}')
    monkeypatch.setattr(M.llm,'writer',lambda *a,**kw:object())
    monkeypatch.setattr(M,'_context',lambda *a,**kw:'Sources')
    captured=[]
    def generate(ctx,stage,model,schema,messages,**kwargs):
        captured.append(schema)
        return schema.model_validate({'projects':[{'project':2,'title':'Readable Project'}]})
    monkeypatch.setattr(M,'_invoke',generate)
    _,out=M._run_agent({'job_dir':tmp_path,'models':{'projects':'m'},'log':lambda _:None,'incremental':True},
        'projects',None,["Project 2 is titled with its bare repo name 'project': give it a readable name."],'')
    assert out is not None and out.projects[0].model_dump()=={'project':2,'title':'Readable Project'}
    with pytest.raises(ValidationError):captured[0].model_validate({'projects':[{'project':1,'title':'Unrequested'}]})
    st={'kept':2,'ranked':[{'title':'One','paragraph':'Keep one.'},{'title':'Two','paragraph':'Keep two.'}]}
    result=json.loads(A.apply_project_fixes(json.dumps(st),out.projects))
    assert result['ranked']==[st['ranked'][0],{'title':'Readable Project','paragraph':'Keep two.'}]


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
    monkeypatch.setattr(I.llm,'writer',lambda *a,**kw:SimpleNamespace())
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
    monkeypatch.setattr(I.llm,'writer',lambda *a,**kw:SimpleNamespace())
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
    monkeypatch.setattr(I.llm,'writer',lambda *a,**kw:SimpleNamespace())
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
    monkeypatch.setattr(I.llm,'writer',lambda *a,**kw:SimpleNamespace())
    seen=[];models={}
    def inspect(cache,stage,model_name,model,schema,messages,**kwargs):
        if stage=='review:summary':seen.extend(messages)
        models[stage]=model_name
        assert kwargs['first']==I._review_method(model_name)
        return M.Review(findings=[])
    monkeypatch.setattr(I,'invoke',inspect)
    ctx={'job_dir':tmp_path,'r':SimpleNamespace(roles=[],skills=[]),'jd':SimpleNamespace(model_dump=lambda **kw:{'title':'Engineer','company':'Beta'}),
         'models':{'router':'m'},'cache':ArtifactCache(tmp_path/'cache'),'log':lambda _:None,
         'company':'Beta builds robots.'}
    review(ctx,M.T.Filled.model_construct(summary='Engineer interested in Beta.',roles=[],skills=[]))
    assert 'Today is ' in seen[0].content and 'not a claim of previous employment' in seen[0].content
    assert json.loads(seen[1].content)['company_context']=='Beta builds robots.'
    assert json.loads(seen[1].content)['target_job_identity']=={'title':'Engineer','company':'Beta'}
    assert 'resume headline' in seen[0].content and 'not employment history' in seen[0].content
    assert models['review:summary']==models['review:projects']==models['review:skills']==I.get_settings().model_planner
    assert models['review:experience']=='m'


@pytest.mark.parametrize('category,term',[
    ('Languages','SQL'),  # Present in the template is not present in this draft.
    ('Runtime','Java'),  # A term cannot migrate between displayed categories.
    ('Languages','C'),  # Punctuation distinguishes C from C++.
])
def test_skill_critic_cannot_request_missing_skills_or_move_its_target(category,term):
    from jobagent.tools.incremental_documents import SkillReview,bind_skill_findings
    result=SkillReview(findings=[{'category':category,'term':term,'kind':'incorrect_category',
                                 'category_scope':'intrinsic_type','membership':'unrelated',
                                 'issue':'Move this term.','severity':'error'}])
    with pytest.raises(ValueError,match='displayed term/category pair'):
        bind_skill_findings(result,[('Languages',['Java','C++']),('Runtime',['JVM'])])


def test_skill_critic_binds_actual_category_errors_and_parenthesized_tools():
    from jobagent.tools.incremental_documents import SkillReview,bind_skill_findings
    skills=[('Languages',['Java','JVM']),('ML',['NLP (spaCy, NLTK)'])]
    result=SkillReview(findings=[{'category':'languages','term':'JVM','kind':'incorrect_category',
                                 'category_scope':'intrinsic_type','membership':'unrelated',
                                 'issue':'JVM is a runtime.','severity':'error'},
                                {'category':'ML','term':'NLP (spaCy, NLTK)','kind':'incorrect_category',
                                 'category_scope':'application_domain','membership':'used_in_domain',
                                 'issue':'An example advisory finding.','severity':'suggestion'}])
    bind_skill_findings(result,skills)


def test_bound_skill_review_keeps_current_scope_and_returns_shared_findings(tmp_path,monkeypatch):
    from jobagent.steps import multi_agent as M
    from jobagent.tools import incremental_documents as I
    monkeypatch.setattr(M.T,'facts_block',lambda:'Owner policy')
    monkeypatch.setattr(I.llm,'writer',lambda *a,**kw:SimpleNamespace())
    def inspect(cache,stage,model_name,model,schema,messages,**kwargs):
        if stage!='review:skills':return M.Review(findings=[])
        assert schema is I.SkillReview
        assert 'omitted template skills are NOT errors' in messages[0].content
        result=schema(findings=[{'category':'Languages','term':'JVM','kind':'incorrect_category',
                                 'category_scope':'intrinsic_type','membership':'unrelated',
                                 'issue':'Use a runtime heading.','severity':'error'}])
        kwargs['validator'](result)
        return result
    monkeypatch.setattr(I,'invoke',inspect)
    ctx={'job_dir':tmp_path,'r':SimpleNamespace(roles=[],skills=[]),'jd':SimpleNamespace(model_dump=lambda **kw:{}),
         'models':{'router':'m'},'cache':ArtifactCache(tmp_path/'cache'),'log':lambda _:None}
    filled=M.T.Filled.model_construct(summary='Engineer.',roles=[],skills=[M.T.SkillLine(category='Languages',terms=['JVM'])])
    findings=review(ctx,filled)
    assert len(findings)==1 and isinstance(findings[0],M.Finding)
    assert findings[0].agent=='skills' and 'Languages: JVM:' in findings[0].issue


@pytest.mark.parametrize('category,term,scope,membership',[
    ('Web Development','.NET','application_domain','used_in_domain'),
    ('Mobile Development','Android','application_domain','used_in_domain'),
    ('Distributed Systems','Enterprise Authentication','application_domain','used_in_domain'),
    ('Distributed Systems','Multi-tenant Security','application_domain','used_in_domain'),
    ('AI & Development Tools','Python','mixed','intrinsic_member'),
    ('Protocols','Ambiguous acronym','application_domain','uncertain'),
])
def test_valid_or_ambiguous_skill_membership_cannot_force_repair(tmp_path,monkeypatch,category,term,scope,membership):
    from jobagent.steps import multi_agent as M
    from jobagent.tools import incremental_documents as I
    monkeypatch.setattr(M.T,'facts_block',lambda:'Owner policy')
    monkeypatch.setattr(I.llm,'writer',lambda *a,**kw:SimpleNamespace())
    def inspect(cache,stage,model_name,model,schema,messages,**kwargs):
        if stage!='review:skills':return M.Review(findings=[])
        result=schema(findings=[{'category':category,'term':term,'kind':'incorrect_category',
            'category_scope':scope,'membership':membership,'issue':'Prefer a narrower category.','severity':'error'}])
        kwargs['validator'](result)
        return result
    monkeypatch.setattr(I,'invoke',inspect)
    ctx={'job_dir':tmp_path,'r':SimpleNamespace(roles=[],skills=[]),'jd':SimpleNamespace(model_dump=lambda **kw:{}),
         'models':{'router':'m'},'cache':ArtifactCache(tmp_path/'cache'),'log':lambda _:None}
    filled=M.T.Filled.model_construct(summary='Engineer.',roles=[],skills=[M.T.SkillLine(category=category,terms=[term])])
    findings=review(ctx,filled)
    assert len(findings)==1 and findings[0].severity=='suggestion'


def test_intrinsic_language_heading_cannot_be_satisfied_by_runtime_usage():
    from jobagent.tools.incremental_documents import SkillFinding
    with pytest.raises(ValueError,match='requires type membership'):
        SkillFinding(category='Programming Languages',term='JVM',kind='incorrect_category',
                     category_scope='intrinsic_type',membership='used_in_domain',issue='Runtime.',severity='error')


def test_skills_context_keeps_only_verbatim_relevant_bounded_posting_excerpts():
    from jobagent.tools.incremental_documents import skill_posting_context
    relevant='Bonus: AI frameworks and open standards such as MCP, A2A, and enterprise authentication.'
    raw='Unrelated benefits.\n'+relevant+'\n'+relevant+'\nSalary: 100000.\nMCP is not ownership evidence.'
    assert skill_posting_context(raw,[('Protocols',['MCP','A2A'])])==[
        relevant,'MCP is not ownership evidence.']
    assert skill_posting_context('We build MCPP tools.',[('Protocols',['MCP'])])==[]
    assert sum(map(len,skill_posting_context(('MCP '+('x'*800)+'\n')*50,[('Protocols',['MCP'])])))<=5000
    assert skill_posting_context(raw,[])==[]


def test_scoped_skills_review_receives_acronym_context_without_extra_review_calls(tmp_path,monkeypatch):
    from jobagent.steps import multi_agent as M
    from jobagent.tools import incremental_documents as I
    monkeypatch.setattr(M.T,'facts_block',lambda:'Owner policy')
    monkeypatch.setattr(I.llm,'writer',lambda *a,**kw:SimpleNamespace())
    calls=[]
    def inspect(cache,stage,model_name,model,schema,messages,**kwargs):
        calls.append(stage)
        payload=json.loads(messages[1].content)
        if stage=='review:skills':
            assert payload['posting_skill_context']==['Experience with AI open standards such as MCP and A2A.']
            assert payload['category_components']=={'Networking & Protocols':['Networking','Protocols']}
            assert 'Do not invent an unrelated expansion' in messages[0].content
            assert 'not simultaneous requirements' in messages[0].content
        else:assert 'posting_skill_context' not in payload
        return M.Review(findings=[])
    monkeypatch.setattr(I,'invoke',inspect)
    ctx={'job_dir':tmp_path,'r':SimpleNamespace(roles=[],skills=[]),'jd':SimpleNamespace(model_dump=lambda **kw:{}),
         'models':{'router':'m'},'cache':ArtifactCache(tmp_path/'cache'),'log':lambda _:None,
         'jd_text':'Experience with AI open standards such as MCP and A2A.'}
    filled=M.T.Filled.model_construct(summary='Engineer.',roles=[],skills=[M.T.SkillLine(category='Networking & Protocols',terms=['MCP','A2A'])])
    assert review(ctx,filled)==[]
    assert sorted(calls)==['review:experience','review:projects','review:skills','review:summary']


@pytest.mark.parametrize('summary,masked',[
    ('Engineer with 3+ years of professional experience in Python.',True),
    ('Engineer with 3 years of Python experience.',True),
    ('Engineer with 3+ years building real-time video streaming pipelines.',True),
    ('Engineer with 3 years after the bachelor degree.',False),
    ('Engineer at Acme since 2018.',False),
    ('Engineer with 3 years of clearance experience.',False)])
def test_semantic_review_separates_policy_authorized_tenure_from_protected_history(tmp_path,monkeypatch,summary,masked):
    from jobagent.steps import multi_agent as M
    from jobagent.tools import incremental_documents as I
    monkeypatch.setattr(M.T,'facts_block',lambda:'Owner policy')
    monkeypatch.setattr(M.T,'declared_claims',lambda:{'years_experience':5})
    monkeypatch.setattr(I.llm,'writer',lambda *a,**kw:SimpleNamespace())
    seen=[]
    def inspect(cache,stage,model_name,model,schema,messages,**kwargs):
        if stage=='review:summary':seen.append(json.loads(messages[1].content))
        return M.Review(findings=[])
    monkeypatch.setattr(I,'invoke',inspect)
    ctx={'job_dir':tmp_path,'r':SimpleNamespace(roles=[SimpleNamespace(company='Acme',title='Engineer',dates='2020–2026')],skills=[]),'jd':SimpleNamespace(model_dump=lambda **kw:{}),
         'models':{'router':'m'},'cache':ArtifactCache(tmp_path/'cache'),'log':lambda _:None}
    filled=M.T.Filled.model_construct(summary=summary,roles=[M.T.FillRole.model_construct(role=0,bullets=[])],skills=[])
    review(ctx,filled)
    assert (seen[0]['summary']!=summary)==masked
    assert 'owner-declared' not in seen[0]['summary']
    assert filled.summary==summary
    assert ('dates' in seen[0]['roles'][0]) is not masked
    if 'Python' in summary:assert 'Python' in seen[0]['summary']
    assert M.summary_issues('Engineer with 6.2 years of experience.',False)


def test_tenure_review_keeps_employer_history_and_requires_an_owner_policy():
    from jobagent.tools.incremental_documents import summary_review_text
    text='Engineer with 3 years at Acme. Built Python services for 2 years.'
    reviewed=summary_review_text(text,5,['Acme'])
    assert '3 years at Acme' in reviewed and '2 years' not in reviewed and 'Python' in reviewed
    assert summary_review_text(text,None,['Acme'])==text


def test_review_omits_only_the_checked_headline_and_keeps_explicit_employment_claims():
    from jobagent.tools.incremental_documents import summary_review_text
    title='Member of Technical Staff, Real Time Systems'
    original=title+' with experience deploying PyTorch models to production.'
    assert summary_review_text(original,None,target_title=title)==(
        'Candidate with experience deploying PyTorch models to production.')
    explicit=title+' at Acme since 2020. Holds a clearance for 3 years.'
    reviewed=summary_review_text(explicit,5,['Acme'],target_title=title)
    assert 'at Acme since 2020' in reviewed and 'clearance for 3 years' in reviewed
    assert original.startswith(title)


@pytest.mark.parametrize('summary,title',[
    ('C# Engineer with experience.','C++ Engineer'),
    ('Software Engineering Lead with experience.','Software Engineer'),
    ('Engineer with experience.','')])
def test_unmatched_or_partial_headlines_are_never_hidden_from_review(summary,title):
    from jobagent.tools.incremental_documents import summary_review_text
    assert summary_review_text(summary,None,target_title=title)==summary


def test_mission_projection_preserves_the_factual_proposition_without_implying_employment():
    from jobagent.tools.incremental_documents import summary_review_parts
    original="Candidate with Python experience. Committed to advancing Beta's mission of building safer systems."
    body,missions=summary_review_parts(original,'Beta')
    assert body=='Candidate with Python experience.'
    assert missions==['building safer systems']
    # This deliberately incorrect proposition still goes to the factual reviewer.
    body,missions=summary_review_parts("Committed to Beta's mission of selling lunar submarines.",'Beta')
    assert missions==['selling lunar submarines']


@pytest.mark.parametrize('sentence',[
    "Committed to Beta's mission of building systems where I worked since 2020.",
    "Committed to Beta's mission of building systems after five years at Acme.",
    "Committed to Other's mission of building safer systems.",
    'I worked at Beta building safer systems.',
])
def test_mission_projection_does_not_hide_explicit_history_or_other_companies(sentence):
    from jobagent.tools.incremental_documents import summary_review_parts
    assert summary_review_parts(sentence,'Beta')==(sentence,[])


@pytest.mark.parametrize('summary,needs_education',[
    ('Engineer with experience building PyTorch systems.',False),
    ('Engineer with a PhD in computing.',True),
    ('Engineer with five post-baccalaureate years.',True),
    ('Engineer who graduated from Example University in 2024.',True)])
def test_summary_education_context_is_scoped_and_excludes_template_comments(tmp_path,monkeypatch,summary,needs_education):
    from jobagent.tools import incremental_documents as I
    from jobagent.steps import multi_agent as M
    seen=[]
    def inspect(cache,stage,model_name,model,schema,messages,**kwargs):
        if stage=='review:summary':seen.append(json.loads(messages[1].content))
        return schema(findings=[])
    monkeypatch.setattr(I,'invoke',inspect)
    monkeypatch.setattr(I.llm,'writer',lambda *a,**kw:SimpleNamespace())
    tex='\\section{Education}\nMaster at Example University, May 2024.\n% Old commented credential\n\\section{Skills}'
    ctx={'job_dir':tmp_path,'r':SimpleNamespace(roles=[],skills=[],tex=tex),
         'jd':SimpleNamespace(model_dump=lambda **kw:{}),'models':{'router':'m'},
         'cache':ArtifactCache(tmp_path/'cache'),'log':lambda _:None}
    review(ctx,M.T.Filled.model_construct(summary=summary,roles=[],skills=[]))
    assert bool(seen[0]['education']) is needs_education
    assert 'commented credential' not in seen[0]['education']


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


def test_title_prefix_is_deterministic_and_incremental_review_errors_remain_blocking(tmp_path,monkeypatch):
    from jobagent.steps import multi_agent as M
    assert M._title_prefix('Autonomy Platform Engineer with 1+ years of experience.','Autonomy Platform Engineer')
    assert not M._title_prefix('Platform Engineer with experience.','Autonomy Platform Engineer')
    findings=[M.Finding(agent='summary',severity='error',issue='Incorrect critic claim about title or lower-bound years')]
    monkeypatch.setattr(M,'content_review',lambda *a:findings)
    f=M.T.Filled.model_construct(summary='Autonomy Platform Engineer with 1+ years.',roles=[],skills=[])
    reviewed=M._reviewed_content({'job_dir':tmp_path,'incremental':True},f,{})
    assert reviewed['semantic_hard']==['summary: '+findings[0].issue]
    assert M._reviewed_content({'job_dir':tmp_path},f,{})['semantic_hard']==[]
    assert reviewed['content_findings']==findings


def test_incremental_draft_schema_matches_the_actual_new_bullet_limit():
    from jobagent.steps import multi_agent as M
    r=SimpleNamespace(roles=[SimpleNamespace(title='Engineer',company='Fixture')])
    compact=M.experience_schema(r,compact=True).model_json_schema()
    ordinary=M.experience_schema(r).model_json_schema()
    assert compact['$defs']['CompactFillBullet']['properties']['text']['maxLength']==M.T.BULLET_MAX
    assert ordinary['$defs']['FillBullet']['properties']['text']['maxLength']==M.T.BULLET_VERBATIM_MAX


def test_posting_bounds_are_exposed_and_enforced_in_draft_and_patch_schemas():
    from pydantic import ValidationError
    from jobagent.state import JobDescription
    from jobagent.steps import multi_agent as M
    r=SimpleNamespace(roles=[SimpleNamespace(title='Engineer',company='Fixture')])
    jd=JobDescription(company='Fixture',title='Engineer',responsibilities=['Build Python APIs.','Test Java services.'])
    draft=M.experience_schema(r,compact=True,jd=jd)
    patch=M.incremental_repair_schema(jd)
    for schema,definition in ((draft,'PostingDraftBullet'),(patch,'PostingBulletFix')):
        bounds=schema.model_json_schema()['$defs'][definition]['properties']['duties']['items']
        assert bounds['minimum']==0 and bounds['exclusiveMaximum']==2
    bullet={'text':'Built Python APIs, reducing latency by 20%.','duties':[1]}
    draft.model_validate({'role_0':[bullet]*M.T.MIN_BULLETS,'notes':''})
    assert isinstance(patch.model_validate({'fixes':[{'role':0,'bullet':1,**bullet}]}),M.IncrementalRepair)
    for invalid in (-1,2,True,'1'):
        wrong={**bullet,'duties':[invalid]}
        with pytest.raises(ValidationError):draft.model_validate({'role_0':[wrong]+[bullet]*(M.T.MIN_BULLETS-1),'notes':''})
        with pytest.raises(ValidationError):patch.model_validate({'fixes':[{'role':0,'bullet':1,**wrong}]})


def test_empty_posting_is_rejected_before_requesting_impossible_coverage():
    from jobagent.state import JobDescription
    from jobagent.steps import multi_agent as M
    jd=JobDescription(company='Fixture',title='Engineer')
    r=SimpleNamespace(roles=[SimpleNamespace(title='Engineer',company='Fixture')])
    with pytest.raises(ValueError,match='no experience coverage items'):M.experience_schema(r,compact=True,jd=jd)
    with pytest.raises(ValueError,match='no experience coverage items'):M.incremental_repair_schema(jd)


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
    monkeypatch.setattr(I.llm,'writer',lambda *a,**kw:SimpleNamespace())
    monkeypatch.setattr(I,'invoke',lambda *a,**kw:I.ProjectVerdicts(verdicts=[I.ProjectVerdict(
        finding=0,project=1,status='rejected',claim_quote='Built quality inspection workflows.',
        source_quote='inventory/quality-inspection management system',reason='The own source explicitly supports the capability.')]))
    payload={'displayed':[{'repo':'owner/project','text':'Built quality inspection workflows.'}],
             'own_repo_sources':{'owner/project':{'summary':'An inventory/quality-inspection management system.'}},
             'owner_claims_policy':'Work impact metrics are allowed.'}
    error=I.ProjectFinding(project=1,severity='error',issue='Quality inspection is not in the source')
    assert I.adjudicate_projects({'adjudicator_model':'judge','cache':ArtifactCache(tmp_path),'log':lambda _:None},payload,[error])==[]


def _summary_judgment_fixture(tmp_path,monkeypatch,*,kind='work',claim='autonomous vehicles',status='rejected',source_id='role:0',source_quote='autonomous boats'):
    from jobagent.tools import incremental_documents as I
    payload={'summary':f'Candidate with experience in {claim}.','roles':[{'employer':'Acme','bullets':['Built C++ data systems for autonomous boats.']}],
             'projects':[],'skills':[('Languages',['C++'])],'education':'Master of Science from Arizona State University.',
             'company_context':'Zoox builds autonomous vehicles.','owner_claims_policy':'Generated duties and domain tenure are authorized.'}
    finding=I.SummaryFinding(kind=kind,claim_quote=claim,issue='A proposed unsupported claim.',severity='error')
    verdict=I.SummaryVerdict(finding=0,status=status,claim_quote=claim,source_id=source_id,source_quote=source_quote,
                            reason='Checked against the exact source and owner policy.')
    monkeypatch.setattr(I.llm,'writer',lambda *a,**kw:SimpleNamespace())
    monkeypatch.setattr(I,'invoke',lambda *a,**kw:I.SummaryVerdicts(verdicts=[verdict]))
    ctx={'models':{'router':'independent-judge'},'cache':ArtifactCache(tmp_path),'log':lambda _:None}
    return I,ctx,payload,finding,verdict


def test_summary_judge_can_reject_incorrect_domain_criticism_using_its_actual_role(tmp_path,monkeypatch):
    I,ctx,payload,finding,_=_summary_judgment_fixture(tmp_path,monkeypatch)
    assert I.adjudicate_summary(ctx,payload,[finding])==[]


@pytest.mark.parametrize('kind,claim,source_id,source_quote,message',[
    ('work','autonomous vehicles','company','autonomous vehicles','source type'),
    ('work','autonomous vehicles','role:0','autonomous cars','unbound'),
    ('credential','PhD from Stanford University','education','Master of Science','different degree'),
    ('credential','Master from Stanford University','education','Master of Science','another institution'),
    ('employment','worked at Google','role:0','autonomous boats','another employer')])
def test_summary_judge_cannot_borrow_incompatible_or_unbound_evidence(tmp_path,monkeypatch,kind,claim,source_id,source_quote,message):
    I,ctx,payload,finding,_=_summary_judgment_fixture(tmp_path,monkeypatch,
        kind=kind,claim=claim,source_id=source_id,source_quote=source_quote)
    with pytest.raises(ValueError,match=message):I.adjudicate_summary(ctx,payload,[finding])


def test_summary_judge_cannot_move_its_finding_to_another_real_claim(tmp_path,monkeypatch):
    I,ctx,payload,finding,verdict=_summary_judgment_fixture(tmp_path,monkeypatch)
    payload['summary']+=' Experience with C++.';verdict.claim_quote='C++'
    with pytest.raises(ValueError,match='cannot change'):I.adjudicate_summary(ctx,payload,[finding])


def test_summary_judge_preserves_genuine_credential_errors_and_honest_uncertainty(tmp_path,monkeypatch):
    I,ctx,payload,finding,verdict=_summary_judgment_fixture(tmp_path,monkeypatch,
        kind='credential',claim='PhD from Stanford University',status='confirmed',
        source_id='education',source_quote='Master of Science')
    assert I.adjudicate_summary(ctx,payload,[finding])[0].severity=='error'
    verdict.status='uncertain';verdict.source_quote=''
    def invoke(*args,**kwargs):
        result=I.SummaryVerdicts(verdicts=[verdict])
        assert not kwargs['cache_when'](result)
        return result
    monkeypatch.setattr(I,'invoke',invoke)
    assert I.adjudicate_summary(ctx,payload,[finding])[0].issue.startswith('uncertain claim')


@pytest.mark.parametrize('kind,quote',[('work','PhD'),('work','claim absent from the summary')])
def test_summary_critic_must_bind_its_actual_claim_and_credential_type(tmp_path,monkeypatch,kind,quote):
    from jobagent.tools import incremental_documents as I
    from jobagent.steps import multi_agent as M
    monkeypatch.setattr(I.llm,'writer',lambda *a,**kw:SimpleNamespace())
    def invoke(cache,stage,model_name,model,schema,messages,**kwargs):
        if stage=='review:summary':return I.SummaryReview(findings=[I.SummaryFinding(
            kind=kind,claim_quote=quote,issue='Unsupported claim.',severity='error')])
        return schema(findings=[])
    monkeypatch.setattr(I,'invoke',invoke)
    monkeypatch.setattr(I,'adjudicate_summary',lambda *a:pytest.fail('Unbound or misclassified claim reached the judge'))
    ctx={'job_dir':tmp_path,'r':SimpleNamespace(roles=[],skills=[]),
         'jd':SimpleNamespace(model_dump=lambda **kw:{}),'models':{'router':'m'},
         'cache':ArtifactCache(tmp_path/'cache'),'log':lambda _:None}
    with pytest.raises(I.ReviewUnavailable,match='summary content review unavailable'):
        I.review(ctx,M.T.Filled.model_construct(summary='Engineer with a PhD.',roles=[],skills=[]))


def test_noncacheable_uncertainty_returns_once_without_sampling_for_confidence(tmp_path,monkeypatch):
    from jobagent.tools import incremental_documents as I
    calls=[]
    monkeypatch.setattr(I.llm,'structured_invoke',lambda *a,**kw:calls.append(1) or Output(text='uncertain'))
    result=I.invoke(ArtifactCache(tmp_path),'adjudicate:summary','m',object(),Output,[HumanMessage('Evidence')],
                    cache_when=lambda out:out.text!='uncertain')
    assert result.text=='uncertain' and calls==[1] and not list(tmp_path.glob('*.json'))


def test_adjudication_cannot_borrow_another_projects_source(tmp_path,monkeypatch):
    from jobagent.steps import multi_agent as M
    from jobagent.tools import incremental_documents as I
    monkeypatch.setattr(I.llm,'writer',lambda *a,**kw:SimpleNamespace())
    monkeypatch.setattr(I,'invoke',lambda *a,**kw:I.ProjectVerdicts(verdicts=[I.ProjectVerdict(
        finding=0,project=1,status='rejected',claim_quote='Built a camera driver.',source_quote='Supports cameras.',
        reason='Unbound quote from another repository')]))
    payload={'displayed':[{'repo':'a','text':'Built a camera driver.'},{'repo':'b','text':'Built a robot.'}],
             'own_repo_sources':{'a':{'summary':'Path planning only.'},'b':{'summary':'Supports cameras.'}},
             'owner_claims_policy':'Owner policy'}
    with pytest.raises(ValueError,match='unbound'):
        I.adjudicate_projects({'adjudicator_model':'judge','cache':ArtifactCache(tmp_path),'log':lambda _:None},payload,
            [I.ProjectFinding(project=1,severity='error',issue='Unsupported camera claim')])


@pytest.mark.parametrize('quote',['','Path planning only.'])
def test_uncertain_project_verdict_blocks_without_retrying_for_confidence_or_caching(tmp_path,monkeypatch,quote):
    from jobagent.tools import incremental_documents as I
    monkeypatch.setattr(I.llm,'writer',lambda *a,**kw:SimpleNamespace())
    calls=[]
    def decide(*args,**kwargs):
        """Return honest uncertainty once, after exercising binding and cache policy."""
        result=I.ProjectVerdicts(verdicts=[I.ProjectVerdict(
            finding=0,project=1,status='uncertain',claim_quote='Built a camera driver.',
            source_quote=quote,reason='The available repository evidence is insufficient.')])
        kwargs['validator'](result)
        assert not kwargs['cache_when'](result)
        calls.append(1)
        return result
    monkeypatch.setattr(I,'invoke',decide)
    payload={'displayed':[{'repo':'a','text':'Built a camera driver.'}],
             'own_repo_sources':{'a':{'summary':'Path planning only.'}},'owner_claims_policy':'Owner policy'}
    out=I.adjudicate_projects({'adjudicator_model':'judge','cache':ArtifactCache(tmp_path),'log':lambda _:None},payload,
        [I.ProjectFinding(project=1,severity='error',issue='Camera claim unsupported')])
    assert len(out)==1 and out[0].severity=='error' and 'uncertain' in out[0].issue
    assert calls==[1]


def test_template_award_is_evidence_for_its_own_title_not_another_projects_features(tmp_path,monkeypatch):
    from jobagent.steps import multi_agent as M
    from jobagent.tools import incremental_documents as I
    monkeypatch.setattr(I.llm,'writer',lambda *a,**kw:SimpleNamespace())
    verdict=I.ProjectVerdict(finding=0,project=1,status='rejected',claim_quote='Hackathon Winner',
        source_quote='Hackathon Winner',evidence_source='owner_template',reason='The owner template explicitly supplies the award.')
    monkeypatch.setattr(I,'invoke',lambda *a,**kw:I.ProjectVerdicts(verdicts=[verdict]))
    payload={'displayed':[{'repo':'a','title':'Hackathon Winner','text':'Built a camera driver.','stack':['Python']}],
             'own_repo_sources':{'a':{'summary':'Supports cameras.'}},
             'owner_template_titles':{'a':'Hackathon Winner'},'owner_claims_policy':'Keep the owner titles.'}
    ctx={'adjudicator_model':'judge','cache':ArtifactCache(tmp_path),'log':lambda _:None}
    errors=[I.ProjectFinding(project=1,severity='error',issue='README does not establish Winner status')]
    assert I.adjudicate_projects(ctx,payload,errors)==[]
    verdict.claim_quote='Built a camera driver.'
    with pytest.raises(ValueError,match='implementation features'):I.adjudicate_projects(ctx,payload,errors)
    verdict.claim_quote='Hackathon Winner';payload['owner_template_titles']={'b':'Hackathon Winner'}
    with pytest.raises(ValueError,match='unbound source_quote'):I.adjudicate_projects(ctx,payload,errors)


def test_adjudication_cannot_move_a_finding_even_when_both_quotes_match_the_other_project(tmp_path,monkeypatch):
    from jobagent.tools import incremental_documents as I
    monkeypatch.setattr(I.llm,'writer',lambda *a,**kw:SimpleNamespace())
    monkeypatch.setattr(I,'invoke',lambda *a,**kw:I.ProjectVerdicts(verdicts=[I.ProjectVerdict(
        finding=0,project=2,status='rejected',claim_quote='Built a robot.',source_quote='Supports robots.',
        reason='Both quotes are real, but belong to the other project.')]))
    payload={'displayed':[{'repo':'a','text':'Built a camera driver.'},{'repo':'b','text':'Built a robot.'}],
             'own_repo_sources':{'a':{'summary':'Path planning only.'},'b':{'summary':'Supports robots.'}},
             'owner_claims_policy':'Owner policy'}
    with pytest.raises(ValueError,match='cannot change'):
        I.adjudicate_projects({'adjudicator_model':'judge','cache':ArtifactCache(tmp_path),'log':lambda _:None},payload,
            [I.ProjectFinding(project=1,severity='error',issue='Camera claim unsupported in project a')])


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
    monkeypatch.setattr(I.llm,'writer',lambda *a,**kw:SimpleNamespace())
    monkeypatch.setattr(I,'invoke',lambda *a,**kw:L.AttributionReview(findings=[]))
    assert L.check(letter,bank,None,'m',lambda _:None)==[]
    data['paragraphs'][0]['sentences'][0]['source_id']='invented'
    with pytest.raises(ValidationError):schema.model_validate(data)


def test_attribution_uses_the_same_dash_normalization_as_letter_rendering(monkeypatch):
    from jobagent.tools import incremental_documents as I
    monkeypatch.setattr(I.llm,'writer',lambda *a,**kw:SimpleNamespace())
    monkeypatch.setattr(I,'invoke',lambda *a,**kw:L.AttributionReview(findings=[]))
    paragraph=Paragraph(text='I built tooling, improving speed by 30%.',evidence=[],bindings=[
        L.Binding(claim='I built tooling — improving speed by 30%.',source_id='a',quote='Improved speed by 30%.')])
    assert L.check(Letter.model_construct(paragraphs=[paragraph]),
                   {'a':{'entity':'Alpha','text':'Improved speed by 30%.'}},None,'m',lambda _:None)==[]


@pytest.mark.parametrize('status',['supported','unsupported','uncertain'])
def test_attribution_verdicts_distinguish_positive_observations_from_errors(monkeypatch,status):
    from jobagent.tools import incremental_documents as I
    monkeypatch.setattr(I.llm,'writer',lambda *a,**kw:SimpleNamespace())
    monkeypatch.setattr(I,'invoke',lambda *a,**kw:L.AttributionReview(findings=[L.AttributionFinding(
        paragraph=1,claim='Built pipelines.',status=status,reason='The source agrees.' if status=='supported' else 'Mismatch or uncertainty.')]))
    paragraph=Paragraph(text='Built pipelines.',evidence=[],bindings=[L.Binding(
        claim='Built pipelines.',source_id='a',quote='Built pipelines, increasing test coverage by 40%.')])
    bank={'a':{'entity':'Alpha','text':'Built pipelines, increasing test coverage by 40%.','kind':'employment'}}
    # A letter may omit a source metric; it cannot add or change one.
    issues=L.check(Letter.model_construct(paragraphs=[paragraph]),bank,None,'m',lambda _:None)
    assert bool(issues)==(status!='supported')


def test_attribution_rejects_an_unbound_reviewer_claim_and_legacy_observation_payload(monkeypatch):
    from pydantic import ValidationError
    from jobagent.tools import incremental_documents as I
    with pytest.raises(ValidationError):L.AttributionReview.model_validate({'issues':['All claims are supported.']})
    monkeypatch.setattr(I.llm,'writer',lambda *a,**kw:SimpleNamespace())
    monkeypatch.setattr(I,'invoke',lambda *a,**kw:L.AttributionReview(findings=[L.AttributionFinding(
        paragraph=1,claim='Invented unseen claim.',status='unsupported',reason='Not actually in this letter.')]))
    paragraph=Paragraph(text='Thank you for considering my application.',evidence=[],bindings=[])
    with pytest.raises(ValueError,match='exact quote'):
        L.check(Letter.model_construct(paragraphs=[paragraph]),{},None,'m',lambda _:None)


def test_incremental_letter_closing_cannot_supply_a_name_or_placeholder():
    from pydantic import ValidationError
    from jobagent.tools.cover_letter import sourced_letter_schema
    schema=sourced_letter_schema({})
    packet={'salutation':'Dear Hiring Team,','closing':'Sincerely','paragraphs':[
        {'sentences':[{'text':'Thank you.','source_id':''}]} for _ in range(4)]}
    assert schema.model_validate(packet).closing=='Sincerely'
    packet['closing']='Sincerely, [Your Name]'
    with pytest.raises(ValidationError):schema.model_validate(packet)


@pytest.mark.parametrize('raw',['Built routing—reduced latency by 20%.',
    'Built pipelines, cutting failed runs 20%.','Connected storage -> realtime exports.',
    'Predicted temperature with R² = 0.984.'])
def test_final_project_evidence_survives_rendered_punctuation_but_excludes_an_unused_variant(tmp_path,raw):
    from jobagent.tools import tailor as T
    (tmp_path/'projects.json').write_text(json.dumps({'kept':2,'ranked':[
        {'title':'Router','paragraph':raw},
        {'title':'Absent','paragraph':'Built a completely different system.'}]}))
    # Exercise real escaping and parsing with the current supported template.
    from jobagent.config import get_settings
    tex=(get_settings().data_dir/'templates/resume.tex').read_text()
    tex=tex.replace('\\end{document}','\\section{Projects}\n'+T.to_latex(raw)+'\n\\end{document}')
    bank=L.sources(tex,tmp_path)
    assert bank['project:0']['text']==raw
    assert 'project:1' not in bank


def test_target_company_and_posting_title_are_not_confused_with_candidate_employer_attribution(monkeypatch):
    from jobagent.tools import incremental_documents as I
    monkeypatch.setattr(I.llm,'writer',lambda *a,**kw:SimpleNamespace())
    monkeypatch.setattr(I,'invoke',lambda *a,**kw:L.AttributionReview(findings=[]))
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
    monkeypatch.setattr(I.llm,'writer',lambda *a,**kw:SimpleNamespace())
    monkeypatch.setattr(I.llm,'structured_invoke',lambda *a,**kw:L.AttributionReview(findings=[]))
    paragraph=Paragraph(text='Beta launched its platform in 2025.',evidence=[],bindings=[
        L.Binding(claim='Beta launched its platform in 2025.',source_id='company',quote='Beta launched its platform in 2025.')])
    bank={'company':{'entity':'Beta','text':'Beta launched its platform in 2025.','kind':'company'}}
    assert L.check(Letter.model_construct(paragraphs=[paragraph]),bank,ArtifactCache(tmp_path),'m',lambda _:None)==[]
