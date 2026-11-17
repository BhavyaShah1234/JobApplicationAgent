"""An unrelated current-posting answer cannot establish a named identifier."""
from types import SimpleNamespace
import pytest
from jobagent.filling.facts import Decision,Fact
from jobagent.filling.planned import PlannedFiller,Mappings,Mapping,Supported
from jobagent.filling.routing import route,resolve_routed,binding_valid
from jobagent.tools.forms import Field


CONSENT='I hereby consent that SAP processes my data as part of the SAP Sanction Party Screening Program.'


@pytest.mark.parametrize('label',['SAP Program Code','Employee ID','Candidate identification number','Referral code'])
def test_named_identifiers_never_borrow_a_current_posting_consent(label):
    field=Field(id=0,kind='text',label=label)
    wrong=Fact('application.answer.7',[CONSENT],'Yes','applicant/application/answers.md')
    r=route(field,[wrong])
    assert r.family=='owner_identifier' and r.fact_ids==[]
    assert resolve_routed(field,[wrong]).value is None
    assert not binding_valid(field,[wrong],'Yes',r)


def test_a_source_resolver_cannot_override_the_identifier_boundary():
    f=Field(id=0,kind='text',label='SAP Program Code')
    wrong=Fact('application.answer.7',[CONSENT],'Yes','applicant/application/answers.md')
    calls=[]
    filler=PlannedFiller([wrong],lambda qs:calls.extend(qs) or {},
        source_resolver=lambda *_:{0:Decision('Yes',wrong.source,wrong.id)})
    assert filler.plan([f])[0].value is None
    assert calls[0]['label']=='SAP Program Code'


def test_a_named_owner_identifier_resolves_with_its_exact_source():
    f=Field(id=0,kind='text',label='Please enter your SAP program code')
    right=Fact('owner.code',['SAP Program Code'],'ASU-1234','applicant/identifiers')
    result=resolve_routed(f,[right])
    assert (result.value,result.fact_id)==('ASU-1234','owner.code')


@pytest.mark.parametrize('alias,authority',[
    ('OtherCo Program Code','owner'),('Program Code','owner'),
    ('SAP Employee ID','owner'),('SAP Program Code','resume'),
])
def test_other_companies_identifiers_and_resume_derived_values_do_not_establish_this_code(alias,authority):
    f=Field(id=0,kind='text',label='SAP Program Code')
    wrong=Fact('wrong',[alias],'X123','source',authority=authority)
    assert resolve_routed(f,[wrong]).value is None


def test_even_an_accepting_model_cannot_fill_a_code_from_unrelated_consent():
    class BadModel:
        model_name='fixture'
        def with_structured_output(self,schema,**kwargs):
            return SimpleNamespace(invoke=lambda _:Mappings(mappings=[Mapping(field_id=0,
                fact_id='application.answer.7',value='Yes')]) if schema is Mappings else Supported(supported_ids=[0]))
    wrong=Fact('application.answer.7',[CONSENT],'Yes','applicant/application/answers.md')
    f=Field(id=0,kind='text',label='SAP Program Code')
    assert PlannedFiller([wrong],lambda _: {},BadModel()).plan([f],allow_human=False)[0].value is None
