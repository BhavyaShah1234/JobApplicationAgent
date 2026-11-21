"""Exercise real section/review boundaries rather than stubbing the orchestration entry point."""
from types import SimpleNamespace

import pytest

from jobagent.steps import multi_agent as M
from jobagent.tools import cover_letter as C, tailor as T


@pytest.mark.parametrize('summary', [
    'Engineer with 12 years of data engineering experience.',
    'Engineer with 20 years of data science experience.',
    'Engineer with a decade of data platform work.',
])
def test_data_career_duration_is_not_a_dataset_horizon(monkeypatch, summary):
    monkeypatch.setattr(T, 'declared_claims', lambda: {'years_experience': 5})
    assert any('declared 5' in issue for issue in M.summary_issues(summary, False))


def test_zero_declared_tenure_is_explicit_in_the_writer_policy(monkeypatch):
    monkeypatch.setattr(T, 'declared_claims', lambda: {'years_experience': 0})
    assert '0 years' in T.facts_block()
    assert 'only what the current resume states' not in T.facts_block()


@pytest.mark.parametrize('claim', [
    'I have reduced latency by 30%.',
    'I was responsible for a 30% reduction in latency.',
    'I had built services that reduced latency by 30%.',
])
def test_personal_results_with_auxiliaries_cannot_borrow_a_posting_figure(claim):
    from jobagent.state import JobDescription
    letter = C.Letter(salutation='Dear Team,', closing='Sincerely', paragraphs=[
        C.Paragraph(text=text, evidence=[]) for text in ['I am excited to apply.', claim, 'Thank you.']])
    jd = JobDescription(title='Engineer', company='Acme', basic_qualifications=['30% travel'])
    assert any('30%' in issue for issue in C.consistency(letter, 'Built Python services.', jd, ''))


@pytest.mark.parametrize('error_type', [KeyError, TypeError, AttributeError, RuntimeError])
def test_prompt_programming_error_escapes_the_actual_section_boundary(tmp_path, monkeypatch, error_type):
    def broken(*args, **kwargs):
        raise error_type('broken prompt implementation')
    monkeypatch.setattr(M.llm, 'writer', lambda *a, **kw: object())
    monkeypatch.setattr(M, '_section_messages', broken)
    ctx = {'job_dir': tmp_path, 'models': {'summary': 'fixture'}, 'log': lambda _: None}
    with pytest.raises(error_type, match='broken prompt'):
        M._run_agent(ctx, 'summary', None, [], '')


@pytest.mark.parametrize('error_type', [KeyError, TypeError, AttributeError, RuntimeError])
def test_review_programming_error_is_not_retried_or_converted_to_fallback(tmp_path, monkeypatch, error_type):
    calls = []
    def broken(*args):
        calls.append(1)
        raise error_type('broken review implementation')
    monkeypatch.setattr(M, 'content_review', broken)
    filled = T.Filled.model_construct(summary='Engineer.', roles=[], skills=[])
    with pytest.raises(error_type, match='broken review'):
        M._reviewed_content({'job_dir': tmp_path}, filled, {})
    assert len(calls) == 1


def test_invalid_profile_is_not_an_ordinary_failed_section(tmp_path, monkeypatch):
    from jobagent.config import get_settings
    (tmp_path / 'master_profile.yaml').write_text('claims: 5')
    monkeypatch.setattr(get_settings(), 'data_dir', tmp_path)
    monkeypatch.setattr(M.llm, 'writer', lambda *a, **kw: object())
    monkeypatch.setattr(M, '_section_messages', lambda *a, **kw: T.facts_block())
    ctx = {'job_dir': tmp_path, 'models': {'summary': 'fixture'}, 'log': lambda _: None}
    with pytest.raises(ValueError, match='master_profile.yaml'):
        M._run_agent(ctx, 'summary', None, [], '')


def test_recoverable_provider_failure_keeps_the_bounded_section_path(tmp_path, monkeypatch):
    monkeypatch.setattr(M.llm, 'writer', lambda *a, **kw: object())
    monkeypatch.setattr(M, '_section_messages', lambda *a, **kw: [])
    def unavailable(*args, **kwargs):
        raise M.llm.StructuredError('temporary timeout', 'timeout')
    monkeypatch.setattr(M, '_invoke', unavailable)
    ctx = {'job_dir': tmp_path, 'models': {'summary': 'fixture'}, 'log': lambda _: None}
    assert M._run_agent(ctx, 'summary', None, [], '') == ('summary', None)


@pytest.mark.parametrize('error', [KeyError('broken worker'), TypeError('broken worker'),
    AttributeError('broken worker'), RuntimeError('broken worker'),
    M.llm.LLMConfigError('broken worker configuration', 'config')])
def test_incremental_worker_cannot_disguise_fatal_error_as_unavailable_review(tmp_path, monkeypatch, error):
    from jobagent.tools import incremental_documents as I
    from jobagent.tools.artifact_cache import ArtifactCache
    monkeypatch.setattr(T, 'facts_block', lambda: 'Owner facts')
    monkeypatch.setattr(I.llm, 'writer', lambda *a, **kw: SimpleNamespace())
    def generate(model, schema, messages, **kwargs):
        if 'Review ONLY experience.' in messages[0].content:
            raise error
        return schema.model_validate({'findings': []})
    monkeypatch.setattr(I.llm, 'structured_invoke', generate)
    ctx = {'job_dir': tmp_path, 'r': SimpleNamespace(roles=[], skills=[], tex=''),
           'jd': SimpleNamespace(model_dump=lambda **kw: {'title': 'Engineer', 'company': 'Acme'}),
           'models': {'router': 'fixture'}, 'cache': ArtifactCache(tmp_path / 'cache'), 'log': lambda _: None}
    filled = T.Filled.model_construct(summary='Engineer building Python services.', roles=[], skills=[])
    with pytest.raises(type(error), match='broken worker') as caught:
        I.review(ctx, filled)
    assert caught.value is error


@pytest.mark.parametrize('stage', [None, 'experience', 'summary', 'letter', 'letter-attribution',
                                 'review:experience', 'adjudicate:summary'])
def test_metric_validation_code_is_a_cache_dependency(tmp_path, monkeypatch, stage):
    from jobagent.tools import job_context as J
    (tmp_path / 'tools').mkdir()
    helper = tmp_path / 'tools/number_claims.py'
    helper.write_text('metric policy v1')
    monkeypatch.setattr(J, 'Path', lambda _: SimpleNamespace(parents={1: tmp_path}))
    before = J.model_version(stage)
    helper.write_text('metric policy v2')
    assert J.model_version(stage) != before
