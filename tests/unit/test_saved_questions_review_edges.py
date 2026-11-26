"""Independent requirements for lossless, per-application saved answers."""
import json

import pytest

from jobagent.tools import saved_questions as S
from test_filling_strategy import world, SourceModel


def question(**changes):
    row = {'id': 7, 'label': 'Additional details', 'section': 'Employment history',
           'kind': 'textarea', 'required': False, 'help': 'Leave blank when not applicable.',
           'options': [], 'format': ''}
    return {**row, **changes}


def test_saved_prompt_preserves_full_question_help_and_all_offered_options():
    q = question(label='Explain your experience with ' + 'enterprise infrastructure, ' * 16,
                 kind='select', options=['Offered option ' + str(i) + ' detail' * 24 for i in range(180)])
    rows = S.parse('Example application needs these answers. Reply with JSON\n' + json.dumps([q]))
    assert rows and rows[0]['label'] == q['label']
    assert rows[0]['help'] == q['help']
    assert rows[0]['options'] == q['options']


def test_explicit_optional_blank_is_an_answer_and_null_is_not():
    q = question()
    answers = S.replies([q], json.dumps({'7': ''}))
    assert answers == {S.key_of(q): ''}
    assert S.replies([q], json.dumps({'7': None})) == {}


def test_saved_records_retain_the_question_condition_and_format(tmp_path):
    q = question(format='MM/YYYY')
    S.record(tmp_path, [q])
    row = S.load(tmp_path)[0]
    assert row['help'] == q['help']
    assert row['format'] == q['format']


def test_answer_text_survives_storage_without_markdown_loss(tmp_path):
    q = question()
    text = 'Yes; details follow.\nBuild A | Build B\nSecond paragraph.'
    S.record(tmp_path, [q], {S.key_of(q): text})
    assert S.load(tmp_path)[0]['answer'] == text


def test_a_later_saved_answer_does_not_overwrite_an_owner_edited_sheet_row(tmp_path):
    q = question()
    S.record(tmp_path, [q], {S.key_of(q): 'First saved reply'})
    sheet = tmp_path / 'application' / 'answers.md'
    owner_row = '| 1 | Owner edited question | Owner edited answer |'
    sheet.write_text('| # | Question | Answer |\n|---|---|---|\n' + owner_row + '\n')
    S.record(tmp_path, [q], {S.key_of(q): 'A later saved reply'})
    assert owner_row in sheet.read_text()


def test_away_replay_keeps_same_label_answers_bound_to_their_sections(tmp_path):
    from jobagent import progress
    from jobagent.tools import presence
    from jobagent.tools.hitl import Hitl
    job = tmp_path / 'outputs' / 'example' / '123'
    qs = [question(id=7, label='City', section='Work Experience 1', kind='text', help=''),
          question(id=8, label='City', section='Work Experience 2', kind='text', help='')]
    S.record(job, qs, {S.key_of(qs[0]): 'Tempe', S.key_of(qs[1]): 'Phoenix'})
    current = [{**qs[0], 'id': 30}, {**qs[1], 'id': 47}]
    presence.set_available(False, state_dir=tmp_path)
    h = Hitl(tmp_path / 'hitl', lambda _: pytest.fail('Saved answers must not prompt the away owner'))
    with progress.execution(new=True, job_dir=str(job), url='https://example.test/jobs/123'):
        reply = h.ask('Example application needs these answers. Reply with JSON\n' + json.dumps(current), timeout_s=.1)
    assert json.loads(reply) == {'30': 'Tempe', '47': 'Phoenix'}
    assert not list(h.pending.glob('*.json'))
    assert not (tmp_path / 'hitl' / 'deferrals.jsonl').exists()


@pytest.mark.parametrize('change', [{'help': 'Including OPT documentation'}, {'options': ['Yes', 'No', 'Some']},
                                    {'required': True}])
