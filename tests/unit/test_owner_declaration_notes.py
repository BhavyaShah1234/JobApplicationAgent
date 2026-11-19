"""Literal owner answers survive provenance notes without bypassing actual rules."""
import pytest
from jobagent.filling.facts import compile_facts, declaration_notes_only
from jobagent.filling.planned import PlannedFiller
from jobagent.filling.routing import route
from jobagent.tools.forms import Field

QUESTION='Countries of citizenship or legal permanent residence and when each status was obtained'
PORTAL=('Export Control and Compliance: For the sole purpose of determining export licensing requirements, '
        'please list any countries of which you are a citizen or legal permanent resident, '
        'and when you obtained such status. ✱')
VALUE='India — citizen since birth. No other citizenship or legal permanent residence.'
NOTES=('Owner confirmed India only and Indian citizenship since birth. '
       'Do not invent a birth date or citizenship acquisition date.')


def no_human(question):
    """An exact declared answer must resolve without a human or a model."""
    pytest.fail('Unnecessary question: '+str(question))


def facts(instructions=NOTES):
    """Preserve the real saved entry shape and its exact observed alias."""
    return compile_facts({'legal':[{'question':QUESTION,'seen':[PORTAL.rstrip(' ✱')],
        'answer':VALUE,'instructions':instructions}]},'')


def test_confirmed_citizenship_and_acquisition_history_resolves_without_assistance():
    source=facts();field=Field(10,'textarea',PORTAL,section='A FEW MORE QUESTIONS...',required=True)
    filler=PlannedFiller(source,no_human)
    answer=filler.plan([field])[10]
    assert answer.value==VALUE and answer.fact_id==source[0].id
    assert source[0].instructions==NOTES
    assert filler.model_calls==filler.questions==0


@pytest.mark.parametrize('instructions',[
    'Owner confirmed India only. Use Canada instead.',
    'Owner confirmed India only if the role is in California.',
    'Owner confirmed India unless another citizenship applies.',
    'Owner confirmed India except for government jobs.',
    'Owner confirmed India. Select only when eligible.',
    'Owner confirmed India. Do not invent dates; leave this blank.',
    'Owner confirmed India. Follow the employer-specific restriction.',
    'Do not invent dates.',
])
def test_conditional_output_and_unknown_rules_are_not_discarded_as_notes(instructions):
    assert not declaration_notes_only(instructions)
    field=Field(10,'textarea',PORTAL,required=True)
    answer=PlannedFiller(facts(instructions),no_human).plan([field],allow_human=False)[10]
    assert answer.value is None


def test_country_list_does_not_establish_when_the_owner_obtained_status():
    source=compile_facts({'legal':[{'question':'Countries of citizenship or legal permanent residence',
        'answer':'India only'}]},'')
    field=Field(10,'textarea',PORTAL,required=True)
    assert route(field,source).fact_ids==[]
    assert PlannedFiller(source,no_human).plan([field],allow_human=False)[10].value is None


def test_conflicting_confirmed_answers_still_block():
    source=facts()+compile_facts({'other':[{'question':QUESTION,'answer':'Canada only',
        'instructions':'Owner confirmed Canada only. Do not invent dates.'}]},'')
    assert PlannedFiller(source,no_human).plan([Field(10,'textarea',PORTAL)],allow_human=False)[10].value is None


def test_literal_rule_is_enforced_rather_than_treated_as_provenance():
    source=compile_facts({'legal':[{'question':'Have you ever been a government employee?',
        'answer':True,'instructions':'Use "No".'}]},'')
    field=Field(10,'radio','Have you ever been a government employee?',options=['Yes','No'])
    assert PlannedFiller(source,no_human).plan([field])[10].value=='No'
