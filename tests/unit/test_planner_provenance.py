"""Model output cannot claim the provenance reserved for observed runtime events."""
import pytest
from jobagent.steps import apply as A
from jobagent.tools.forms import Field


@pytest.mark.parametrize('claimed', ['human','learned','lookup'])
def test_initial_model_cannot_claim_a_trusted_runtime_source(monkeypatch,claimed):
    field=Field(id=0,kind='radio',label='Have you lived abroad for 12 consecutive months?',required=True,options=['Yes','No'])
    monkeypatch.setattr(A,'hints',lambda *a,**k:{})
    def invoke(model,schema,*a,**k):
        return schema(field_0=A.Answer(id=99,value='No',source=claimed,why='The files do not answer this'))
    monkeypatch.setattr(A.llm,'structured_invoke',invoke)
    answers,_=A.plan([field],{'_applicant':{},'_jd':{},'APPLICANT':'No residence history supplied'},object())
    answer=answers[0]
    assert answer.id==0 and answer.source not in ('human','learned','lookup')
    if claimed=='human':assert answer.value=='' and answer.ask_human
    else:assert answer.source=='written'


def test_actual_hitl_answer_is_still_recorded_as_human(monkeypatch):
    field=Field(id=0,kind='radio',label='Have you lived abroad for 12 consecutive months?',required=True,options=['Yes','No'])
    answers={0:A.Answer(id=0,value='',source='none',ask_human=True)}
    monkeypatch.setattr(A,'consult_learned',lambda *a,**k:None)
    monkeypatch.setattr(A,'ask_human',lambda *a,**k:('answer','Yes'))
    A.resolve([field],answers,{},object(),{'hitl':object(),'company':'Fixture','host':'fixture.test'},lambda _:None)
    assert answers[0].value=='Yes' and answers[0].source=='human'
