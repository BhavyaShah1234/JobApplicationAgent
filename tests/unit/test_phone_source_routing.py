"""Reworded phone pickers must bind to the right component before any model/HITL."""
import pytest

from jobagent.filling.facts import Fact, compile_facts
from jobagent.filling.planned import PlannedFiller
from jobagent.filling.routing import route
from jobagent.tools.forms import Field


def phone_facts():
    return compile_facts({'contact': [
        {'question': 'Legal name', 'answer': {'first': 'Casey', 'last': 'Morgan'}},
        {'question': 'Phone', 'answer': {
            'number': '2025550100', 'country': 'United States', 'country_code': '+1',
            'device_type': 'Mobile', 'extension': '',
        }},
    ], 'address': [{'question': 'Current address', 'answer': {'country': 'Canada'}}]}, '')


def no_human(questions):
    pytest.fail('A known phone code must not request human answers: '+repr(questions))


@pytest.mark.parametrize('label', [
    'Country/Region Phone Code *', 'Country Calling Code', 'Telephone Dial Code', 'Dialing Code',
])
def test_reworded_code_resolves_with_other_known_fields_without_model_or_human(label):
    fields = [Field(0, 'text', 'First Name *'), Field(9, 'combobox', label,
        section='Phone', required=True, value='United States of America (+1)',
        options=['United States of America (+1)'], options_state='partial')]
    filler = PlannedFiller(phone_facts(), no_human)
    answers = filler.plan(fields)
    assert answers[0].value == 'Casey'
    assert answers[9].value == 'United States of America (+1)'
    assert answers[9].fact_id.endswith('/Phone/answer/country_code')
    assert filler.model_calls == filler.questions == 0


def test_partial_list_with_wrong_country_searches_the_country_from_the_phone_record():
    field = Field(9, 'combobox', 'Country/Region Phone Code *', section='Phone',
                  options=['Canada (+1)'], options_state='partial')
    filler = PlannedFiller(phone_facts(), no_human)
    assert filler.plan([field])[9].value == 'United States (+1)'
    field.options_state = 'complete'
    assert filler.plan([field], allow_human=False)[9].value is None


@pytest.mark.parametrize('section', ['Reference 1', 'Emergency contact', 'Supervisor', 'Work Experience 1', 'Education 1'])
def test_another_person_cannot_borrow_the_applicants_dial_code(section):
    field = Field(9, 'combobox', 'Country/Region Phone Code *', section=section,
                  options=['United States of America (+1)'], options_state='complete')
    facts = phone_facts()
    assert route(field, facts).fact_ids == []
    assert PlannedFiller(facts, no_human).plan([field], allow_human=False)[9].value is None


def test_an_explicitly_scoped_contact_keeps_its_own_phone_code():
    field = Field(9, 'combobox', 'Country/Region Phone Code *', section='Reference 1',
                  options=['United States of America (+1)', 'India (+91)'], options_state='complete')
    own = Fact('reference-code', ['Country Phone Code'], '+91', 'applicant/references/code',
               section='Reference 1', variants=['India (+91)'])
    answer = PlannedFiller([*phone_facts(), own], no_human).plan([field])[9]
    assert answer.value == 'India (+91)' and answer.fact_id == 'reference-code'


def test_conflicting_phone_codes_remain_unresolved():
    facts = [Fact('a', ['Country Phone Code'], '+1', 'applicant/a', variants=['United States (+1)']),
             Fact('b', ['Country Phone Code'], '+91', 'applicant/b', variants=['India (+91)'])]
    field = Field(9, 'combobox', 'Country/Region Phone Code *',
                  options=['United States of America (+1)', 'India (+91)'], options_state='complete')
    assert PlannedFiller(facts, no_human).plan([field], allow_human=False)[9].value is None


def test_a_number_box_that_normalises_what_was_typed_still_reads_back_as_the_same_number():
    from jobagent.tools.forms import same_value
    assert same_value("4.0", "4", "spin") and same_value("4", "4.0", "number") and same_value("1,000", "1000", "spin")
    assert not same_value("4.0", "3", "spin") and not same_value("4.0", "", "spin")
    assert same_value("abc", "abc", "spin")                        # non-numeric text falls through to the ordinary comparison
