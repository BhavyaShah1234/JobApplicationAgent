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
        lambda draft, *args, **kwargs: [] if draft.salutation == 'Two pages' else ['repair one', 'repair two'], compile_pdf)
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
    letter_world(tmp_path, monkeypatch, [letter('Dear Team')], lambda *args, **kwargs: [], compile_pdf, header=False)
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
    complete = ('Teams needed reliable cloud deployment. Built a Python and Terraform deployment system '
                'with validated configuration and repeatable infrastructure updates.')
    # fixture contract (source-sentence binding): the project carries the saved card its sentences come from
    source = {'full_name': 'owner/source', 'title': 'Source Project', 'summary': complete + ' It ensures consistent, validated AWS environments for every team.'}
    full = 'Retain this full description of the platform. ' * 5
    before = {'kept': 1, 'short': ['owner/source'], 'evidence': {'owner/source': source}, 'ranked': [{
        'repo': 'owner/source', 'title': 'Source Project', 'paragraph': full.strip(),
        'short': 'Built cloud automation. It ensures consistent, validated AWS'}]}
    (tmp_path / 'projects.json').write_text(json.dumps(before))
    monkeypatch.setattr(M.llm, 'writer', lambda *a, **kw: object())
    monkeypatch.setattr(M, '_context', lambda *a, **kw: 'Actual project sources')
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
    assert 'short' not in ' '.join(M.P.binding_findings({**after, 'ranked': [{**after['ranked'][0], 'paragraph': complete}]}))


@pytest.mark.parametrize('incremental', [False, True])
def test_long_project_repair_keeps_complete_source_sentences_without_truncating_words(tmp_path, monkeypatch, incremental):
    from pydantic import ValidationError
    prefix = ('Engineering teams needed repeatable infrastructure deployment across development and production environments. '
              'Built a Python and Terraform platform with validated configuration, automated provisioning and reproducible updates.')
    overlong = 'Infrastructure ' * 35 + 'deployment.'
    tail = 'It also supports ' + 'source verified deployment workflows and configuration audit logs ' * 4 + 'for distributed teams.'
    # fixture contract (source-sentence binding): the repair chooses among the saved card's numbered sentences
    card = {'full_name': 'owner/source', 'title': 'Source Project', 'summary': f'{prefix} {tail} {overlong}'}
    state = {'kept': 1, 'short': [], 'evidence': {'owner/source': card},
             'ranked': [{'repo': 'owner/source', 'title': 'Source Project', 'paragraph': prefix, 'short': ''}]}
    (tmp_path / 'projects.json').write_text(json.dumps(state))
    monkeypatch.setattr(M.llm, 'writer', lambda *args, **kwargs: object())
    monkeypatch.setattr(M, '_context', lambda *args, **kwargs: 'The actual repository evidence')
    assert 420 < len(prefix + ' ' + tail) < 700 and len(overlong) > 470
    def generate(ctx, stage, model, schema, messages, **kwargs):
        # A single overlong sentence cannot be turned into a fake complete one.
        with pytest.raises(ValidationError, match='no complete useful prefix'):
            schema.model_validate({'projects': [{'project': 1, 'paragraph_sentences': [4]}]})
        # arbitrary prose, even prose that looks like the sources, is not a selection
        with pytest.raises(ValidationError, match='complete sentence'):
            schema.model_validate({'projects': [{'project': 1, 'paragraph': prefix + ' It also supports source verified deployment workflows.'}]})
        return schema.model_validate({'projects': [{'project': 1, 'paragraph_sentences': [1, 2, 3]}]})
    monkeypatch.setattr(M, '_invoke', generate)
    _, out = M._run_agent({'job_dir': tmp_path, 'models': {'projects': 'fixture'},
                          'log': lambda _: None, 'incremental': incremental},
                         'projects', None, ['Shorten project 1 to fit, preserving its own facts.'], '')
    assert out is not None and out.projects[0].paragraph == prefix          # the over-limit third sentence is left out whole, never cut
    assert len(out.projects[0].paragraph) <= M.P.FULL_LIMIT


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
    # fixture contract (source-sentence binding): the descriptions are whole sentences of each project's own saved card
    prose = 'Built Python services for the platform team. Delivered reliable systems with automated tests.'
    entries = [{'repo': f'owner/repository-{i}', 'title': f'Readable Project {i}', 'paragraph': prose} for i in range(4)]
    (tmp_path / 'projects.json').write_text(json.dumps({
        'ranked': entries, 'kept': 2, 'short': [],
        'evidence': {e['repo']: {'full_name': e['repo'], 'title': e['title'], 'summary': prose} for e in entries}}))
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


