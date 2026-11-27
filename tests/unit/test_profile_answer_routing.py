"""Observed employer/availability variants use owner facts without model or HITL."""
import pytest
from jobagent.filling.facts import compile_facts, declaration_notes_only
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
