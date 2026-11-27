"""The retained original filler must enforce the same explicit owner sources."""
from jobagent.steps import apply
from jobagent.tools.forms import Field


def context():
    return {'_applicant':{
        'contact':[{'question':'Current location (city)','answer':'Tempe, Arizona, United States'}],
        'demographics':[{'question':'Race / ethnicity','answer':'Asian'}],
        'education':[{'question':'Degrees','answer':[
            {'school':'Graduate University','degree':'Masters','field':['Artificial Intelligence','Other']},
            {'school':'Undergraduate University','degree':'Bachelors','field':'Computer Science'}]}]},
        '_jd':{'company':'Fixture'},'_jd_text':'','TODAY':'2026-11-27',
        'ANSWERS_FOR_THIS_APPLICATION':'| 1 | If yes, what versions? | .NET 8 |'}


def enforce(field,value,ctx=None,source='applicant'):
    answers={field.id:apply.Answer(id=field.id,value=value,source=source,ask_human=True,flag='planned value')}
    apply.enforce([field],answers,ctx or context(),lambda _:None)
    return answers[field.id]


def test_original_location_does_not_accept_the_job_city_as_actual_residence():
    answer=enforce(Field(1,'text','Location'),'Sunnyvale, California, United States')
    assert answer.value=='Tempe, Arizona, United States' and not answer.ask_human


def test_original_race_honors_the_same_privacy_fallback():
    answer=enforce(Field(1,'radio','Race',options=['Asian (Not Hispanic or Latino)','Decline to self-identify']), 'Asian')
    assert answer.value=='Decline to self-identify' and not answer.ask_human


def test_original_keeps_an_offered_plain_asian_choice():
    answer=enforce(Field(1,'radio','Race',options=['Asian','Decline to self-identify']),'Asian')
    assert answer.value=='Asian'


def test_original_major_is_bound_to_its_degree_instead_of_a_neighbor():
    answer=enforce(Field(1,'combobox','Discipline',section='Education 1',options=['Computer Science','Other']),'Computer Science')
    assert answer.value=='Other' and not answer.ask_human


def test_original_reuses_the_exact_application_answer_after_model_verification():
    answer=enforce(Field(1,'text','If yes, what versions?'),'.NET 8')
    assert answer.value=='.NET 8' and not answer.ask_human and not answer.flag


def test_a_resolved_owner_rule_cancels_the_models_obsolete_lookup():
    ctx=context()
    ctx['_applicant']['dates']=[{'question':'Earliest start date','answer':'',
        'instructions':"The coming Monday's date; in a list, 'immediately'."}]
    field=Field(1,'text','When are you looking to start a position?')
    answers={1:apply.Answer(id=1,value='2027-01-01',source='applicant',lookup='web: earliest start date')}
    apply.enforce([field],answers,ctx,lambda _:None)
    assert answers[1].value=='2026-11-30' and not answers[1].lookup


def test_missing_current_role_evidence_asks_instead_of_searching_for_a_company_title():
    field=Field(1,'text','Current Title')
    answers={1:apply.Answer(id=1,value='Company Is Not A Title',source='applicant',lookup='web: company job title')}
    apply.enforce([field],answers,context(),lambda _:None)
    assert answers[1].value=='' and answers[1].ask_human and not answers[1].lookup


def test_a_current_human_literal_is_not_overridden():
    answer=enforce(Field(1,'text','Location'),'Owner-confirmed temporary location',source='human')
    assert answer.value=='Owner-confirmed temporary location'