def test_employer_lab_name_is_not_academic_wording(tmp_path, monkeypatch):
    ctx, value = review_world(tmp_path, monkeypatch)
    ctx['r'].roles[0].company = 'Logos Robotics Lab'
    value.roles[0].bullets = [T.FillBullet(text=text, duties=[0]) for text in (
        'Built Playwright suites, lifting coverage by 20% at Logos Robotics Lab.',
        'Analyzed Jenkins logs, reducing failures by 15% at Logos Robotics Lab.')]
    assert T.quality(ctx['r'], ctx['jd'], value) == []
    assert T.duplicates(ctx['r'], value, substantive=True) == []


def test_employer_name_exception_retains_real_academic_wording(tmp_path, monkeypatch):
    ctx, value = review_world(tmp_path, monkeypatch)
    ctx['r'].roles[0].company = 'Logos Robotics Lab'
    value.roles[0].bullets[0].text = 'Built Python tools for unpaid student teams at Logos Robotics Lab, reducing errors.'
    assert any('academic/unpaid' in issue for issue in T.quality(ctx['r'], ctx['jd'], value))
    ctx['r'].roles[0].company = 'Acme'
    value.roles[0].bullets[0].text = 'Built Python tools at Another Robotics Lab, reducing errors.'
    assert any('academic/unpaid' in issue for issue in T.quality(ctx['r'], ctx['jd'], value))


def test_repeated_employer_identity_does_not_make_distinct_duties_duplicates(tmp_path, monkeypatch):
    ctx, value = review_world(tmp_path, monkeypatch)
    ctx['r'].roles[0].company = 'Logos Robotics Lab'
    value.roles[0].bullets = [T.FillBullet(text=text, duties=[0]) for text in (
        'Built Playwright suites, lifting coverage by 20% at Logos Robotics Lab.',
        'Analyzed Jenkins logs, reducing failures by 15% at Logos Robotics Lab.')]
    assert T.duplicates(ctx['r'], value, substantive=True) == []


def test_employer_name_exception_retains_repeated_duties(tmp_path, monkeypatch):
    ctx, value = review_world(tmp_path, monkeypatch)
    ctx['r'].roles[0].company = 'Logos Robotics Lab'
    value.roles[0].bullets = [T.FillBullet(text=text, duties=[0]) for text in (
        'Built Python regression suites to reduce release failures at Logos Robotics Lab.',
        'Built Python regression suites to reduce release failures.')]
    assert T.duplicates(ctx['r'], value, substantive=True)


@pytest.mark.parametrize('status', ['confirmed', 'uncertain'])
def test_protected_template_date_concerns_are_input_advisories_not_rewrite_targets(tmp_path, monkeypatch, status):
    from jobagent.tools import document_review as R
    ctx, value = review_world(tmp_path, monkeypatch)
    ctx['r'].roles[0].dates = 'January 2026 -- Present'
    original = ctx['r'].roles[0].dates
    finding = M.Finding(agent='experience', severity='error', issue='The original role dates overlap another role.')
    def adjudicate(model, schema, messages, **kwargs):
        data = json.loads(messages[-1].content)
        assert data['claims']['role-header:0']['dates'] == original
        return schema.model_validate({'verdicts':[{'finding':0, 'status':status,
            'claim_id':'role-header:0', 'source_id':'role:0', 'reason':'Concern about the original dates.'}]})
    monkeypatch.setattr(R.llm, 'structured_invoke', adjudicate)
    result = R.adjudicate(ctx, value, [finding])
    assert len(result) == 1 and result[0].severity == 'suggestion'
    assert 'source input' in result[0].issue.lower()
    assert ctx['r'].roles[0].dates == original