def test_changed_question_semantics_cannot_replay_an_old_answer_while_away(tmp_path, change):
    from jobagent import progress
    from jobagent.tools import presence
    from jobagent.tools.hitl import CandidateAway, Hitl
    job = tmp_path / 'outputs' / 'example' / '123'
    q = question(label='Do you need employer support?', kind='select', help='Visa sponsorship only', options=['Yes', 'No'])
    S.record(job, [q], {S.key_of(q): 'No'})
    presence.set_available(False, state_dir=tmp_path)
    h = Hitl(tmp_path / 'hitl', lambda _: pytest.fail('Away owner must not receive a notification'))
    with progress.execution(new=True, job_dir=str(job), url='https://example.test/jobs/123'):
        with pytest.raises(CandidateAway):
            h.ask('Example application needs these answers. Reply with JSON\n' + json.dumps([{**q, **change}]), timeout_s=.1)


def test_native_filler_replays_distinct_employer_answers_while_owner_is_away(world):
    from jobagent import progress
    from jobagent.config import get_settings
    from jobagent.steps import apply
    from jobagent.tools import forms, presence
    from jobagent.tools.hitl import Hitl
    world.setup('''<fieldset><legend>Work Experience (1)</legend><label for=a>City</label><input id=a required></fieldset>
      <fieldset><legend>Work Experience (2)</legend><label for=b>City</label><input id=b required></fieldset>
      <button type=button onclick="window.sent=true">Apply</button>''')
    world.page.goto('https://fixture.test/apply')
    fields = forms.read_page(world.page).fields
    qs = [{name: getattr(f, name) for name in ('id', 'label', 'section', 'kind', 'required', 'options', 'help', 'format')}
          for f in fields]
    assert [q['section'] for q in qs] == ['Work Experience 1', 'Work Experience 2']
    S.record(world.job, qs, {S.key_of(qs[0]): 'Tempe', S.key_of(qs[1]): 'Phoenix'})
    state = get_settings().state_dir
    presence.set_available(False, state_dir=state)
    hitl = Hitl(state / 'hitl', lambda _: pytest.fail('No human notification is needed for saved answers'))
    with progress.execution(new=True, job_dir=str(world.job), url='https://fixture.test/apply'):
        result = apply.run(world.page, world.job, 'fixture', model=SourceModel(), hitl=hitl,
                           filling_strategy='adaptive', log=lambda _: None)
    assert result['state'] == 'review', result.get('reason')
    assert world.page.locator('#a').input_value() == 'Tempe'
    assert world.page.locator('#b').input_value() == 'Phoenix'
    assert not world.page.evaluate('!!window.sent')


def test_native_filler_cannot_bypass_changed_help_through_a_generated_markdown_alias(world):
    from jobagent import progress
    from jobagent.config import get_settings
    from jobagent.steps import apply
    from jobagent.tools import forms, presence
    from jobagent.tools.hitl import Hitl
    label = 'Is your response to this question yes?'
    world.setup('<label for=a>' + label + '</label><select id=a required aria-describedby=guide>'
                '<option value="">Choose</option><option>Yes</option><option>No</option></select>'
                '<p id=guide>For applicant placement group B.</p>')
    world.page.goto('https://fixture.test/apply')
    f = forms.read_page(world.page).fields[0]
    q = {name: getattr(f, name) for name in ('id', 'label', 'section', 'kind', 'required', 'options', 'help', 'format')}
    q['help'] = 'For applicant placement group A.'
    S.record(world.job, [q], {S.key_of(q): 'No'})
    state = get_settings().state_dir
    presence.set_available(False, state_dir=state)
    hitl = Hitl(state / 'hitl', lambda _: pytest.fail('The away owner must not be notified'))
    with progress.execution(new=True, job_dir=str(world.job), url='https://fixture.test/apply'):
        result = apply.run(world.page, world.job, 'fixture', model=SourceModel(), hitl=hitl,
                           filling_strategy='adaptive', log=lambda _: None)
    assert result['state'] == 'blocked'
    assert not result.get('ready_to_submit')
    assert world.page.locator('#a').input_value() == ''
