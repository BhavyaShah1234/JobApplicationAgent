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


def test_native_cli_letter_inherits_actual_fallback_strategy_and_resume_is_reusable(cli_world, monkeypatch):
    main, job, resumes, letters = cli_world
    original = M.run_job
    def fallback_resume(*args, **kwargs):
        return {**original(*args, **kwargs), 'document_strategy': 'current',
                'strategy_fallback': {'requested': 'incremental', 'used': 'current'}}
    monkeypatch.setattr(M, 'run_job', fallback_resume)
    command = ['tailor', str(job), 'acme', '--no-ats', '--document-strategy', 'incremental']
    first = CliRunner().invoke(main.app, command)
    assert first.exit_code == 1, first.output  # only the fake first letter is invalid
    assert resumes[0]['document_strategy'] == 'incremental' and not letters[0].get('incremental')
    second = CliRunner().invoke(main.app, command)
    assert second.exit_code == 0, second.output
    assert len(resumes) == 1 and len(letters) == 2 and not letters[1].get('incremental')


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
    value.roles = [T.FillRole(role=0, bullets=[T.FillBullet(text=text, duties=[0]) for text in (
        'Developed AI tools using Python and TensorFlow, enhancing data processing efficiency by 35%.',
        'Built AI tools, reducing review time by 20%.')])]
    issues = T.quality(template, jd, value)
    assert not any('bullet 1' in issue for issue in issues)
    assert any('bullet 2' in issue and 'names no technology' in issue for issue in issues)


def test_employer_name_is_not_a_technology_but_an_explicit_product_still_is():
    from jobagent.state import JobDescription
    template = T.parse(r'\section{Summary}Old.\section{Experience}\section{Projects}\section{Education}MS\section{Technical Skills}')
    value = filled()
    value.roles = [T.FillRole(role=0, bullets=[T.FillBullet(text=text, duties=[]) for text in (
        "Championed Samsara's cultural principles while scaling AI solutions globally.",
        'Integrated Samsara telematics, reducing reporting time by 20%.')])]
    jd = JobDescription(title='Engineer', company='Samsara')
    assert any('bullet 1' in issue and 'names no technology' in issue for issue in T.quality(template, jd, value))
    jd.technical_skills = ['Samsara telematics']
    assert not any('bullet 2' in issue for issue in T.quality(template, jd, value))


@pytest.mark.parametrize('incremental', [False, True])
def test_native_project_repair_can_correct_a_short_variant_and_rejects_truncation(tmp_path, monkeypatch, incremental):
    from jobagent.steps import ats as A
    from pydantic import ValidationError
    before = {'kept': 1, 'short': ['owner/source'], 'ranked': [{
        'repo': 'owner/source', 'title': 'Source Project', 'paragraph': 'Retain this full description.',
        'short': 'Built cloud automation. It ensures consistent, validated AWS'}]}
    (tmp_path / 'projects.json').write_text(json.dumps(before))
    monkeypatch.setattr(M.llm, 'writer', lambda *a, **kw: object())
    monkeypatch.setattr(M, '_context', lambda *a, **kw: 'Actual project sources')
    complete = ('Teams needed reliable cloud deployment. Built a Python and Terraform deployment system '
                'with validated configuration and repeatable infrastructure updates.')
    def generate(ctx, stage, model, schema, messages, **kwargs):
        with pytest.raises(ValidationError, match='complete sentence'):
            schema.model_validate({'projects': [{'project': 1, 'short': before['ranked'][0]['short']}]})
        return schema.model_validate({'projects': [{'project': 1, 'short': complete}]})
    monkeypatch.setattr(M, '_invoke', generate)
    _, out = M._run_agent({'job_dir': tmp_path, 'models': {'projects': 'fixture'},
                          'log': lambda _: None, 'incremental': incremental},
                         'projects', None, ['Repair project 1 unfinished displayed short description.'], '')
    assert out is not None
    after = json.loads(A.apply_project_fixes(json.dumps(before), out.projects))
    assert after['ranked'][0]['paragraph'] == before['ranked'][0]['paragraph']
    assert after['ranked'][0]['short'] == complete
    assert M.P.content_findings(after) == []


@pytest.mark.parametrize('incremental', [False, True])
def test_long_project_repair_keeps_complete_source_sentences_without_truncating_words(tmp_path, monkeypatch, incremental):
    from pydantic import ValidationError
    (tmp_path / 'projects.json').write_text('{}')
    monkeypatch.setattr(M.llm, 'writer', lambda *args, **kwargs: object())
    monkeypatch.setattr(M, '_context', lambda *args, **kwargs: 'The actual repository evidence')
    prefix = ('Engineering teams needed repeatable infrastructure deployment across development and production environments. '
              'Built a Python and Terraform platform with validated configuration, automated provisioning and reproducible updates.')
    long = prefix + ' It also supports ' + 'source verified deployment workflows and configuration audit logs ' * 4 + 'for distributed teams.'
    assert 420 < len(long) < 700
    def generate(ctx, stage, model, schema, messages, **kwargs):
        # A single overlong sentence cannot be turned into a fake complete one.
        with pytest.raises(ValidationError, match='no complete useful prefix'):
            schema.model_validate({'projects': [{'project': 1, 'paragraph': 'Infrastructure ' * 35 + 'deployment.'}]})
        return schema.model_validate({'projects': [{'project': 1, 'paragraph': long}]})
    monkeypatch.setattr(M, '_invoke', generate)
    _, out = M._run_agent({'job_dir': tmp_path, 'models': {'projects': 'fixture'},
                          'log': lambda _: None, 'incremental': incremental},
                         'projects', None, ['Shorten project 1 to fit, preserving its own facts.'], '')
    assert out is not None and out.projects[0].paragraph == prefix
    assert len(out.projects[0].paragraph) <= T.PARAGRAPH_MAX