def test_protected_header_cannot_borrow_a_different_source(tmp_path, monkeypatch):
    from jobagent.tools import document_review as R
    ctx, value = review_world(tmp_path, monkeypatch)
    finding = M.Finding(agent='experience', severity='error', issue='Dates need checking.')
    monkeypatch.setattr(R.llm, 'structured_invoke', lambda model, schema, messages, **kwargs:
        schema.model_validate({'verdicts':[{'finding':0, 'status':'confirmed',
            'claim_id':'role-header:0', 'source_id':'posting', 'reason':'Posting dates differ.'}]}))
    with pytest.raises(ValueError, match='own template role'):
        R.adjudicate(ctx, value, [finding])


def test_generated_tenure_claim_still_blocks_against_protected_dates(tmp_path, monkeypatch):
    from jobagent.tools import document_review as R
    ctx, value = review_world(tmp_path, monkeypatch)
    value.roles[0].bullets[0].text = 'Built Python APIs over ten years, reducing errors.'
    finding = M.Finding(agent='experience', severity='error', issue='The generated ten-year tenure conflicts with source dates.')
    monkeypatch.setattr(R.llm, 'structured_invoke', lambda model, schema, messages, **kwargs:
        schema.model_validate({'verdicts':[{'finding':0, 'status':'confirmed',
            'claim_id':'experience', 'source_id':'role:0', 'reason':'Generated duration is unsupported.'}]}))
    result = R.adjudicate(ctx, value, [finding])
    assert len(result) == 1 and result[0].severity == 'error'


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


def test_late_policy_writer_and_review_facts_share_estimate_permission(monkeypatch):
    monkeypatch.setattr(T, 'declared_claims', lambda: {'years_experience': 5})
    facts = T.facts_block()
    assert 'estimated' in facts and 'confirmation' in facts
    role = SimpleNamespace(pool=[], bullets=[], title='Engineer', company='Acme', dates='2026')
    resume = SimpleNamespace(roles=[role], skills=[], summary='Prior summary')
    messages = T.build_fill_prompt(resume, M.JobDescription(title='Engineer', company='Acme'), '', 4)
    assert sum(message.content.count(T.WORK_DRAFT_POLICY) for message in messages) == 1
    assert T.WORK_DRAFT_POLICY in facts
    assert 'employers' in facts and 'credentials' in facts and 'repository sources' in facts


def test_late_policy_review_receives_role_bound_estimate_metadata(tmp_path, monkeypatch):
    from jobagent.tools import document_review as R
    ctx, value = review_world(tmp_path, monkeypatch)
    value.roles[0].bullets[0].estimated = ['20%']
    def adjudicate(model, schema, messages, **kwargs):
        data = json.loads(messages[-1].content)
        assert data['sources']['role:0']['draft_impact_estimates'] == [{'bullet': 1, 'values': ['20%']}]
        assert T.WORK_DRAFT_POLICY in data['sources']['owner_policy']
        return schema.model_validate({'verdicts': [{'finding': 0, 'status': 'rejected',
            'claim_id': 'experience', 'source_id': 'owner_policy',
            'reason': 'This tagged draft estimate follows the supplied policy; it still needs candidate confirmation.'}]})
    monkeypatch.setattr(R.llm, 'structured_invoke', adjudicate)
    finding = M.Finding(agent='experience', issue='The source original lacks the 20% measurement.', severity='error')
    assert R.adjudicate(ctx, value, [finding]) == []
    context = M._context(ctx, value, None, visible_only=True)
    assert 'ESTIMATE METADATA (not displayed; requires candidate confirmation)' in context
    assert '"estimated": ["20%"]' in context
    repair = T.build_repair_prompt(ctx['r'], ctx['jd'], value, ['A proposed measurement issue'])
    assert T.WORK_DRAFT_POLICY in repair[0].content
    assert "estimated impact awaiting confirmation: ['20%']" in repair[1].content


