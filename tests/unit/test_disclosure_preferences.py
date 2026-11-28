"""Explicit privacy preferences choose refusal options, never demographic traits."""
import pytest

from jobagent.filling.facts import compile_facts, resolve
from jobagent.filling.routing import resolve_routed
from jobagent.tools import forms


@pytest.fixture
def facts():
    return compile_facts({
        'work_authorization':[
            {'question':'Are you legally authorized to work in the United States?','answer':True},
            {'question':'Will you now require sponsorship?','answer':True},
            {'question':'Will you in the future require sponsorship for employment visa status?','answer':True,
             'seen':['Do you require visa sponsorship?','Will you now or in the future require sponsorship?']},
            {'question':'Have you previously held H-1B status?','answer':False},
            {'question':'Are you a United States citizen?','answer':False}],
        'self_identification':[
            {'question':'Gender','seen':['Gender Identity'],'answer':'Prefer not to say'},
            {'question':'Transgender?','seen':['Do you identify as transgender?'],'answer':'Prefer not to say'},
            {'question':'Race / ethnicity','seen':['Race','Race/Ethnicity'],'answer':'Prefer not to say'},
            {'question':'Hispanic or Latino?','answer':'Prefer not to say'},
            {'question':'Veteran status','answer':'Prefer not to say'},
            {'question':'Disability status','answer':'Prefer not to say'}]},'')


def decision(facts, label, options=(), kind='select'):
    f=forms.Field(0,kind,label,options=list(options),required=True)
    return resolve_routed(f,facts) or resolve(f,facts)


@pytest.mark.parametrize('label',['Gender','Gender Identity','What is your gender?','Sex',
    'Transgender?','Do you identify as transgender?','Race','Race/Ethnicity',
    'Hispanic or Latino?','Are you Hispanic or Latino?','Veteran status','Protected Veteran Status',
    'Disability status'])
@pytest.mark.parametrize('option',["I don't wish to answer",'Decline to self-identify',
    'I do not want to answer','I prefer not to answer','Prefer not to say',
    'I choose not to disclose','Rather not say',
    'I do not wish to answer (United States of America)'])
def test_disclosure_refusal_uses_the_actual_offered_wording(facts,label,option):
    assert decision(facts,label,['Yes','No','Male','Female','Asian','Other',option]).value==option


@pytest.mark.parametrize('label',['Gender','Race','Hispanic or Latino?','Veteran status','Disability status'])
def test_missing_refusal_choice_never_falls_back_to_a_trait(facts,label):
    assert decision(facts,label,['Yes','No','Male','Female','Asian','Other']).value is None


@pytest.mark.parametrize('label',['Gender','Race','Veteran status','Disability status'])
def test_text_disclosure_questions_use_the_saved_literal(facts,label):
    assert decision(facts,label,kind='text').value=='Prefer not to say'


@pytest.mark.parametrize('label',['Will you now require sponsorship?',
    'Will you in the future require sponsorship for employment visa status?',
    'Do you require visa sponsorship?','Will you now or in the future require sponsorship?'])
def test_current_and_future_sponsorship_answers_are_yes(facts,label):
    assert decision(facts,label,['Yes','No']).value=='Yes'


@pytest.mark.parametrize('label,expected',[
    ('Are you legally authorized to work in the United States?','Yes'),
    ('Have you previously held H-1B status?','No'),
    ('Are you a United States citizen?','No')])
def test_sponsorship_preference_does_not_answer_other_immigration_predicates(facts,label,expected):
    assert decision(facts,label,['Yes','No']).value==expected


def test_declining_disclosure_is_not_no_or_other():
    assert forms.match_option('Prefer not to say',['No','Other'])[0] is None


@pytest.mark.parametrize('option',['No','Other','I do not have a disability',
    'Prefer not to say, but I am a veteran','Decline to self-identify as Asian'])
def test_refusal_is_not_a_substring_or_a_negative_trait(option):
    assert not forms.is_disclosure_refusal(option)
    assert forms.match_option('Prefer not to say',[option])[0] is None
    assert not forms.already_shows('Prefer not to say',option,'select')


def test_multiple_same_purpose_facts_must_all_decline():
    conflicting=compile_facts({'self_identification':[
        {'question':'Gender','answer':'Prefer not to say'},
        {'question':'Transgender?','answer':False}]},'')
    result=resolve_routed(forms.Field(0,'select','What is your gender?',options=['No','Prefer not to say']),conflicting)
    assert result is None or result.value is None


@pytest.mark.parametrize('label,owner,wrong,options,expected',[
    ('Gender','Prefer not to say','Male',['Male','I do not wish to answer'],'I do not wish to answer'),
    ('Veteran status','Prefer not to say','No',['No','Decline to self-identify'],'Decline to self-identify'),
    ('Disability status','Prefer not to say','No',['No','I do not want to answer'],'I do not want to answer'),
    ('Hispanic or Latino?','Prefer not to say','No',['No','Prefer not to say'],'Prefer not to say'),
    ('Do you require visa sponsorship?','Yes','No',['Yes','No'],'Yes')])
def test_original_engine_enforces_explicit_owner_answers(label,owner,wrong,options,expected):
    from jobagent.steps import apply
    field=forms.Field(1,'select',label,options=options,required=True)
    ctx={'_applicant':{'preferences':[{'question':label,'answer':owner}]}}
    answers={1:apply.Answer(id=1,value=wrong,source='applicant')}
    apply.enforce([field],answers,ctx,lambda _:None)
    assert answers[1].value==expected and not answers[1].ask_human
