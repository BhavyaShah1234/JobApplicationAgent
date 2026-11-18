"""Never turn a saved non-veteran declaration into affirmative military service."""
import pytest
from jobagent.filling.facts import Fact,Decision
from jobagent.filling.planned import PlannedFiller
from jobagent.tools.forms import Field

AFFIRMATIVE='I identify as a veteran, just not a protected veteran'


def plan(options,source_resolver=None):
    fact=Fact('veteran',['Veteran status'],'Not a veteran','applicant')
    def fail(*a,**kw):pytest.fail('A personal status cannot be guessed by a model or an operator')
    filler=PlannedFiller([fact],fail,model=object(),source_resolver=source_resolver)
    filler.invoke=fail
    value=filler.plan([Field(0,'combobox','Please indicate your veteran status. *',required=True,
        options=options,options_state='complete')],allow_human=False)[0]
    assert filler.model_calls==filler.questions==0
    return value


@pytest.mark.parametrize('negative',['Not a veteran','I am not a veteran','I identify as not a veteran','Non-veteran'])
def test_explicit_non_veteran_option_precedes_related_negative_protected_status(negative):
    assert plan([AFFIRMATIVE,'I am not a protected veteran',negative]).value==negative


@pytest.mark.parametrize('options',[[AFFIRMATIVE],['I am a protected veteran',AFFIRMATIVE,'I decline to answer']])
def test_non_protected_veteran_is_not_an_answer_for_someone_who_is_not_a_veteran(options):
    assert plan(options).value is None


@pytest.mark.parametrize('negative',['No','I am not a protected veteran','Not a protected veteran'])
def test_non_veteran_can_truthfully_deny_a_binary_protected_veteran_question(negative):
    assert plan(['I am a protected veteran',negative]).value==negative


def test_source_resolver_cannot_expand_the_saved_status_to_affirmative_military_service():
    result=plan([AFFIRMATIVE],source_resolver=lambda *_:{0:Decision(AFFIRMATIVE,'applicant','veteran')})
    assert result.value is None
