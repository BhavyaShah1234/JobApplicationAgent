"""Standing permission must reach new agreement fields without answering history."""
import pytest

from jobagent.filling.agreements import POLICY_CONTEXT, POLICY_QUESTION, agreement_value, project
from jobagent.filling.facts import Fact, resolve
from jobagent.filling.planned import PlannedFiller
from jobagent.filling.routing import binding_valid, resolve_routed, route
from jobagent.tools.forms import Field


def policy(answer='Yes'):
    return [{'question': POLICY_QUESTION, 'company': 'any', 'answer': answer, 'policy_context': POLICY_CONTEXT}]


def field(label, kind='combobox', options=None, **kwargs):
    return Field(id=0, label=label, kind=kind, options=['Yes', 'No'] if options is None else options,
                 options_state='complete', **kwargs)


@pytest.mark.parametrize('f, expected', [
    (field('Do you agree to the terms and conditions?'), 'Yes'),
    (field('I acknowledge that I accept the recruitment privacy notice', 'checkbox'), 'checked'),
    (field('I agree', 'checkbox', section='Terms and Conditions'), 'checked'),
    (field('Do you agree to the Non Disclosure Agreement?', options=[
        'I have read and agree to the Non Disclosure Agreement',
        'I have read and DO NOT agree to the Non Disclosure Agreement']),
        'I have read and agree to the Non Disclosure Agreement'),
    (field('Applicants must agree to the Mutual Arbitration Agreement', options=[
        'I have read and agree to the Non Disclosure Agreement',
        'I have read and DO NOT agree to the Non Disclosure Agreement',
        'I have read and agree to the Mutual Arbitration Agreement',
        'I have read and DO NOT agree to the Mutual Arbitration Agreement']),
        'I have read and agree to the Mutual Arbitration Agreement'),
    (field('I acknowledge the ethics code of conduct', 'radio', ['Agree', 'Disagree']), 'Agree'),
    (field('I consent to data processing', 'textarea', []), 'I agree'),
    (field('Enter Yes if you agree to the agreement', 'text', []), 'Yes'),
    (field('I certify the above information is accurate', 'checkbox'), 'checked'),
    (field('I acknowledge the at-will employment terms', options=[
        'Yes, I understand there is no employment contract', 'No, I do not agree']),
        'Yes, I understand there is no employment contract'),
    (field('I do not agree to the terms', 'checkbox'), ''),
    (field('Do you disagree with the terms?', 'radio'), 'No'),
])
def test_standing_permission_is_bound_and_plans_without_a_model_or_hitl(f, expected):
    facts = project([f], [], policy())
    r = route(f, facts)
    assert (resolve_routed(f, facts, r) or resolve(f, facts)).value == expected
    assert binding_valid(f, facts, expected, r)
    def unexpected(*_args, **_kwargs):
        pytest.fail('Owner agreement permission should not need a model or a human')
    filler = PlannedFiller(facts, unexpected)
    filler.invoke = unexpected
    answers = filler.plan([f])
    assert answers[f.id].value == expected


@pytest.mark.parametrize('label', [
    'Are you bound by a non-disclosure agreement with your current employer?',
    'Have you previously signed an arbitration agreement?',
    'I acknowledge I have 5 years of experience',
    'I acknowledge that I am a United States citizen',
    'I acknowledge that I opened the email and marked the sender as safe',
    'Please enter your name:',
    'Do you require visa sponsorship?',
    'I agree that I have completed the required assessment under the terms',
])
def test_permission_does_not_answer_biographical_questions_or_completed_actions(label):
    assert project([field(label)], [], policy()) == []


@pytest.mark.parametrize('entries', [[], policy('No'), [{**policy()[0], 'company': 'example'}],
                                      [{**policy()[0], 'policy_context': ''}]])
def test_permission_is_explicit_opt_in_and_revocable(entries):
    assert project([field('Do you agree to the terms?')], [], entries) == []


def test_existing_current_application_answer_takes_precedence():
    f = field('Do you agree to the arbitration agreement?')
    current = Fact('application.answer.0', [f.label], 'No', 'human')
    assert project([f], [current], policy()) == [current]


def test_permission_does_not_match_another_control_with_the_same_label():
    f = field('Do you agree to the terms?', identity='one')
    other = field(f.label, identity='two')
    facts = project([f], [], policy())
    assert (resolve_routed(other, facts) or resolve(other, facts)).value is None
    assert len(project([f, other], [], policy())) == 2


def test_incomplete_or_ambiguous_catalogue_does_not_invent_an_answer():
    f = field('Do you agree to the agreement?', options=['Accept A', 'Accept B', 'Decline'])
    assert agreement_value(f) is None
    f.options_state = 'partial'
    assert agreement_value(f) is None


def test_new_policy_does_not_press_submit_or_challenge_controls():
    assert agreement_value(field('I agree to terms and submit application', 'submit')) is None
    assert agreement_value(field('I am human; accept verification challenge', 'checkbox')) is None
    assert agreement_value(field('I accept terms and confirm I am not a robot', 'checkbox')) is None
    assert agreement_value(field('I agree to the terms', 'buttons', ['Agree and submit', 'Cancel'])) is None


def test_revocation_removes_projected_policy_answers():
    f = field('I agree to terms')
    assert project([f], project([f], [], policy()), policy('No')) == []


def test_legacy_resolver_uses_the_same_owner_permission(monkeypatch):
    from jobagent.steps import apply
    monkeypatch.setattr(apply.learned, 'load', lambda: policy())
    f = field('Have you read and agree to the Mutual Arbitration Agreement?', options=['I agree', 'I do not agree'])
    answers = {}
    apply.resolve([f], answers, {}, None, {}, lambda *_: None)
    assert answers[f.id].value == 'I agree'
    assert answers[f.id].ask_human is False
