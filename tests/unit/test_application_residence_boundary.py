"""A prepared postal address cannot establish the candidate's actual residence."""
import copy
import pytest
from jobagent.tools import application_address as A, applicant, forms
from jobagent.steps import apply
from jobagent.filling.facts import compile_facts
from jobagent.filling.routing import personal_facts
from jobagent.filling.planned import PlannedFiller
from test_application_address import prepared_address_lookups


def context(location='', instructions='Same as Current Address.'):
    return {'_jd':{'company':'Example','locations':['Chicago, Illinois']},'_applicant':{'address':[
        {'question':'Current address','answer':{'line1':'1 Home Road','city':'Tempe','state':'Arizona','postal_code':'85281','country':'United States'}},
        {'question':'Current location (city)','seen':['Current location','Location (City)'],'answer':location,'instructions':instructions}]}}


def entry(ctx,question):
    return next(e for _,e in applicant.entries(ctx['_applicant']) if e['question']==question)


def test_postal_lookup_preserves_and_routes_the_original_residence(tmp_path,prepared_address_lookups):
    ctx=context();original=copy.deepcopy(ctx['_applicant']);A.prepare(ctx,tmp_path,None,log=lambda _:None)
    assert entry(ctx,'Current address')['answer']['city']=='Chicago'
    assert entry(ctx,'Current location (city)')['answer']=='Tempe, Arizona'
    assert ctx['_personal_address']==original['address'][0]['answer']
    facts=compile_facts(ctx['_applicant'],'')+personal_facts(ctx)
    filler=PlannedFiller(facts,lambda _:pytest.fail('Residence is already supplied'))
    assert filler.plan([forms.Field(1,'text','Current location')])[1].value=='Tempe, Arizona'
    answers={1:apply.Answer(id=1,value='Tempe, Arizona',source='applicant')}
    apply.enforce_selected_address([forms.Field(1,'text','Current location')],answers,ctx)
    assert answers[1].value=='Tempe, Arizona'
    A.prepare(ctx,tmp_path,None,log=lambda _:None)
    assert entry(ctx,'Current location (city)')['answer']=='Tempe, Arizona'


def test_explicit_owner_location_is_never_replaced_by_the_job_city(tmp_path,prepared_address_lookups):
    ctx=context('Mesa, Arizona','');A.prepare(ctx,tmp_path,None,log=lambda _:None)
    assert entry(ctx,'Current location (city)')['answer']=='Mesa, Arizona'


def test_unknown_owner_residence_does_not_become_the_generated_address(tmp_path,prepared_address_lookups):
    ctx=context();ctx['_applicant']['address'][0]['answer']={}
    A.prepare(ctx,tmp_path,None,log=lambda _:None)
    assert not entry(ctx,'Current location (city)')['answer']


def test_unknown_actual_state_cannot_be_proved_by_the_job_postal_state(tmp_path,prepared_address_lookups):
    from jobagent.filling.routing import resolve_routed
    ctx=context();ctx['_applicant']['address'][0]['answer']={}
    A.prepare(ctx,tmp_path,None,log=lambda _:None)
    facts=compile_facts(ctx['_applicant'],'')+personal_facts(ctx)
    question=forms.Field(2,'radio','Are you a resident of Illinois?',options=['Yes','No'])
    assert resolve_routed(question,facts).value is None


def test_actual_country_and_state_use_original_owner_evidence(tmp_path,prepared_address_lookups):
    from jobagent.filling.routing import resolve_routed
    ctx=context();A.prepare(ctx,tmp_path,None,log=lambda _:None)
    facts=compile_facts(ctx['_applicant'],'')+personal_facts(ctx)
    assert resolve_routed(forms.Field(2,'radio','Are you a resident of Illinois?',options=['Yes','No']),facts).value=='No'
    country=forms.Field(3,'select','Country of residence',options=['United States','India'])
    decision=resolve_routed(country,facts)
    assert decision.value=='United States' and decision.fact_id=='owner.residence.country'
