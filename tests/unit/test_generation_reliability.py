"""Independent regressions for the full document lifecycle, including real TeX failures."""
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from jobagent.config import get_settings
from jobagent.steps import multi_agent as M
from jobagent.tools import cover_letter as C, documents as D, tailor as T


def filled():
    return T.Filled.model_construct(summary="Engineer with Python experience.", roles=[], skills=[], notes="")


def test_literal_code_braces_compile_and_remain_visible(tmp_path):
    prose = 'Parsed JSON {"key": "value"} and handled a standalone } safely.'
    tex = r'\documentclass{article}\begin{document}' + T.to_latex(prose) + r'\end{document}'
    assert T.compile_pdf(tex, tmp_path) == (1, "")
    text = subprocess.check_output(['pdftotext', str(tmp_path/'resume.pdf'), '-'], text=True)
    assert '{' in text and '}' in text and 'standalone' in text


def test_failed_compile_removes_previous_pdf_and_its_measurements(tmp_path):
    assert T.compile_pdf(r'\documentclass{article}\begin{document}Good.\end{document}', tmp_path) == (1, '')
    pages, error = T.compile_pdf(r'\documentclass{article}\begin{document}}\end{document}', tmp_path)
    assert pages == 0 and error
    assert not (tmp_path/'resume.pdf').exists()
    assert not (tmp_path/'resume.layout.json').exists()


def test_compiler_timeout_is_reported_without_a_stale_pdf(tmp_path, monkeypatch):
    (tmp_path/'resume.pdf').write_bytes(b'stale')
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired('latexmk', 180)
    monkeypatch.setattr(T.subprocess, 'run', timeout)
    pages, error = T.compile_pdf('source', tmp_path)
    assert pages == 0 and 'TimeoutExpired' in error
    assert not (tmp_path/'resume.pdf').exists()


def test_transient_review_retries_the_unchanged_content_without_rewriting(tmp_path, monkeypatch):
    calls = []
    def review(ctx, current):
        calls.append(current.summary)
        if len(calls) == 1:
            raise M.llm.StructuredError('temporary timeout', 'timeout')
        return []
    monkeypatch.setattr(M, 'content_review', review)
    result = M._reviewed_content({'job_dir': tmp_path, 'log': lambda _: None}, filled(), {})
    assert not result['content_error'] and len(calls) == 2
    assert calls[0] == calls[1]
    M._reviewed_content({'job_dir': tmp_path}, filled(), result)
    assert len(calls) == 2  # a successful unchanged review is reusable


def test_failed_review_is_never_reused_as_a_successful_review(tmp_path, monkeypatch):
    f = filled()
    old = {'content_hash': M._content_hash({'job_dir': tmp_path}, f),
           'content_findings': [], 'content_error': 'earlier provider timeout'}
    monkeypatch.setattr(M, 'content_review', lambda *args: [])
    assert M._reviewed_content({'job_dir': tmp_path}, f, old)['content_error'] == ''


def snapshot_world(tmp_path, monkeypatch):
    (tmp_path/'resume.tex').write_text('accepted source')
    (tmp_path/'resume.pdf').write_bytes(b'accepted PDF')
    (tmp_path/'resume.layout.json').write_text('{"free_pt":4}')
    monkeypatch.setattr(M.layout, 'check', lambda _: SimpleNamespace(issues=[]))
    ctx = {'job_dir': tmp_path, 'jd': None, 'jd_text': 'JD', 'sites': ('hireflow',), 'log': lambda _: None}
    f = filled()
    monkeypatch.setattr(M, 'render', lambda _: (f, 'accepted source', [], 1, ''))
    monkeypatch.setattr(M, 'hard_issues', lambda *args: [])
    return ctx, f


