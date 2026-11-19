"""Real questionnaire captions must retain the question's purpose and scope."""
import pytest

from jobagent.filling.facts import Decision,Fact,compile_facts
from jobagent.filling.planned import PlannedFiller
from jobagent.filling.routing import answer_sheet_facts,family
from jobagent.tools.forms import Field

AGE="1) * As of today's date, are you 18 years of age or older? *"
RESTRICTIONS=('4) * To the best of your knowledge and belief, are you aware of a contract or agreement '
 'with your current employer (or other company), such as a non-competition, non-disclosure, or '
 'non-solicitation agreement, that impact or interfere with your ability to work for the Company '
 'or communicate with former colleagues about working at the Company *')
COUNTRIES=('9a) * If your response to question 9 is “No”, please specify the country or countries '
 'where you have citizenship or lawful permanent resident status. *')
VISA='11a) * Are you now, or have you in the past, been on a J-1 or J-2 exchange visitor’s visa? *'


def no_ask(questions):raise AssertionError(questions)


def test_bare_contact_location_uses_the_prepared_city_without_a_model_or_question():
    facts=compile_facts({'contact':[
        {'question':'Current location (city)','answer':'Sunnyvale, California'},
        {'question':'Current address','answer':{'city':'Sunnyvale','state':'California','country':'United States'}},
    ]},'')
    f=Field(0,'combobox','Location',required=True,section='Autofill from resume',options_state='partial')
    filler=PlannedFiller(facts,no_ask,model=object())
    filler.invoke=lambda *a,**kw:pytest.fail('A prepared city needs no model lookup')
    decision=filler.plan([f])[0]
    assert decision.value=='Sunnyvale, California' and 'Current location (city)' in decision.fact_id


def test_bare_location_preserves_country_office_and_employment_scope():
    from jobagent.filling.routing import route,candidates
    facts=compile_facts({'contact':[
        {'question':'Current location (city)','answer':'Sunnyvale, California'},
        {'question':'Current address','answer':{'country':'United States'}},
    ]},'')
    country=Field(0,'select','Location',options=['Canada','India','Japan','United States'])
    assert route(country,facts).family=='country'
    office=Field(0,'combobox','Location',help='Select the office location for this position')
    assert route(office,facts).family=='office_preferences'
    employer=Field(0,'text','Location',section='Work Experience 1',help='Office location')
    assert route(employer,facts).family=='location'
    assert not any('Current location (city)' in f.id for f in candidates(employer,facts))
    assert PlannedFiller(facts,no_ask).plan([employer],allow_human=False)[0].value is None


def field(label):
    return Field(0,'textarea' if label==COUNTRIES else 'combobox',label,required=True,
                 options=[] if label==COUNTRIES else ['Yes','No'],options_state='complete')


def plan(label,facts,**kwargs):
    filler=PlannedFiller(facts,no_ask,**kwargs)
    filler.invoke=lambda *a,**kw:pytest.fail('Unproven personal predicates must not be model guesses')
    answer=filler.plan([field(label)],allow_human=False)[0]
    assert filler.model_calls==filler.questions==0
    return answer


@pytest.mark.parametrize('label,kind',[(AGE,'age_threshold'),(RESTRICTIONS,'work_restrictions'),
    (COUNTRIES,'citizenship_countries'),(VISA,'visa_history'),
    ('Current employer','current_employer'),('Who is your current employer?','current_employer'),
    ("Today's date",'signature_date'),('Date signed','signature_date'),
    ('Do you need sponsorship? Your current employer and permanent resident status may matter.','sponsorship')])
def test_question_purpose_takes_priority_over_incidental_words(label,kind):
    assert family(label)==kind


def test_age_uses_owner_threshold_instead_of_the_clock():
    facts=compile_facts({'auth':[{'question':'Are you age 18 or over?','answer':True}]},'',reference_date='2026-11-17')
    result=plan(AGE,facts,model=object())
    assert result.value=='Yes' and result.fact_id.endswith('/Are you age 18 or over?/answer')


@pytest.mark.parametrize('different',[
    'Are you age 21 or over?','Are you under the age of 18?',
    'Are you over the age of 18?','Are you not age 18 or over?',
    'Are you age 18 or over and authorized to work?',
])
def test_other_age_thresholds_comparisons_or_combined_claims_cannot_be_borrowed(different):
    assert plan(AGE,[Fact('age',[different],'Yes','applicant')],model=object()).value is None


@pytest.mark.parametrize('label',[RESTRICTIONS,COUNTRIES,VISA])
def test_incidental_employer_address_sponsorship_and_narrower_facts_cannot_answer(label):
    facts=compile_facts({'profile':[
        {'question':'Current employer','answer':'Example Employer'},
        {'question':'Current address','answer':{'country':'United States'}},
        {'question':'Visa status / citizenship','answer':{'visa_status':'F-1 OPT','citizenship':'India'}},
        {'question':'Bound by a non-compete agreement?','answer':False},
        {'question':'Do you require sponsorship?','answer':True},
        {'question':'Have you ever held a J-1 visa?','answer':False},
    ]},'')
    assert plan(label,facts,model=object()).value is None


@pytest.mark.parametrize('label,value',[(RESTRICTIONS,'No'),(COUNTRIES,'India'),(VISA,'No')])
def test_current_application_owner_answer_resolves_the_exact_predicate(label,value):
    facts=answer_sheet_facts('| # | Question | Answer |\n| 1 | '+label+' | '+value+' |')
    result=plan(label,facts,model=object())
    assert result.value==value and result.source=='applicant/application/answers.md'


@pytest.mark.parametrize('label',[AGE,RESTRICTIONS,COUNTRIES,VISA])
def test_legacy_source_resolver_cannot_bypass_personal_purpose_binding(label):
    fact=Fact('unrelated',['Do you require sponsorship?'],'Yes','applicant')
    result=plan(label,[fact],model=object(),source_resolver=lambda *_:{0:Decision('Yes',fact.source,fact.id)})
    assert result.value is None


@pytest.mark.parametrize('label',['Country of citizenship','Citizenship country','Nationality'])
def test_structured_citizenship_component_is_distinct_from_residential_country(label):
    facts=compile_facts({'profile':[
        {'question':'Current address','answer':{'country':'United States'}},
        {'question':'Visa status / citizenship','answer':{'visa_status':'F-1 OPT','citizenship':'India'}},
    ]},'')
    f=Field(0,'text',label,required=True)
    result=PlannedFiller(facts,no_ask).plan([f],allow_human=False)[0]
    assert result.value=='India' and result.fact_id.endswith('/citizenship')


@pytest.mark.parametrize('different',[
    'Have you ever held a J-1 and J-2 visa?',
    'Have you applied for a J-1 or J-2 visa?',
    'Have you in the past five years held a J-1 or J-2 visa?',
])
def test_visa_types_actions_and_time_windows_are_not_interchangeable(different):
    assert plan(VISA,[Fact('visa',[different],'No','applicant')],model=object()).value is None