def test_late_policy_scoped_experience_and_summary_receive_estimate_metadata(tmp_path, monkeypatch):
    from jobagent.tools import incremental_documents as I
    from jobagent.tools.artifact_cache import ArtifactCache
    ctx, value = review_world(tmp_path, monkeypatch)
    ctx['cache'] = ArtifactCache(tmp_path / 'cache')
    value.roles[0].bullets[0].estimated = ['20%']
    seen = {}
    def invoke(cache, stage, model_name, model, schema, messages, **kwargs):
        seen[stage] = json.loads(messages[-1].content)
        return schema.model_validate({'findings': []})
    monkeypatch.setattr(I, 'invoke', invoke)
    assert I.review(ctx, value) == []
    for section in ('experience', 'summary'):
        assert seen['review:' + section]['roles'][0]['draft_impact_estimates'] == [
            {'bullet': 1, 'values': ['20%']}]


def test_late_policy_estimate_only_change_invalidates_a_previous_review(tmp_path, monkeypatch):
    ctx, value = review_world(tmp_path, monkeypatch)
    calls = []
    def review(context, current):
        estimates = current.roles[0].bullets[0].estimated
        calls.append(list(estimates))
        return [] if estimates else [M.Finding(agent='experience', severity='error', issue='Metric lacks an estimate tag.')]
    monkeypatch.setattr(M, 'content_review', review)
    first = M._reviewed_content(ctx, value, {})
    assert first['semantic_hard']
    value.roles[0].bullets[0].estimated = ['20%']
    second = M._reviewed_content(ctx, value, first)
    assert calls == [[], ['20%']] and not second['semantic_hard']


def _late_policy_summary_case(tmp_path, monkeypatch, claim, extra_roles=()):
    from jobagent.tools import incremental_documents as I
    from jobagent.tools.artifact_cache import ArtifactCache
    payload = {'summary': claim + '.', 'roles': [
        {'employer': 'Acme', 'bullets': ['Built SaaS products and SQL services using Python.']}, *extra_roles],
        'projects': [], 'skills': [], 'education': '', 'company_context': '', 'owner_claims_policy': T.facts_block()}
    finding = I.SummaryFinding(kind='employment', claim_quote=claim, issue='A proposed employment contradiction.', severity='error')
    verdict = I.SummaryVerdict(finding=0, status='rejected', claim_quote=claim, source_id='role:0',
        source_quote='Built SaaS products and SQL services using Python.', reason='Checked the actual source and owner policy.')
    monkeypatch.setattr(I, 'review_model', lambda *args, **kwargs: object())
    monkeypatch.setattr(I, 'invoke', lambda *args, **kwargs: I.SummaryVerdicts(verdicts=[verdict]))
    ctx = {'models': {'router': 'judge'}, 'cache': ArtifactCache(tmp_path), 'log': lambda _: None}
    return I, ctx, payload, finding


@pytest.mark.parametrize('claim', [
    'Candidate with experience in functional and automation testing for SaaS products',
    'Built Python tooling for SQL databases'])
def test_late_policy_domain_subject_is_not_a_named_employer(tmp_path, monkeypatch, claim):
    I, ctx, payload, finding = _late_policy_summary_case(tmp_path, monkeypatch, claim)
    assert I.adjudicate_summary(ctx, payload, [finding]) == []


@pytest.mark.parametrize('claim', ['Worked at Google', 'Working for Google', 'Engineer at Google', 'Employed by Google'])
def test_late_policy_unknown_employer_cannot_borrow_a_role(tmp_path, monkeypatch, claim):
    I, ctx, payload, finding = _late_policy_summary_case(tmp_path, monkeypatch, claim)
    with pytest.raises(ValueError, match='another employer'):
        I.adjudicate_summary(ctx, payload, [finding])


def test_late_policy_named_other_employer_cannot_borrow_a_role(tmp_path, monkeypatch):
    I, ctx, payload, finding = _late_policy_summary_case(tmp_path, monkeypatch, 'Delivered software for Beta',
        [{'employer': 'Beta', 'bullets': ['Built another service.']}])
    with pytest.raises(ValueError, match='another employer'):
        I.adjudicate_summary(ctx, payload, [finding])


