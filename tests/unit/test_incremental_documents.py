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
    monkeypatch.setattr(I.job_context,'model_version',lambda:'v1')
    def call(model='m',prompt='a'):
        return invoke(ArtifactCache(tmp_path),'experience',model,object(),Output,[HumanMessage(prompt)])
    call();call()
    assert len(calls)==1
    call(prompt='b');call(model='other')
    monkeypatch.setattr(I.job_context,'model_version',lambda:'v2')
    call()
    assert len(calls)==4


def test_failure_and_corruption_never_become_cache_hits(tmp_path,monkeypatch):
    from jobagent.tools import incremental_documents as I
    def fail(*a,**kw):raise RuntimeError('provider down')
    monkeypatch.setattr(I.llm,'structured_invoke',fail)
    with pytest.raises(RuntimeError):invoke(ArtifactCache(tmp_path),'x','m',None,Output,[HumanMessage('q')])
    assert not list(tmp_path.glob('*.json'))
    cache=ArtifactCache(tmp_path);cache.put('x',{}, {'text':'good'})
    path=next(tmp_path.glob('*.json'));doc=json.loads(path.read_text());doc['result']['text']='corrupt';path.write_text(json.dumps(doc))
    assert cache.get('x',{}) is None


def test_changed_skills_rechecks_skills_and_summary_only(tmp_path,monkeypatch):
    from jobagent.steps import multi_agent as M
    from jobagent.tools import incremental_documents as I
    monkeypatch.setattr(M.T,'facts_block',lambda:'owner claims')
    calls=[]
    def generate(model,schema,msgs,**kw):
        calls.append(msgs[0].content.split('Review ONLY ')[1].split(';')[0]);return M.Review(findings=[])
    monkeypatch.setattr(I.llm,'structured_invoke',generate)
    monkeypatch.setattr(I.llm,'writer',lambda *a,**kw:object())
    f=M.T.Filled.model_construct(summary='A summary.',roles=[],skills=[M.T.SkillLine(category='Languages',terms=['Python'])])
    ctx={'job_dir':tmp_path,'r':SimpleNamespace(roles=[],skills=[]),'jd':SimpleNamespace(model_dump=lambda **kw:{}),
         'models':{'router':'m'},'cache':ArtifactCache(tmp_path/'cache'),'log':lambda m:None}
    review(ctx,f);review(ctx,f)
    assert len(calls)==4
    f.skills[0].terms=['Rust'];review(ctx,f)
    assert sorted(calls[4:])==['skills','summary']


def panel_world(tmp_path,monkeypatch,scores):
    from jobagent.steps import multi_agent as M
    (tmp_path/'resume.pdf').write_bytes(b'valid-pdf-fixture')
    seen=[]
    def evaluate(job,jd,text,rnd,sites,log):
        seen.append((rnd,sites))
        return {'scores':scores,'missing':{},'advice':[],'completed':list(scores)}
    monkeypatch.setattr(M.A,'evaluate',evaluate)
    ctx={'job_dir':tmp_path,'jd':None,'jd_text':'Exact JD','log':lambda _:None,'sites':(),'incremental':True}
    state={'ctx':ctx,'best':{'scores':{},'f':None,'hard':[],'layout':0}}
    return ctx,state,seen


def test_final_panel_once_without_refinement_and_missing_provider_stays_incomplete(tmp_path,monkeypatch):
    ctx,state,seen=panel_world(tmp_path,monkeypatch,{'hireflow':80})
    best,history=final_panel(ctx,state,('hireflow','atsfriendly'))
    assert seen==[(0,('hireflow','atsfriendly'))]
    assert history[0]['panel']['complete'] is False and history[0]['panel']['average'] is None


def test_ats_detects_mutation_of_the_scored_pdf(tmp_path,monkeypatch):
    from jobagent.steps import multi_agent as M
    ctx,state,seen=panel_world(tmp_path,monkeypatch,{'hireflow':80})
    def mutate(*a):
        (tmp_path/'resume.pdf').write_bytes(b'other')
        return {'scores':{'hireflow':80}}
    monkeypatch.setattr(M.A,'evaluate',mutate)
    with pytest.raises(RuntimeError,match='changed'):final_panel(ctx,state,('hireflow',))


def test_changed_jd_is_rejected_before_an_upload(tmp_path,monkeypatch):
    ctx,state,seen=panel_world(tmp_path,monkeypatch,{'hireflow':80})
    (tmp_path/'jd.txt').write_text('Changed posting')
    with pytest.raises(RuntimeError,match='before'):final_panel(ctx,state,('hireflow',))
    assert not seen


def test_letter_cannot_transfer_metric_to_another_employer(monkeypatch):
    paragraph=Paragraph(text='At Beta I cut latency by 30%.',evidence=[],bindings=[
        L.Binding(claim='At Beta I cut latency by 30%.',source_id='a',quote='Cut latency by 30%.')])
    letter=Letter.model_construct(paragraphs=[paragraph])
    bank={'a':{'entity':'Alpha','text':'Cut latency by 30%.'},'b':{'entity':'Beta','text':'Built APIs.'}}
    issues=L.check(letter,bank,None,'m',lambda _:None)
    assert any('different employer' in i for i in issues)


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