def test_rollback_restores_measurements_from_the_same_pdf(tmp_path, monkeypatch):
    ctx, f = snapshot_world(tmp_path, monkeypatch)
    best = M._snapshot(ctx, f, [])
    (tmp_path/'resume.layout.json').write_text('{"free_pt":90}')
    (tmp_path/'resume.pdf').write_bytes(b'rejected PDF')
    M._rollback({'ctx': ctx, 'best': best}, 'reject edit')
    assert (tmp_path/'resume.pdf').read_bytes() == b'accepted PDF'
    assert json.loads((tmp_path/'resume.layout.json').read_text())['free_pt'] == 4


@pytest.mark.parametrize('raises', [False, True])
def test_unavailable_ats_keeps_a_valid_resume_and_never_requests_overflow_repair(tmp_path, monkeypatch, raises):
    ctx, f = snapshot_world(tmp_path, monkeypatch)
    calls = []
    def unavailable(*args, **kwargs):
        calls.append(1)
        if raises:
            raise RuntimeError('site unavailable')
        return {'scores': {}, 'missing': {}, 'advice': [], 'template': [], 'keep': [], 'completed': []}
    monkeypatch.setattr(M.A, 'evaluate', unavailable)
    result = M.score({'ctx': ctx, 'f': f})
    assert result['best']['pdf'] == b'accepted PDF'
    assert result['best']['hard'] == [] and result['ats_unavailable']
    assert len(calls) == 2 and M.after_score({'ctx': ctx, **result}) == 'done'


def letter_world(tmp_path, monkeypatch, drafts, issues, compiler, *, header=True):
    (tmp_path/'resume.tex').write_text('resume')
    monkeypatch.setattr(C, 'resume_text', lambda _: 'resume')
    monkeypatch.setattr(C, 'template_path', lambda: SimpleNamespace(read_text=lambda: 'template', name='template'))
    monkeypatch.setattr(C, 'build_prompt', lambda *args: [])
    drafts = iter(drafts)
    monkeypatch.setattr(C.llm, 'structured_invoke', lambda *args, **kwargs: next(drafts))
    monkeypatch.setattr(C, 'render', lambda template, draft, jd: draft.salutation)
    monkeypatch.setattr(C, 'consistency', issues)
    monkeypatch.setattr(C, 'header_matches', lambda *args: header)
    monkeypatch.setattr(C, 'compile_pdf', compiler)


def letter(name):
    return C.Letter(salutation=name, closing='Sincerely', paragraphs=[
        C.Paragraph(text='Supported evidence.', evidence=[]) for _ in range(3)])


def test_letter_best_selection_prefers_a_compiled_one_page_candidate(tmp_path, monkeypatch):
    def compile_pdf(tex, directory, **kwargs):
        (directory/'cover_letter.pdf').write_bytes(tex.encode())
        return (2 if tex == 'Two pages' else 1), ''
    letter_world(tmp_path, monkeypatch, [letter('Two pages'), letter('One page')],
        lambda draft, *args: [] if draft.salutation == 'Two pages' else ['repair one', 'repair two'], compile_pdf)
    result = C.write(tmp_path, SimpleNamespace(), '', object(), rounds=2, log=lambda _: None)
    assert result['pages'] == 1 and result['letter'].salutation == 'One page'
    assert result['valid'] is False and result['issues'] == ['repair one', 'repair two']
    assert (tmp_path/'cover_letter.pdf').read_bytes() == b'One page'


def test_successful_letter_is_not_recompiled_and_header_mismatch_is_blocking(tmp_path, monkeypatch):
    calls = []
    def compile_pdf(tex, directory, **kwargs):
        calls.append(1)
        assert len(calls) == 1
        (directory/'cover_letter.pdf').write_bytes(b'compiled')
        return 1, ''
    letter_world(tmp_path, monkeypatch, [letter('Dear Team')], lambda *args: [], compile_pdf, header=False)
    result = C.write(tmp_path, SimpleNamespace(), '', object(), rounds=1, log=lambda _: None)
    assert len(calls) == 1 and result['valid'] is False
    assert any('header' in issue for issue in result['issues'])