def test_late_policy_own_employer_affiliation_keeps_its_source(tmp_path, monkeypatch):
    I, ctx, payload, finding = _late_policy_summary_case(tmp_path, monkeypatch, 'Worked at Acme')
    assert I.adjudicate_summary(ctx, payload, [finding]) == []


def test_late_policy_company_name_is_not_a_substring_of_metadata(tmp_path, monkeypatch):
    I, ctx, payload, finding = _late_policy_summary_case(tmp_path, monkeypatch, 'Built metadata pipelines using Python',
        [{'employer': 'Meta', 'bullets': ['Built a different service.']}])
    assert I.adjudicate_summary(ctx, payload, [finding]) == []


@pytest.mark.parametrize('verb', ['Enhanced', 'Enhancing', 'Enhances'])
def test_late_policy_enhancement_is_a_stated_qualitative_result(tmp_path, monkeypatch, verb):
    ctx, value = review_world(tmp_path, monkeypatch)
    value.roles[0].bullets[0].text = f'{verb} service uptime using Python.'
    assert not any('states no result' in issue for issue in T.quality(ctx['r'], ctx['jd'], value))


def test_late_policy_an_activity_without_an_outcome_still_fails(tmp_path, monkeypatch):
    ctx, value = review_world(tmp_path, monkeypatch)
    value.roles[0].bullets[0].text = 'Worked on Python services.'
    assert any('states no result' in issue for issue in T.quality(ctx['r'], ctx['jd'], value))



def test_a_summary_names_its_own_employer_without_the_legal_suffix(tmp_path, monkeypatch):
    """`Anton Intelligence LLC` is written `Anton Intelligence` in prose: the candidate's own employer must not be read as another one."""
    I, ctx, payload, finding = _late_policy_summary_case(tmp_path, monkeypatch, 'Software engineer at Acme Technologies')
    payload['roles'][0]['employer'] = 'Acme Technologies Inc'
    assert I.adjudicate_summary(ctx, payload, [finding]) == []


def test_another_employer_is_recognised_without_its_legal_suffix(tmp_path, monkeypatch):
    I, ctx, payload, finding = _late_policy_summary_case(tmp_path, monkeypatch, 'Delivered software for Beta Industries',
        [{'employer': 'Beta Industries LLC', 'bullets': ['Built another service.']}])
    with pytest.raises(ValueError, match='another employer'):
        I.adjudicate_summary(ctx, payload, [finding])



def _project_world(tmp_path, monkeypatch, paragraph):
    from jobagent.tools import document_review as R
    ctx, value = review_world(tmp_path, monkeypatch)
    ctx['cards'] = {'ak-asu/cloudforge': {'full_name': 'ak-asu/cloudforge', 'summary': 'A platform that turns requirements into AWS infrastructure.', 'readme': 'Architecture only.'}}
    (tmp_path / 'projects.json').write_text(json.dumps({'kept': 1, 'short': [], 'ranked': [{
        'repo': 'ak-asu/cloudforge', 'title': 'CloudForge', 'stack': ['Python'], 'paragraph': paragraph}]}))
    return R, ctx, value


def _verdict(finding, status, reason, claim_id='project:0', source_id='repo:0'):
    return {'verdicts': [{'finding': finding, 'status': status, 'claim_id': claim_id, 'source_id': source_id, 'reason': reason}]}


def test_the_adjudicator_gets_a_code_verified_inventory_of_the_figures_each_finding_cites(tmp_path, monkeypatch):
    R, ctx, value = _project_world(tmp_path, monkeypatch, 'Turns plain-English requirements into deployed AWS infrastructure.')
    seen = []
    def verdict(model, schema, messages, **kwargs):
        seen.append(json.loads(messages[-1].content)['code_verified']['finding_figures'])
        return schema.model_validate(_verdict(0, 'rejected', 'The paragraph has no such figure.'))
    monkeypatch.setattr(R.llm, 'structured_invoke', verdict)
    finding = M.Finding(agent='projects', severity='error', issue="The description for 'CloudForge' mentions a 30% reduction in deployment time.")
    assert R.adjudicate(ctx, value, [finding]) == []
    assert seen == [[{'finding': 0, 'cited': ['30%'], 'absent_from_section': ['30%']}]]


