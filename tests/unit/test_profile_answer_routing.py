"""Observed employer/availability variants use owner facts without model or HITL."""
import pytest
from jobagent.filling.facts import Fact,compile_facts, declaration_notes_only
from jobagent.filling.planned import PlannedFiller
from jobagent.filling.routing import family
from jobagent.tools.forms import Field


def forbidden(*args, **kwargs):
    pytest.fail('A saved literal/rule must not require assistance')


def test_confirmed_short_employer_name_is_an_answer_not_an_unknown_condition():
    notes='Owner-confirmed current employer display name; use this shorter name.'
    facts=compile_facts({'work':[{'question':'Current company','answer':'Logos Robotics Lab','instructions':notes}]},'')
    filler=PlannedFiller(facts,forbidden,model=object());filler.invoke=forbidden
    decision=filler.plan([Field(1,'text','Current company ✱',required=True)])[1]
    assert decision.value=='Logos Robotics Lab' and decision.fact_id==facts[0].id
    assert facts[0].instructions==notes
    assert filler.questions==filler.model_calls==0


@pytest.mark.parametrize('label',[
    'When are you looking to start a position?',
    'When would you like to start your new role?',
    'When are you available to start a new job?',
])
def test_availability_variants_apply_the_saved_weekday_rule(label):
    facts=compile_facts({'dates':[{'question':'Earliest start date','answer':'',
        'instructions':"The coming Monday's date; in a list, 'immediately'."}]},'',reference_date='2026-11-26')
    filler=PlannedFiller(facts,forbidden,model=object());filler.invoke=forbidden
    assert filler.plan([Field(1,'text',label)])[1].value=='2026-11-30'
    assert filler.questions==filler.model_calls==0


@pytest.mark.parametrize('notes',[
    'Owner-confirmed employer; use this shorter name only if the company is in the JD.',
    'Owner confirmed employer. Use another company instead.',
    'Owner-confirmed employer use another company.',
    'Owner-confirmed employer; use this shorter name unless instructed otherwise.',
    'Owner-confirmed employer; leave this blank.',
])
def test_conditions_and_output_changes_are_not_discarded_as_confirmation_notes(notes):
    assert not declaration_notes_only(notes)


@pytest.mark.parametrize('label',[
    'When did you start your previous position?',
    'When are you planning to graduate?',
    'When would you like to start your graduate studies?',
])
def test_availability_does_not_answer_employment_history_or_school_dates(label):
    assert family(label)!='start_date'


@pytest.mark.parametrize('label',[
    'Current Title', 'Current job title', 'Your current position title',
    'What is your current (or most recent) title? *',
    'Please provide your current title', 'Most recent job title',
])
def test_current_title_questions_select_the_role_not_the_company(label):
    # The current job deliberately is not the first source record.
    facts=compile_facts({'work':[
        {'question':'Current company','answer':'Current Lab'},
        {'question':'Work history','answer':[
            {'company':'Prior Company','title':'Prior Engineer','end':'May 2025'},
            {'company':'Current Lab','title':'Robotics Researcher','end':'Present','current':True}]}]},'')
    filler=PlannedFiller(facts,forbidden,model=object());filler.invoke=forbidden
    decision=filler.plan([Field(1,'text',label,required=True)])[1]
    assert family(label)=='current_title'
    assert decision.value=='Robotics Researcher' and decision.fact_id.endswith('/1/title')
    assert filler.questions==filler.model_calls==0


def test_most_recent_title_uses_evidenced_dates_when_no_job_is_current():
    facts=compile_facts({'work':[{'question':'Work history','answer':[
        {'company':'Earlier','title':'Earlier Engineer','end':'May 2026'},
        {'company':'Latest','title':'Latest Engineer','end':'August 2026'}]}]},'')
    filler=PlannedFiller(facts,forbidden,model=object());filler.invoke=forbidden
    assert filler.plan([Field(1,'text','What is your current or most recent title?')])[1].value=='Latest Engineer'


@pytest.mark.parametrize('rows',[
    [],
    [{'company':'Only Company','end':'Present','current':True}],
    [{'title':'One','current':True},{'title':'Two','current':True}],
    [{'title':'One','end':'2026'},{'title':'Two','end':'2026'}],
])
def test_missing_or_ambiguous_title_cannot_be_filled_with_a_company(rows):
    facts=compile_facts({'work':[{'question':'Current company','answer':'Company Is Not A Title'},
                               {'question':'Work history','answer':rows}]},'')
    filler=PlannedFiller(facts,forbidden,model=object());filler.invoke=forbidden
    decision=filler.plan([Field(1,'text','What is your current or most recent title?')],allow_human=False)[1]
    assert decision.value is None


def test_an_employment_row_keeps_its_own_title_despite_another_current_role():
    facts=compile_facts({'work':[{'question':'Work history','answer':[
        {'title':'Prior Engineer','end':'May 2025'},
        {'title':'Current Engineer','current':True}]}]},'')
    filler=PlannedFiller(facts,forbidden,model=object());filler.invoke=forbidden
    decision=filler.plan([Field(1,'text','Job Title',section='Work Experience 1')])[1]
    assert decision.value=='Prior Engineer' and decision.fact_id.endswith('/0/title')


def test_current_title_uses_the_normal_resume_projection_pipeline():
    from jobagent.tools import applicant
    data={'experience':[{'question':'Work history','answer':'',
                         'instructions':'One entry per role, in order; current only for the current role.'},
                        {'question':'Current company','answer':''}]}
    tex=r'''\section{Experience}
        \resumeSubheading{Prior Engineer}{May 2025 -- May 2026}{Earlier Company}{Phoenix, Arizona}
        \resumeSubheading{Robotics Researcher}{January 2026 -- Present}{Current Lab}{Tempe, Arizona}
        \section{Technical Skills}'''
    facts=compile_facts(applicant.with_resume(data,applicant.resume_experience(tex)),'')
    filler=PlannedFiller(facts,forbidden,model=object());filler.invoke=forbidden
    decision=filler.plan([Field(1,'text','What is your current (or most recent) title? *')])[1]
    assert decision.value=='Robotics Researcher' and decision.fact_id.endswith('/1/title')
    assert next(x for x in facts if x.id==decision.fact_id).authority=='resume'


@pytest.mark.parametrize('authority',['document','lookup','portal'])
def test_a_posting_or_web_title_does_not_establish_the_applicants_current_title(authority):
    facts=[Fact('foreign-title',['Job Title'],'Posting Role','outside',section='Work Experience 1',authority=authority),
           Fact('foreign-current',['Current'],'True','outside',section='Work Experience 1',authority=authority)]
    filler=PlannedFiller(facts,forbidden,model=object());filler.invoke=forbidden
    assert filler.plan([Field(1,'text','Current Title')],allow_human=False)[1].value is None
