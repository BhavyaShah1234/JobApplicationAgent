import pytest
from jobagent.steps import apply as A
from jobagent.tools import forms


@pytest.mark.parametrize('prefill,expected', [
    ('parser@example.test', 'owner@example.test'),
    ('owner@example.test', 'owner@example.test'),
    ('owner+jobs@example.test', 'owner@example.test'),
    ('approved@example.test', 'approved@example.test'),
])
def test_routed_email_prefill_requires_an_explicit_owner_value(prefill, expected):
    from jobagent.filling.facts import Fact
    from jobagent.filling.routing import resolve_routed
    fact=Fact('email',['Email'],'owner@example.test','applicant/contact',variants=['approved@example.test'],
              instructions="On portals with an account the form shows the account's email (the agent's Gmail): keep that.")
    field=forms.Field(0,'email','Email',value=prefill,section='Contact information')
    assert resolve_routed(field,[fact]).value == expected


@pytest.mark.parametrize('known,prefill,section,expected',[
 ('casey@example.test','casey@example.test','Contact information',True),
 ('casey@example.test','other@example.test','Contact information',False),
 ('','casey@example.test','Contact information',False),
 ('casey@example.test','casey@example.test','Reference 1',False),
 ('casey@example.test','','Contact information',False),
])
def test_prefilled_email_needs_exact_purpose_bound_owner_evidence(monkeypatch,known,prefill,section,expected):
    data={'contact':[{'question':'Email','seen':['Email Address'],'answer':known,
          'instructions':"On portals with an account the form shows the account's email (the agent's Gmail): keep that."}]}
    field=forms.Field(0,'text','Email address',required=True,value=prefill,section=section)
    answers={0:A.Answer(id=0,value=prefill or known,source='already_filled')}
    ctx={'_applicant':data,'_jd':{},'_jd_text':''}
    monkeypatch.setattr(A,'hints',lambda *a,**k:{})
    calls=[]
    def verify(model,schema,*args,**kwargs):
        calls.append(schema)
        return schema(**{name:A.Check(id=int(name.split('_')[-1]),supported=False,ask_human=True) for name in schema.model_fields})
    monkeypatch.setattr(A.llm,'structured_invoke',verify)
    A.check([field],answers,ctx,object(),lambda _:None)
    if expected:
        assert answers[0].source=='applicant' and answers[0].value==known and not answers[0].ask_human
        assert not calls  # Literal owner evidence needs no speculative second opinion.
    else:
        assert not answers[0].value and answers[0].ask_human


@pytest.mark.parametrize('label,kind,value,section,expected',[
 ('Phone number','tel','+1 202-555-0100','Contact information',True),
 ('Country for phone number','combobox','United States (+1)','Contact information',True),
 ('Country for phone number','combobox','Canada (+1)','Contact information',False),
 ('Preferred first name','text','Casey','Contact information',True),
 ('Preferred first name','text','Casey','Reference 1',False),
])
def test_prefilled_contact_cannot_borrow_another_country_or_person(monkeypatch,label,kind,value,section,expected):
    data={'contact':[{'question':'Phone','answer':{'number':'2025550100','country_code':'+1','country':'United States'}},
                     {'question':'Preferred name','answer':{'first':'Casey','last':'Morgan','full':'Casey Morgan'}}]}
    field=forms.Field(0,kind,label,required=True,value=value,section=section,
        options=['United States (+1)','Canada (+1)'] if kind=='combobox' else [])
    answers={0:A.Answer(id=0,value=value,source='already_filled')}
    monkeypatch.setattr(A,'hints',lambda *a,**k:{})
    calls=[]
    def verify(model,schema,*args,**kwargs):
        calls.append(schema)
        return schema(**{name:A.Check(id=int(name.split('_')[-1]),supported=False,ask_human=True) for name in schema.model_fields})
    monkeypatch.setattr(A.llm,'structured_invoke',verify)
    A.check([field],answers,{'_applicant':data,'_jd':{},'_jd_text':''},object(),lambda _:None)
    if expected:
        assert answers[0].source=='applicant' and answers[0].value==value and not answers[0].ask_human
        assert not calls
    else:assert not answers[0].value and answers[0].ask_human