def test_a_confirmation_that_rests_on_a_figure_absent_from_the_section_is_refused(tmp_path, monkeypatch):
    """The saved Samsara attempt: 'CloudForge mentions a 30% reduction' was confirmed while the 30% sat in Experience and the project text was qualitative."""
    R, ctx, value = _project_world(tmp_path, monkeypatch, 'Turns plain-English requirements into deployed AWS infrastructure.')
    monkeypatch.setattr(R.llm, 'structured_invoke', lambda model, schema, messages, **kw: schema.model_validate(
        _verdict(0, 'confirmed', 'The description mentions a 30% reduction in deployment time that the repository does not give.')))
    finding = M.Finding(agent='projects', severity='error', issue="The description for 'CloudForge' mentions a 30% reduction in deployment time.")
    with pytest.raises(ValueError, match='not in the section it accuses'):
        R.adjudicate(ctx, value, [finding])


def test_a_mixed_finding_keeps_its_genuine_concern_when_the_reason_does_not_rely_on_the_stray_figure(tmp_path, monkeypatch):
    R, ctx, value = _project_world(tmp_path, monkeypatch, 'Turns requirements into deployed AWS infrastructure with Kubernetes autoscaling.')
    monkeypatch.setattr(R.llm, 'structured_invoke', lambda model, schema, messages, **kw: schema.model_validate(
        _verdict(0, 'confirmed', 'Kubernetes autoscaling is not shown anywhere in the repository evidence.')))
    finding = M.Finding(agent='projects', severity='error',
                        issue='Kubernetes autoscaling is unsupported; the repository only documents a 30% CPU benchmark.')
    result = R.adjudicate(ctx, value, [finding])
    assert len(result) == 1 and result[0].severity == 'error' and 'Kubernetes' in result[0].issue


def test_a_finding_citing_a_figure_that_is_on_the_page_is_not_restricted(tmp_path, monkeypatch):
    R, ctx, value = _project_world(tmp_path, monkeypatch, 'Cut deployment time by 30% with generated infrastructure.')
    monkeypatch.setattr(R.llm, 'structured_invoke', lambda model, schema, messages, **kw: schema.model_validate(
        _verdict(0, 'confirmed', 'The 30% reduction is not in the repository evidence.')))
    finding = M.Finding(agent='projects', severity='error', issue='CloudForge claims a 30% reduction that its repository does not give.')
    assert len(R.adjudicate(ctx, value, [finding])) == 1


def test_a_rejection_is_never_refused_for_mentioning_a_figure(tmp_path, monkeypatch):
    R, ctx, value = _project_world(tmp_path, monkeypatch, 'Turns requirements into deployed AWS infrastructure.')
    monkeypatch.setattr(R.llm, 'structured_invoke', lambda model, schema, messages, **kw: schema.model_validate(
        _verdict(0, 'rejected', 'There is no 30% claim in this paragraph.')))
    finding = M.Finding(agent='projects', severity='error', issue='CloudForge shows a 30% reduction.')
    assert R.adjudicate(ctx, value, [finding]) == []


@pytest.mark.parametrize('text', ['M.S. in Robotics from ASU with Python.', 'MS in Computer Science, building ML systems.', 'B.Tech in Electronics and 3 years of ML work.',
                                   'MBA and software engineer.', 'B.E. in Mechanical Engineering.', 'Ph.D. candidate.', 'Master of Science graduate.', 'Holds a GPA of 4.0.'])
def test_the_summary_reviewer_gets_the_education_source_for_degree_abbreviations(text):
    from jobagent.tools import incremental_documents as I
    assert I.names_a_credential(text), text


@pytest.mark.parametrize('text', ['Built services as a platform engineer.', 'Used ms-level timers and be-spoke tooling.', 'Python engineer shipping ML systems at scale.',
                                   'Designed a REST API in as little as two sprints.'])