@pytest.mark.parametrize('free_pt,needs_more_work', [(8, False), (44, True)])
def test_filled_two_project_page_does_not_demand_an_unnecessary_third_project(tmp_path, monkeypatch, free_pt, needs_more_work):
    from jobagent.state import JobDescription
    f = filled()
    f.summary = 'Engineer building Python services with reliable deployment and comprehensive automated testing.'
    f.skills = [T.SkillLine(category=c, terms=[v]) for c, v in
                (('Languages', 'Python'), ('Cloud', 'AWS'), ('Data', 'PostgreSQL'))]
    role = T.Role(header='', title='Engineer', company='Acme', bullets=[], span=(0, 0))
    template = SimpleNamespace(roles=[role], skills=[], tex='')
    f.roles = [T.FillRole(role=0, bullets=[T.FillBullet(text=text, duties=[]) for text in (
        'Built Python services, reducing response time by 20%.',
        'Deployed Docker workloads, improving recovery by 30%.',
        'Designed PostgreSQL indexes, cutting query costs by 25%.')])]
    entries = [{'repo': f'owner/repository-{i}', 'title': f'Readable Project {i}',
                'paragraph': 'Built Python services. Delivered reliable systems.'} for i in range(4)]
    (tmp_path / 'projects.json').write_text(json.dumps({'ranked': entries, 'kept': 2, 'short': []}))
    monkeypatch.setattr(M, 'render', lambda _: (f, '', [], 1, ''))
    monkeypatch.setattr(M.layout, 'check', lambda _: M.layout.Layout(pages=1, free_pt=free_pt))
    monkeypatch.setattr(M.layout, 'review', lambda *a, **kw: [])
    monkeypatch.setattr(M, '_reviewed_content', lambda *a: {'content_findings': [], 'content_error': ''})
    monkeypatch.setattr(M, 'summary_issues', lambda *a: [])
    checked = M.check_layout({'ctx': {'r': template, 'jd': JobDescription(title='Engineer', company='Acme'),
                            'job_dir': tmp_path, 'log': lambda _: None}, 'f': f})
    assert bool(checked['tasks'].get('projects')) is needs_more_work
    assert bool(checked['tasks'].get('experience')) is needs_more_work
    assert bool(checked['hard']) is needs_more_work
    assert M.after_layout(checked) == ('fix' if needs_more_work else 'score')


def test_failed_incremental_draft_uses_current_and_retains_failure_and_panel(tmp_path, monkeypatch):
    calls = []
    panel = ('atsfriendly', 'skillsfirst')
    def generate(job, slug, models, sites, log, **kwargs):
        calls.append((sites, kwargs))
        if len(calls) == 1:
            (job / 'generation-attempts.json').write_text('["unresolved coverage"]')
            (job / 'incremental-metrics-v1.json').write_text('{"elapsed_s":100}')
            return {'produced': False, 'failure': 'checks failed', 'hard_issues': ['unresolved coverage']}
        (job / 'generation-attempts.json').write_text('[]')
        return {'produced': True, 'document_strategy': 'current', 'hard_issues': []}
    monkeypatch.setattr(M, '_run_job', generate)
    result = M.run_job(tmp_path, 'company', {}, sites=panel, document_strategy='incremental', ats_refine=True, log=lambda _: None)
    assert len(calls) == 2 and all(sites == panel for sites, _ in calls)
    assert calls[1][1] == {'document_strategy': 'current', 'ats_refine': False, '_final_only': True}
    assert result['document_strategy'] == 'current' and result['produced']
    provenance = json.loads((tmp_path / 'document-fallback.json').read_text())
    assert provenance['requested'] == 'incremental' and provenance['used'] == 'current'
    assert provenance['ats_refine_not_applied'] is True
    archive = tmp_path / provenance['failed_attempt']
    assert json.loads((archive / 'generation-attempts.json').read_text()) == ['unresolved coverage']
    assert json.loads((archive / 'result.json').read_text())['hard_issues'] == ['unresolved coverage']


@pytest.mark.parametrize('strategy,changed,produced', [('current', False, False), ('incremental', True, False),
                                                    ('incremental', False, True)])
