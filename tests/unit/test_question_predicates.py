"""Counterexamples to treating adjacent declarations as interchangeable boolean facts."""
import pytest

from jobagent.filling.facts import Fact, norm
from jobagent.filling.planned import PlannedFiller
from jobagent.filling.routing import route, resolve_routed, binding_valid, needs_entity_binding
from jobagent.filling.adapter import _saved_matches
from jobagent.tools import forms, learned


INTRO = ('For these questions, a Senior Government Official is an official of a foreign government. '
         'This definition applies to the official described below. ') * 4
REFERRAL = INTRO + 'Were you referred for this position by a Senior Government Official?'
RELATIVE = INTRO + 'Are you related to a Senior Government Official?'
ACK = ('All associates must follow the Code of Conduct. Inform your manager and receive Ethics Office approval '
       'before outside business activity. ') * 3 + 'Please acknowledge your understanding of this requirement.'


def field(label, ident=0):
    return forms.Field(id=ident, kind='select', label=label, options=['Yes', 'No'])


@pytest.mark.parametrize('family,label', [
    ('government_referral', REFERRAL), ('government_relationship', RELATIVE), ('ethics_ack', ACK),
])
def test_long_declarations_are_classified_by_their_actual_question(family, label):
    assert len(label) > 300
    assert route(field(label), []).family == family


def test_a_relative_answer_cannot_establish_a_government_referral_even_when_both_are_no():
    relative = Fact('relative', [RELATIVE], 'No', 'applicant/background')
    f = field(REFERRAL)
    routed = route(f, [relative])
    assert resolve_routed(f, [relative], routed).value is None
    assert not binding_valid(f, [relative], 'No', routed)
    planner = PlannedFiller([relative], lambda _: pytest.fail('allow_human is false'))
    assert planner.plan([f], allow_human=False)[f.id].value is None


def test_a_generic_employee_referral_is_not_a_government_official_referral():
    employee = Fact('employee', ['Employee referral'], 'Yes', 'applicant/source')
    f = field(REFERRAL)
    assert resolve_routed(f, [employee]).value is None


def test_a_particular_government_definition_is_not_silently_replaced_by_another():
    prior = Fact('different_definition', ['Were you referred by a government official?'], 'No', 'applicant/background')
    assert resolve_routed(field(REFERRAL), [prior]).value is None


def test_each_declaration_keeps_its_own_answer_and_fact_id():
    facts = [Fact('referral', [REFERRAL], 'Yes', 'applicant/background'),
             Fact('relative', [RELATIVE], 'No', 'applicant/background')]
    referral = resolve_routed(field(REFERRAL), facts)
    relative = resolve_routed(field(RELATIVE), facts)
    assert (referral.value, referral.fact_id) == ('Yes', 'referral')
    assert (relative.value, relative.fact_id) == ('No', 'relative')


def test_a_saved_policy_acknowledgement_is_not_mistaken_for_a_managers_contact(monkeypatch):
    monkeypatch.setattr(learned, '_vectors', lambda _: pytest.fail('An exact acknowledgement needs no embeddings'))
    f = field(ACK)
    entry = {'question': ACK, 'answer': 'Yes', 'company': 'fixture'}
    assert not needs_entity_binding(f.label, kind=f.kind)
    assert _saved_matches([f], [entry], 'fixture')[norm(ACK)] == [entry]
    decision = resolve_routed(f, [Fact('ack', [ACK], 'Yes', 'learned:fixture:ack')])
    assert (decision.value, decision.fact_id) == ('Yes', 'ack')
    assert needs_entity_binding('Supervisor name', kind='text')


def test_answer_sheet_and_source_resolver_shortcuts_cannot_bypass_question_binding():
    wrong = Fact('application.answer.1', [RELATIVE], 'No', 'applicant/application/answers.md')
    planner = PlannedFiller([wrong], lambda _: pytest.fail('allow_human is false'),
                            source_resolver=lambda *_: {})
    assert planner.plan([field(REFERRAL)], allow_human=False)[0].value is None


def test_duplicate_matching_sources_are_unambiguous_and_the_current_job_sheet_wins():
    f = field(ACK)
    old = Fact('learned', [ACK], 'No', 'learned:fixture:ack')
    current = Fact('application.answer.1', [ACK], 'Yes', 'applicant/application/answers.md')
    result = resolve_routed(f, [old, current])
    assert (result.value, result.fact_id) == ('Yes', 'application.answer.1')
    assert resolve_routed(f, [old, Fact('other', [ACK], 'Yes', 'applicant/consent')]).value is None


def test_no_relatives_does_not_establish_no_personal_or_financial_ties():
    q = 'Do you have any close or intimate personal relationships, shared financial interests, or other personal or business ties with anyone employed here?'
    relative = Fact('relatives', ['Relatives working at the company?'], 'No', 'applicant/history')
    assert route(field(q), [relative]).family == 'employer_personal_ties'
    assert resolve_routed(field(q), [relative]).value is None
    explicit = Fact('ties', [q], 'No', 'applicant/history')
    assert resolve_routed(field(q), [relative, explicit]).fact_id == 'ties'


def test_an_outside_work_answer_cannot_serve_as_an_ethics_acknowledgement():
    q = 'Do you have a second job or outside business that you will continue while employed here?'
    outside = Fact('outside', [q], 'No', 'applicant/history')
    assert route(field(q), [outside]).family == 'outside_activity'
    assert resolve_routed(field(ACK), [outside]).value is None