def test_ordinary_words_are_not_read_as_degrees(text):
    from jobagent.tools import incremental_documents as I
    assert not I.names_a_credential(text), text



def _credential_case(tmp_path, monkeypatch, claim, education):
    from jobagent.tools import incremental_documents as I
    from jobagent.tools.artifact_cache import ArtifactCache
    payload = {'summary': claim + '.', 'roles': [{'employer': 'Acme', 'bullets': ['Built Python services.']}], 'projects': [], 'skills': [],
               'education': education, 'company_context': '', 'owner_claims_policy': T.facts_block()}
    finding = I.SummaryFinding(kind='credential', claim_quote=claim, issue='A proposed credential contradiction.', severity='error')
    verdict = I.SummaryVerdict(finding=0, status='rejected', claim_quote=claim, source_id='education', source_quote=education.split(',')[0],
                               reason='The education source supports the credential.')
    monkeypatch.setattr(I, 'review_model', lambda *args, **kwargs: object())
    monkeypatch.setattr(I, 'invoke', lambda *args, **kwargs: I.SummaryVerdicts(verdicts=[verdict]))
    return I, {'models': {'router': 'judge'}, 'cache': ArtifactCache(tmp_path), 'log': lambda _: None}, payload, finding


@pytest.mark.parametrize('claim,education', [
    ('Master\'s degree in Robotics', 'M.S. in Robotics, Arizona State University'),
    ('MS in Robotics', 'Master of Science in Robotics, Arizona State University'),
    ('B.Tech in Electronics', 'Bachelor of Technology in Electronics, Example University'),
    ('PhD in Robotics', 'Ph.D. in Robotics, Example University')])
def test_equivalent_spellings_of_the_same_degree_support_the_credential(tmp_path, monkeypatch, claim, education):
    I, ctx, payload, finding = _credential_case(tmp_path, monkeypatch, claim, education)
    assert I.adjudicate_summary(ctx, payload, [finding]) == []


@pytest.mark.parametrize('claim,education', [
    ('M.S. in Robotics', 'B.S. in Physics, Arizona State University'),
    ('Master of Science in Robotics', 'Bachelor of Science in Robotics, Arizona State University'),
    ('MBA from Example University', 'B.Tech in Electronics, Example University'),
    ('Ph.D. in Robotics', 'M.S. in Robotics, Arizona State University')])
def test_a_different_degree_level_cannot_support_the_credential(tmp_path, monkeypatch, claim, education):
    I, ctx, payload, finding = _credential_case(tmp_path, monkeypatch, claim, education)
    with pytest.raises(ValueError, match='different degree'):
        I.adjudicate_summary(ctx, payload, [finding])


# --- future interest is not employment ------------------------------------------------------------------------------------------

@pytest.mark.parametrize('claim', ['Eager to bring Python experience to Beta', 'Looking for a role at Beta', 'Excited to join Beta and build data tools',
                                    'Motivated to contribute to Beta as an engineer', 'Ready to apply Python and SQL at Beta'])
def test_genuine_future_interest_in_the_target_company_is_not_an_employment_claim(tmp_path, monkeypatch, claim):
    I, ctx, payload, finding = _late_policy_summary_case(tmp_path, monkeypatch, claim)
    payload['target_job_identity'] = {'company': 'Beta'}
    assert I.adjudicate_summary(ctx, payload, [finding]) == []


@pytest.mark.parametrize('claim', ['Worked at Beta building data tools', 'Eager to join Beta after working at Google', 'Engineer for Beta with Python and SQL'])
def test_a_past_or_present_affiliation_with_the_target_or_another_company_still_cannot_borrow_a_role(tmp_path, monkeypatch, claim):
    I, ctx, payload, finding = _late_policy_summary_case(tmp_path, monkeypatch, claim)
    payload['target_job_identity'] = {'company': 'Beta'}
    with pytest.raises(ValueError, match='another employer'):
        I.adjudicate_summary(ctx, payload, [finding])



