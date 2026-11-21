"""Code-review regressions for new metric-module integration; authored after reading committed 7f7c243."""
import pytest
from jobagent.tools import cover_letter as C, job_context as J
from test_metric_direction import letter, JD

NEGATIVE_SOURCE='Jane Doe. In Python instrumentation tests, API response time increased by 20%.'

@pytest.mark.parametrize('text',['I look forward to cutting API response time using Python.','I am eager to bring my Python work to cut API response time.'])
def test_future_goal_is_not_a_claim_that_reverses_past_history(text):
    found=C.consistency(letter(text),NEGATIVE_SOURCE,JD,'')
    assert not any('metric-direction' in x for x in found),found

def test_company_context_fact_is_not_a_candidate_achievement():
    text='Your Python platform saw API response time increase by 20% during expansion.'
    source='Jane Doe. Built Python APIs, reducing response time by 10%.'
    found=C.consistency(letter(text),source,JD,text)
    assert not any('metric-direction' in x for x in found),found

def test_opposite_past_claim_is_still_blocked():
    found=C.consistency(letter('I cut API response time by 20% using Python.'),NEGATIVE_SOURCE,JD,'')
    assert any('metric-direction' in x for x in found),found

@pytest.mark.parametrize('stage',[None,'experience','letter','letter-attribution','review:experience','adjudicate:experience'])
def test_metric_helper_only_change_invalidates_cached_acceptance(tmp_path,monkeypatch,stage):
    (tmp_path/'tools').mkdir()
    monkeypatch.setattr(J,'__file__',str(tmp_path/'tools/job_context.py'))
    helper=tmp_path/'tools/metric_direction.py'
    helper.write_text('POLICY = 1')
    before=J.model_version(stage)
    helper.write_text('POLICY = 2')
    assert J.model_version(stage)!=before


@pytest.mark.parametrize('original',[True,False])
def test_adjudicator_machine_facts_respect_the_same_original_exemption(tmp_path,monkeypatch,original):
    import json
    from test_generation_reliability import review_world
    from jobagent.steps import multi_agent as M
    from jobagent.tools import document_review as R,tailor as T
    ctx,value=review_world(tmp_path,monkeypatch)
    text='Built Python services, increasing API response time by 20%.'
    ctx['r'].roles[0].bullets=[text]
    value.roles[0].bullets[0]=T.FillBullet(text=text,original=0 if original else None,estimated=[] if original else ['20%'])
    seen=[]
    def decide(model,schema,messages,**kwargs):
        seen.append(json.loads(messages[-1].content)['code_verified']['metric_direction'])
        return schema.model_validate({'verdicts':[{'finding':0,'status':'rejected','claim_id':'experience','source_id':'role:0','reason':'Read the role source.'}]})
    monkeypatch.setattr(R.llm,'structured_invoke',decide)
    finding=M.Finding(agent='experience',severity='error',issue='Review the metric direction in the first bullet.')
    R.adjudicate(ctx,value,[finding])
    assert bool(seen[0]) is (not original)


def test_a_future_clause_does_not_waive_an_explicit_past_bad_claim():
    text='I built Python services, increasing API response time by 20%, and I will improve their architecture.'
    found=C.consistency(letter(text),NEGATIVE_SOURCE,JD,'')
    assert any('metric-direction' in x for x in found),found
