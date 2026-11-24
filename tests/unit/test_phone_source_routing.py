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


def test_a_dial_code_list_written_flag_code_country_is_matched_by_code_and_country():
    from jobagent.tools.forms import phone_code_option
    options=['🇦🇬 +1 Antigua and Barbuda','🇨🇦 +1 Canada','🇺🇸 +1 United States','🇬🇧 +44 United Kingdom']
    assert phone_code_option('United States (+1)',options)=='🇺🇸 +1 United States'
    assert phone_code_option('+44',options)=='🇬🇧 +44 United Kingdom'
    assert phone_code_option('+1',options) is None                    # +1 alone is ambiguous: never guessed
    assert phone_code_option('United States (+1)',['United States (+1)','Canada (+1)'])=='United States (+1)'      # the older shape still works


ANT_SELECT = """<html><body><label for=pm>Preferred Contact Method</label>
<div class="ant-select ant-select-single"><div class="ant-select-selector"><span class="ant-select-selection-search">
<input id=pm role=combobox readonly aria-haspopup=listbox aria-expanded=false class="ant-select-selection-search-input"></span>
<span class="ant-select-selection-item" title="Email">Email</span></div></div></body></html>"""


def test_an_ant_design_select_reads_back_the_shown_choice_not_its_empty_search_input():
    import pytest
    from playwright.sync_api import sync_playwright
    from jobagent.tools import forms
    with sync_playwright() as pw:
        try:
            b = pw.chromium.launch(channel="chrome", headless=True)
        except Exception:
            pytest.skip("no Chrome here")
        try:
            page = b.new_page(); page.set_content(ANT_SELECT)
            field = next(f for f in forms.read_page(page).fields if f.label == "Preferred Contact Method")
            assert field.kind == "combobox" and field.value == "Email"        # not ''
            page.locator(".ant-select-selection-item").evaluate("e => e.remove()")
            assert next(f for f in forms.read_page(page).fields if f.label == "Preferred Contact Method").value == ""     # nothing chosen: empty
        finally:
            b.close()