def test_a_valid_reason_that_discusses_a_source_figure_is_not_refused(tmp_path, monkeypatch):
    """A mixed finding confirmed because of the feature, with the reason mentioning the source's own 30% benchmark."""
    R, ctx, value = _project_world(tmp_path, monkeypatch, 'Turns requirements into deployed AWS infrastructure with Kubernetes autoscaling.')
    for reason in ("Kubernetes is unsupported; the source's 30% benchmark concerns CPU use, not autoscaling.",
                   'The README documents a 30% benchmark that the paragraph does not mention, but no autoscaling.'):
        monkeypatch.setattr(R.llm, 'structured_invoke', lambda model, schema, messages, reason=reason, **kw: schema.model_validate(_verdict(0, 'confirmed', reason)))
        finding = M.Finding(agent='projects', severity='error', issue='Kubernetes autoscaling is unsupported; the repository only documents a 30% CPU benchmark.')
        assert len(R.adjudicate(ctx, value, [finding])) == 1, reason


@pytest.mark.parametrize('reason', ['The paragraph claims 30% faster deploys, which no source supports.', "The project text states a 30% reduction that is not in the repository.",
                                     "The displayed description mentions a 30% reduction in deployment time."])
def test_a_reason_that_charges_the_displayed_text_with_an_absent_figure_is_refused(tmp_path, monkeypatch, reason):
    R, ctx, value = _project_world(tmp_path, monkeypatch, 'Turns requirements into deployed AWS infrastructure.')
    monkeypatch.setattr(R.llm, 'structured_invoke', lambda model, schema, messages, **kw: schema.model_validate(_verdict(0, 'confirmed', reason)))
    finding = M.Finding(agent='projects', severity='error', issue='CloudForge shows a 30% reduction in deployment time.')
    with pytest.raises(ValueError, match='not in the section it accuses'):
        R.adjudicate(ctx, value, [finding])


def test_a_rejection_of_an_experience_or_skills_error_cannot_rest_on_company_facts(tmp_path, monkeypatch):
    from jobagent.tools import document_review as R
    ctx, value = review_world(tmp_path, monkeypatch)
    for agent, claim in (('experience', 'experience'), ('skills', 'skills')):
        finding = M.Finding(agent=agent, severity='error', issue='A listed technology is not supported by the role.')
        monkeypatch.setattr(R.llm, 'structured_invoke', lambda model, schema, messages, claim=claim, **kw: schema.model_validate(
            {'verdicts': [{'finding': 0, 'status': 'rejected', 'claim_id': claim, 'source_id': 'company', 'reason': 'The company builds this.'}]}))
        with pytest.raises(ValueError, match='Company facts cannot establish'):
            R.adjudicate(ctx, value, [finding])
        monkeypatch.setattr(R.llm, 'structured_invoke', lambda model, schema, messages, claim=claim, **kw: schema.model_validate(
            {'verdicts': [{'finding': 0, 'status': 'rejected', 'claim_id': claim, 'source_id': 'owner_policy', 'reason': 'The owner policy permits it.'}]}))
        assert R.adjudicate(ctx, value, [finding]) == []


def test_company_context_and_the_posting_title_still_settle_summary_findings(tmp_path, monkeypatch):
    from jobagent.tools import document_review as R
    ctx, value = review_world(tmp_path, monkeypatch)
    finding = M.Finding(agent='summary', severity='error', issue='The summary mission claim is unsupported.')
    monkeypatch.setattr(R.llm, 'structured_invoke', lambda model, schema, messages, **kw: schema.model_validate(
        {'verdicts': [{'finding': 0, 'status': 'rejected', 'claim_id': 'summary', 'source_id': 'company', 'reason': 'It restates the company mission.'}]}))
    assert R.adjudicate(ctx, value, [finding]) == []


def test_a_run_that_raises_does_not_leave_an_older_fallback_record_behind(tmp_path, monkeypatch):
    (tmp_path / 'document-fallback.json').write_text('{"used": "current"}')
    monkeypatch.setattr(M, '_run_job', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('provider down')))
    with pytest.raises(RuntimeError):
        M.run_job(tmp_path, 'acme', {}, sites=(), log=lambda _: None, document_strategy='incremental')
    assert not (tmp_path / 'document-fallback.json').exists()
