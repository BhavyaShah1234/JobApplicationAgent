import hashlib
import json

import pytest

from jobagent.tools import applicant, eligibility


POLICY = ('At this time Capital One will not sponsor a new applicant for employment authorization, '
          'or offer any immigration related support for this position (e.g. F-1 OPT, STEM OPT and CPT).')
FACTS = {'us_citizen': False, 'permanent_resident': False, 'authorized': True,
         'needs_sponsorship': False, 'visa_status': 'F-1 OPT', 'needs_immigration_support': None}
QUESTION = ('Will you now or in the future require immigration support or sponsorship? '
            'Support does include documentation needed for F-1 OPT and STEM OPT.')


@pytest.fixture(autouse=True)
def _stop_mode(monkeypatch):
    """These tests are about what the screen does when it enforces (eligibility_mode: stop); the default is now warn (tests/unit/test_eligibility_mode.py)."""
    from jobagent.tools import eligibility
    monkeypatch.setattr(eligibility, "mode", lambda: "stop")


def test_no_visa_sponsorship_does_not_clear_a_broader_no_support_policy():
    verdict = eligibility.screen(POLICY, FACTS)
    assert verdict.level == 'review'
    assert any(r['requirement'] == 'immigration-related support' for r in verdict.reasons)
    assert eligibility.screen(POLICY, {**FACTS, 'needs_immigration_support': True}).level == 'blocked'
    assert eligibility.screen(POLICY, {**FACTS, 'needs_immigration_support': False}).level == 'ok'


@pytest.mark.parametrize('text', [
    'We provide immigration related support for this position, including F-1 OPT documentation.',
    'We will not require candidates to pay for immigration-related support.',
    'We cannot offer technical support to customers after hours.',
])
def test_positive_or_different_support_statements_do_not_exclude_candidates(text):
    assert eligibility.screen(text, {**FACTS, 'needs_immigration_support': True}).level == 'ok'


def test_profile_keeps_visa_and_broader_support_separate_from_sponsorship():
    data = {'work_authorization': [
        {'question': 'Will you now require sponsorship?', 'answer': False},
        {'question': 'Visa status / citizenship', 'answer': {'visa_status': 'F-1 OPT', 'citizenship': 'India'}},
        {'question': 'Do you need immigration-related support, including OPT documentation?', 'answer': True},
    ]}
    facts = applicant.eligibility_facts(data)
    assert facts['visa_status'] == 'f-1 opt'
    assert facts['needs_sponsorship'] is False and facts['needs_immigration_support'] is True


def test_current_posting_support_answer_is_used_again_at_review(monkeypatch, tmp_path):
    monkeypatch.setattr(applicant, 'eligibility_facts', lambda: dict(FACTS))
    (tmp_path / 'jd.txt').write_text(POLICY)
    (tmp_path / 'application').mkdir()
    (tmp_path / 'application/answers.md').write_text('| # | Question | Answer |\n| 1 | '+QUESTION+' | Yes |')
    assert eligibility.assess_job(tmp_path).level == 'blocked'
    assert eligibility.review_blockers(tmp_path)
    digest = hashlib.sha256((tmp_path / 'jd.txt').read_bytes()).hexdigest()
    (tmp_path / 'eligibility.json').write_text(json.dumps({'ignored': True, 'jd_sha256': digest}))
    assert eligibility.review_blockers(tmp_path) == []
    (tmp_path / 'jd.txt').write_text(POLICY+' New requirements.')
    assert eligibility.review_blockers(tmp_path)


@pytest.mark.parametrize('saved', [{'ignored': True}, None, []])
def test_unbound_or_malformed_override_does_not_silence_review(monkeypatch, tmp_path, saved):
    monkeypatch.setattr(applicant, 'eligibility_facts', lambda: dict(FACTS))
    (tmp_path / 'jd.txt').write_text(POLICY)
    (tmp_path / 'eligibility.json').write_text(json.dumps(saved))
    assert eligibility.review_blockers(tmp_path)


def test_live_approval_rechecks_policy_even_when_all_fields_are_verified(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from jobagent.steps import submit
    monkeypatch.setattr(applicant, 'eligibility_facts', lambda: {**FACTS, 'needs_immigration_support': True})
    monkeypatch.setattr(submit.forms, 'upload_blockers', lambda *args: [])
    (tmp_path / 'jd.txt').write_text(POLICY)
    view = SimpleNamespace(fields=[], errors=[], coverage=SimpleNamespace(problems=lambda: []))
    blockers = submit.live_blockers(None, view, job_dir=tmp_path)
    assert len(blockers) == 1
    assert 'eligibility blocked' in blockers[0] and 'immigration-related support' in blockers[0]
    # The normal per-posting override is honored, but never after the JD changes.
    digest = hashlib.sha256((tmp_path / 'jd.txt').read_bytes()).hexdigest()
    (tmp_path / 'eligibility.json').write_text(json.dumps({'ignored': True, 'jd_sha256': digest}))
    assert submit.live_blockers(None, view, job_dir=tmp_path) == []
    (tmp_path / 'jd.txt').write_text(POLICY + ' Updated posting.')
    assert submit.live_blockers(None, view, job_dir=tmp_path)


def test_conflicting_posting_answers_leave_support_unresolved(monkeypatch, tmp_path):
    monkeypatch.setattr(applicant, 'eligibility_facts', lambda: dict(FACTS))
    (tmp_path / 'jd.txt').write_text(POLICY)
    (tmp_path / 'application').mkdir()
    (tmp_path / 'application/answers.md').write_text(
        '| # | Question | Answer |\n| 1 | ' + QUESTION + ' | Yes |\n| 2 | ' + QUESTION + ' | No |')
    assert eligibility.assess_job(tmp_path).level == 'review'
    assert eligibility.review_blockers(tmp_path)