@pytest.fixture
def cli_world(tmp_path, monkeypatch):
    from jobagent import main
    from jobagent.steps import tailor
    data, state, job = (tmp_path/name for name in ('data', 'state', 'job'))
    (data/'templates').mkdir(parents=True)
    (data/'templates/resume.tex').write_text('current template')
    (state/'companies').mkdir(parents=True)
    (state/'companies/acme.md').write_text('Acme company facts')
    job.mkdir()
    (job/'jd.json').write_text('{"title":"Engineer","company":"Acme","source_url":"https://example.com/job"}')
    monkeypatch.setenv('DATA_DIR', str(data)); monkeypatch.setenv('STATE_DIR', str(state))
    monkeypatch.setenv('DOCUMENT_STRATEGY', 'incremental')
    get_settings.cache_clear()
    monkeypatch.setattr(main, '_skill_inputs', lambda *args: 'off')
    monkeypatch.setattr(D, 'pdf_check', lambda *args: (1, ''))
    resume_calls, letter_calls = [], []
    def resume(directory, *args, **kwargs):
        resume_calls.append(kwargs)
        for name in ('resume.tex', 'resume.pdf', 'filled.json', 'resume.layout.json'):
            (directory/name).write_text('output')
        return {'produced': True, 'average': None, 'scores': {}, 'average_sites': [], 'average_missing': [],
                'average_complete': False, 'pages': 1, 'hard_issues': [], 'claims_issues': [],
                'template_sha256': D.template_fingerprint()}
    def cover(directory, *args, **kwargs):
        letter_calls.append(kwargs)
        (directory/'cover_letter.tex').write_text('letter')
        (directory/'cover_letter.pdf').write_text('letter PDF')
        return {'valid': len(letter_calls)>1, 'issues': ['repair needed'] if len(letter_calls)==1 else [], 'words': 300, 'pages': 1}
    monkeypatch.setattr(M, 'run_job', resume)
    monkeypatch.setattr(tailor, 'cover_letter', cover)
    yield main, job, resume_calls, letter_calls
    get_settings.cache_clear()


def test_explicit_current_overrides_incremental_env_and_invalid_letter_alone_is_retried(cli_world):
    main, job, resumes, letters = cli_world
    command = ['tailor', str(job), 'acme', '--no-ats', '--document-strategy', 'current']
    first = CliRunner().invoke(main.app, command)
    assert first.exit_code == 1, first.output
    assert resumes[0]['document_strategy'] == 'current'
    assert json.loads((job/'workflow.json').read_text())['stages']['cover_letter']['status'] == 'failed'
    second = CliRunner().invoke(main.app, command)
    assert second.exit_code == 0, second.output
    assert len(resumes) == 1 and len(letters) == 2


def test_missing_measurements_invalidate_resume_reuse(cli_world):
    main, job, resumes, _ = cli_world
    # The resume schema and measurements are all cache dependencies, not only PDFs.
    command = ['tailor', str(job), 'acme', '--no-ats', '--document-strategy', 'current']
    CliRunner().invoke(main.app, command)
    (job/'resume.layout.json').unlink()
    CliRunner().invoke(main.app, command)
    assert len(resumes) == 2


def test_summary_adjudication_resolves_exact_evidence_ids_instead_of_retyping_quotes(tmp_path, monkeypatch):
    from jobagent.tools import incremental_documents as I
    from jobagent.tools.artifact_cache import ArtifactCache
    payload = {'summary': 'Engineer with Python experience.', 'roles': [], 'projects': [],
               'skills': [('Languages', ['Python'])], 'education': '', 'company_context': ''}
    finding = I.SummaryFinding(kind='skills', claim_quote='Python experience', issue='Unsupported skill', severity='error')
    monkeypatch.setattr(I, 'review_model', lambda *args: object())
    def invoke(cache, stage, model_name, model, schema, messages, **kwargs):
        data = json.loads(messages[-1].content)
        ref = next(key for key, value in data['source_excerpts'].items() if value['source_id']=='skills')
        answer = schema.model_validate({'verdicts': [{'finding': 0, 'status': 'rejected', 'source_ref': ref,
                                                     'reason': 'The displayed skill supports the claim.'}]})
        kwargs['validator'](answer)
        return answer
    monkeypatch.setattr(I, 'invoke', invoke)
    ctx = {'models': {'router': 'reviewer'}, 'cache': ArtifactCache(tmp_path), 'log': lambda _: None}
    assert I.adjudicate_summary(ctx, payload, [finding]) == []


