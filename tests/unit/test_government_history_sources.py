"""A complete owner denial covers narrower employment history, never other purposes."""
import pytest

from jobagent.filling.facts import Fact,compile_facts
from jobagent.filling.planned import PlannedFiller
from jobagent.tools.forms import Field


DOD='7) Are you a current employee of the US Department of Defense (DOD) or were you an employee of the US Department of Defense (DOD) on or after January 28, 2008?'
GOV='8) Are you a current Federal, State or Local Government employee; including military (other than the DOD) or have you at any time in the past 5 years been an employee of one of these entities?'


def no_ask(questions):raise AssertionError(questions)


@pytest.mark.parametrize('label',[DOD,GOV])
def test_complete_owner_negative_resolves_narrower_history_without_a_model_or_human(label):
    facts=compile_facts({'history':[{'question':'Current or former government employee?','answer':False}]},'')
    field=Field(0,'combobox',label,required=True,options=['Yes','No'],options_state='complete')
    filler=PlannedFiller(facts,no_ask)
    answer=filler.plan([field])[0]
    assert answer.value=='No' and answer.fact_id.endswith('/Current or former government employee?/answer')
    assert filler.model_calls==filler.questions==0


@pytest.mark.parametrize('fact',[
    Fact('yes',['Current or former government employee?'],'Yes','applicant'),
    Fact('current',['Are you a current government employee?'],'No','applicant'),
    Fact('resume',['Current or former government employee?'],'No','resume',authority='resume'),
    Fact('referral',['Were you referred by a government official?'],'No','applicant'),
    Fact('relative',['Are you related to a government official?'],'No','applicant'),
])
@pytest.mark.parametrize('label',[DOD,GOV])
def test_a_different_predicate_timeframe_or_authority_cannot_answer_the_question(fact,label):
    field=Field(0,'combobox',label,required=True,options=['Yes','No'],options_state='complete')
    assert PlannedFiller([fact],no_ask).plan([field],allow_human=False)[0].value is None


def test_exact_current_application_declaration_takes_precedence():
    facts=[Fact('global',['Current or former government employee?'],'No','applicant'),
           Fact('application.answer.1',[DOD],'Yes','applicant/application/answers.md')]
    field=Field(0,'select',DOD,options=['Yes','No'],options_state='complete')
    assert PlannedFiller(facts,no_ask).plan([field])[0].value=='Yes'


@pytest.mark.parametrize('label,section',[
    ('Are you a government employee or contractor?',''),
    ('Are you married to a government employee?',''),
    (DOD,'Reference 1'),
])
def test_employment_denial_cannot_establish_contracting_or_another_persons_history(label,section):
    field=Field(0,'select',label,section=section,options=['Yes','No'],options_state='complete')
    facts=[Fact('global',['Current or former government employee?'],'No','applicant')]
    assert PlannedFiller(facts,no_ask).plan([field],allow_human=False)[0].value is None


def test_a_model_cannot_expand_an_employment_denial_to_contracting_history():
    field=Field(0,'select','Are you a government employee or contractor?',
                required=True,options=['Yes','No'],options_state='complete')
    facts=[Fact('global',['Current or former government employee?'],'No','applicant')]
    filler=PlannedFiller(facts,no_ask,model=object())
    filler.invoke=lambda *a,**kw:pytest.fail('Missing contracting history needs owner evidence, not a model guess')
    assert filler.plan([field],allow_human=False)[0].value is None
    assert filler.model_calls==0
