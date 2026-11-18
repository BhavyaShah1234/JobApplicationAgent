"""The saved relevance/cap rule can select owned resume skills without asking."""
from jobagent.filling.facts import compile_facts
from jobagent.filling.planned import PlannedFiller
from jobagent.filling.routing import route
from jobagent.tools.forms import Field


def facts(rule='The ones most relevant to the job description, at most 3.'):
    return compile_facts({'experience': [{'question': 'Skills', 'seen': ['Type to Add Skills'],
        'answer': ['JavaScript', 'Java', 'C', 'C++', 'Python', 'Rust', 'Go'],
        'instructions': rule, 'source': "the job's tailored resume"}]},
        'Required Python, Python, C++ and JavaScript. Optional Kubernetes.')


def no_human(questions):
    raise AssertionError('Resume skills should not need a human: '+repr(questions))


def test_skills_follow_the_explicit_cap_and_relevance_without_borrowing_jd_qualifications():
    field=Field(0,'combobox','Type to Add Skills',section='Skills',multiple=True,options_state='search_required')
    filler=PlannedFiller(facts(),no_human)
    assert route(field,filler.facts).sources==['resume','owner_rules','jd']
    answer=filler.plan([field])[0]
    assert answer.value=='Python; JavaScript; C++'
    assert answer.fact_id.endswith('/Skills/answer/selected')
    assert filler.model_calls==filler.questions==0
    assert next(f for f in filler.facts if f.id==answer.fact_id).authority=='resume'


def test_a_single_choice_gets_one_sourced_skill_and_a_complete_list_filters_unavailable_ones():
    field=Field(0,'select','Skills',options=['JavaScript','Kubernetes'],options_state='complete')
    assert PlannedFiller(facts(),no_human).plan([field])[0].value=='JavaScript'
    field.options=['Kubernetes']
    assert PlannedFiller(facts(),no_human).plan([field])[0].value==''
    field.required=True
    assert PlannedFiller(facts(),no_human).plan([field],allow_human=False)[0].value is None


def test_a_different_owner_rule_is_not_silently_treated_as_the_standard_rule():
    bank=facts('Only use Python if I explicitly approve it for this company.')
    assert not any(f.id.endswith('/selected') for f in bank)


def test_an_absent_selection_rule_keeps_the_original_record_without_inventing_a_policy():
    bank=facts(None)
    assert len(bank)==1 and bank[0].record


def test_an_individual_employer_cannot_borrow_the_resumes_global_skills():
    field=Field(0,'combobox','Skills',section='Work Experience 1',multiple=True)
    assert PlannedFiller(facts(),no_human).plan([field],allow_human=False)[0].value is None