def test_embedding_failure_ranks_existing_evidence_without_inventing_projects(monkeypatch):
    from jobagent.tools import repo_cards as R
    def unavailable(*args):
        raise ConnectionError('embedding provider down')
    monkeypatch.setattr(R.llm, 'embeddings', lambda: SimpleNamespace(embed_query=unavailable))
    def card(name, text):
        return {'full_name': name, 'title': text, 'summary': text, 'tech_stack': ['Python'],
                'fields': [], 'readme': text, 'substance': 4, 'kind': 'application'}
    cards = [card('owner/api', 'Python API service'), card('owner/vision', 'Computer vision system')]
    result = R.rank(cards, 'Python API service', log=lambda _: None)
    assert [entry['full_name'] for _, entry in result] == ['owner/api', 'owner/vision']
    assert all(entry in cards for _, entry in result)


def test_current_generation_uses_the_same_quality_and_coverage_acceptance_checks(tmp_path, monkeypatch):
    from jobagent.state import JobDescription
    monkeypatch.setattr(M.layout, 'check', lambda _: SimpleNamespace(issues=[], pages=1, free_pt=0))
    resume = SimpleNamespace(roles=[object()], skills=[], tex='')
    jd = JobDescription(title='Engineer', company='Acme', responsibilities=['Build Python services.'])
    monkeypatch.setattr(T, 'duplicates', lambda *args, **kwargs: ['Repeated duty'])
    monkeypatch.setattr(T, 'quality', lambda *args: ['Missing concrete tool'])
    issues = M.hard_issues({'job_dir': tmp_path, 'r': resume, 'jd': jd}, filled())
    assert 'Repeated duty' in issues and 'Missing concrete tool' in issues
    assert any('coverage targets' in issue for issue in issues)


def test_original_backfill_and_zero_numbered_duties_survive_structured_file_roundtrip():
    from jobagent.state import JobDescription
    bullet = T.FillBullet(text='Built Python services, reducing errors by 20%.', duties=[], original=0)
    assert T.FillBullet.model_validate_json(bullet.model_dump_json()) == bullet
    schema = M._posting_bullet_schema(M.CompactFillBullet, JobDescription(title='Engineer', company='Acme'), 'NoNumberedDuties')
    assert schema(text=bullet.text, duties=[]).duties == []
    with pytest.raises(ValueError):
        schema(text=bullet.text, duties=[0])


def test_generic_tool_description_is_allowed_when_the_actual_stack_is_named():
    from jobagent.state import JobDescription
    role = SimpleNamespace(bullets=[], title='Engineer', company='Acme', dates='2026')
    template = SimpleNamespace(roles=[role], skills=[('Languages', 'Python, TensorFlow')])
    jd = JobDescription(title='Engineer', company='Acme', technical_skills=['Python', 'TensorFlow'])
    value = filled()
    value.roles = [T.FillRole(role=0, bullets=[T.FillBullet(text=text, duties=[]) for text in (
        'Developed AI tools using Python and TensorFlow, enhancing data processing efficiency by 35%.',
        'Built AI tools, reducing review time by 20%.')])]
    issues = T.quality(template, jd, value)
    assert not any('bullet 1' in issue for issue in issues)
    assert any('bullet 2' in issue and 'names no technology' in issue for issue in issues)