def test_current_failures_changed_sources_and_successful_drafts_do_not_fallback(tmp_path, monkeypatch, strategy, changed, produced):
    calls = []
    def generate(*args, **kwargs):
        calls.append(kwargs)
        return {'produced': produced, 'source_inputs_changed': changed}
    monkeypatch.setattr(M, '_run_job', generate)
    M.run_job(tmp_path, 'company', {}, sites=(), document_strategy=strategy)
    assert len(calls) == 1 and not (tmp_path / 'document-fallback.json').exists()


@pytest.mark.parametrize('error', [RuntimeError('programming defect'), M.llm.LLMConfigError('quota', 'config')])
def test_fallback_does_not_hide_programming_or_provider_configuration_errors(tmp_path, monkeypatch, error):
    calls = []
    def broken(*args, **kwargs):
        calls.append(1)
        raise error
    monkeypatch.setattr(M, '_run_job', broken)
    with pytest.raises(type(error)):
        M.run_job(tmp_path, 'company', {}, sites=(), document_strategy='incremental')
    assert calls == [1] and not (tmp_path / 'document-fallback.json').exists()


def test_fallback_generation_defers_entire_requested_panel_until_the_final_resume(tmp_path, monkeypatch):
    from jobagent.steps import tailor as single
    from jobagent.state import JobDescription
    data, state, job = (tmp_path / name for name in ('data', 'state', 'job'))
    (data / 'templates').mkdir(parents=True)
    (state / 'companies').mkdir(parents=True)
    job.mkdir()
    (data / 'templates/resume.tex').write_text('Template')
    (state / 'companies/acme.md').write_text('Company research')
    (job / 'jd.json').write_text(JobDescription(title='Engineer', company='Acme').model_dump_json())
    (job / 'resume.pdf').write_bytes(b'Validated graph output')
    f = filled()
    best = {'f': f, 'scores': {}, 'hard': []}
    monkeypatch.setattr(M, 'get_settings', lambda: SimpleNamespace(data_dir=data, state_dir=state))
    monkeypatch.setattr(D, 'validate_template', lambda _: None)
    monkeypatch.setattr(T, 'parse', lambda _: SimpleNamespace(tex='', roles=[], skills=[]))
    monkeypatch.setattr(M.SG, 'for_tailor', lambda *args: {})
    monkeypatch.setattr(M.SG, 'guard_resume', lambda *args: None)
    monkeypatch.setattr(M.SG, 'violations', lambda *args: [])
    contexts = []
    def graph_output(initial, config):
        contexts.append(initial['ctx'])
        return {'best': best, 'history': []}
    monkeypatch.setattr(M, 'build_graph', lambda: SimpleNamespace(invoke=graph_output))
    monkeypatch.setattr(M, '_version_files', lambda _: {})
    monkeypatch.setattr(M.bundle, 'publish', lambda *args: None)
    monkeypatch.setattr(M.layout, 'page_count', lambda _: 1)
    monkeypatch.setattr(M.layout, 'check', lambda _: M.layout.Layout(1, 8))
    monkeypatch.setattr(single, 'report', lambda *args: 'Review')
    scans = []
    def evaluate(*args):
        scans.append(args[4])
        return {'scores': {'atsfriendly': 81.0}}
    monkeypatch.setattr(M.A, 'evaluate', evaluate)
    monkeypatch.setattr(M.ats_sites, 'write_summary', lambda job, scores, sites: {
        'average': 81.0, 'scores': scores, 'sites': list(sites), 'missing': [], 'complete': True})
    panel = ('atsfriendly', 'skillsfirst')
    result = M._run_job(job, 'acme', {}, sites=panel, log=lambda _: None,
                        document_strategy='current', _final_only=True)
    assert contexts[0]['sites'] == () and scans == [panel]
    assert result['produced'] and result['scores'] == {'atsfriendly': 81.0}


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


def test_independent_project_reader_sees_the_same_readme_evidence_as_the_writer(tmp_path, monkeypatch):
    from jobagent.tools import document_review as R
    ctx, value = review_world(tmp_path, monkeypatch)
    readme = 'The Python service supports batches of 1000 events with validated configuration.'
    ctx['cards'] = {'owner/service': {'full_name': 'owner/service', 'summary': 'A Python service.', 'readme': readme}}
    (tmp_path / 'projects.json').write_text(json.dumps({'kept': 1, 'short': [], 'ranked': [{
        'repo': 'owner/service', 'title': 'Event Service', 'stack': ['Python'],
        'paragraph': 'Processed batches of 1000 events using Python.'}]}))
    def adjudicate(model, schema, messages, **kwargs):
        payload = json.loads(messages[-1].content)
        assert payload['sources']['repo:0']['readme'] == readme
        assert M.P.SOURCE_POLICY in messages[0].content
        return schema.model_validate({'verdicts': [{'finding': 0, 'status': 'rejected',
            'claim_id': 'project:0', 'source_id': 'repo:0', 'reason': 'The supplied README establishes the batch size.'}]})
    monkeypatch.setattr(R.llm, 'structured_invoke', adjudicate)
    finding = M.Finding(agent='projects', issue='Batch size is absent from the summary.', severity='error')
    assert R.adjudicate(ctx, value, [finding]) == []


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