def review_world(tmp_path, monkeypatch):
    from jobagent.state import JobDescription
    from jobagent.tools import incremental_documents as I
    role = SimpleNamespace(title='Engineer', company='Acme', dates='2026', bullets=[])
    template = SimpleNamespace(tex=r'\section{Education}Master of Science.\section{Technical Skills}', roles=[role], skills=[])
    value = filled(); value.summary = 'Software Engineer II with Python experience and production delivery.'
    value.roles = [T.FillRole(role=0, bullets=[T.FillBullet(text=text, duties=[0]) for text in (
        'Built Python APIs, reducing latency by 20%.', 'Deployed Docker services, cutting failures by 30%.')])]
    value.skills = [T.SkillLine(category=name, terms=[term]) for name, term in (
        ('Languages', 'Python'), ('Cloud', 'AWS'), ('Monitoring', 'Prometheus'))]
    ctx = {'job_dir': tmp_path, 'r': template, 'jd': JobDescription(title='Software Engineer II', company='Acme'),
           'company': 'Acme builds software.', 'models': {'router': 'critic'}, 'log': lambda _: None}
    monkeypatch.setattr(I, 'review_model', lambda *args, **kwargs: object())
    return ctx, value


def test_independent_review_rejects_provably_false_critic_errors_without_dropping_other_findings(tmp_path, monkeypatch):
    from jobagent.tools import document_review as R
    ctx, value = review_world(tmp_path, monkeypatch)
    findings = [M.Finding(agent=agent, issue=issue, severity='error') for agent, issue in (
        ('summary', 'Does not open with Software Engineer II'), ('skills', 'Monitoring category is empty'),
        ('experience', 'The bullets do not repeat their employer name'))]
    def adjudicate(model, schema, messages, **kwargs):
        if 'findings' in schema.model_fields:
            return M.Review(findings=findings)
        data = json.loads(messages[-1].content)
        assert data['code_verified']['summary_title_prefix_matches'] is True
        assert data['sources']['role:0']['employer'] == 'Acme'
        return schema.model_validate({'verdicts': [{'finding': i, 'status': 'rejected', 'claim_id': claim,
            'source_id': source, 'reason': 'The supplied current document establishes this fact.'}
            for i, claim, source in ((0, 'summary', 'posting'), (1, 'skills', 'skills'), (2, 'experience', 'role:0'))]})
    monkeypatch.setattr(R.llm, 'structured_invoke', adjudicate)
    monkeypatch.setattr(M.llm, 'writer', lambda *args, **kwargs: object())
    assert M.content_review(ctx, value) == []


@pytest.mark.parametrize('status', ['confirmed', 'uncertain'])
def test_independent_review_preserves_real_and_unsettled_errors(tmp_path, monkeypatch, status):
    from jobagent.tools import document_review as R
    ctx, value = review_world(tmp_path, monkeypatch)
    finding = M.Finding(agent='skills', issue='A runtime is incorrectly categorized as a language', severity='error')
    def adjudicate(model, schema, messages, **kwargs):
        return schema.model_validate({'verdicts': [{'finding': 0, 'status': status, 'claim_id': 'skills',
            'source_id': 'skills', 'reason': 'Needs a concrete category correction.'}]})
    monkeypatch.setattr(R.llm, 'structured_invoke', adjudicate)
    result = R.adjudicate(ctx, value, [finding])
    assert len(result) == 1 and result[0].severity == 'error' and result[0].issue.startswith(status)


def test_independent_review_cannot_borrow_another_projects_source(tmp_path, monkeypatch):
    from jobagent.tools import document_review as R
    ctx, value = review_world(tmp_path, monkeypatch)
    state = {'kept': 2, 'short': [], 'ranked': [
        {'repo': f'owner/p{i}', 'title': f'Project {i}', 'paragraph': 'Built Python services.', 'stack': ['Python']}
        for i in range(2)]}
    (tmp_path/'projects.json').write_text(json.dumps(state))
    finding = M.Finding(agent='projects', issue='Unsupported implementation claim', severity='error')
    def adjudicate(model, schema, messages, **kwargs):
        return schema.model_validate({'verdicts': [{'finding': 0, 'status': 'rejected', 'claim_id': 'project:0',
            'source_id': 'repo:1', 'reason': 'The other repository supports it.'}]})
    monkeypatch.setattr(R.llm, 'structured_invoke', adjudicate)
    with pytest.raises(ValueError, match='own repository'):
        R.adjudicate(ctx, value, [finding])
