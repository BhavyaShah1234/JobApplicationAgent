"""One project is shown once: identity cues, model choice, retry exhaustion, provider outage, cache, swaps and normal validation.

Own controls (the independent existing-API controls live in test_independent_project_identity_controls.py). No repository or company is named in
the source; identity comes only from a card's own data."""
import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

from jobagent.config import get_settings
from jobagent.state import JobDescription
from jobagent.steps import multi_agent as M
from jobagent.tools import projects as P
from test_scope_evidence_flow import FILLED

FIRST = 'Built a Python event service that validates incoming sensor readings and stores timestamped observations for authorized operators to review in a dashboard.'
SECOND = 'The prototype exposes read-only status pages, and automated tests cover malformed input, bounded report exports, authentication and scoped access checks.'
OTHER = 'Built a Python image labeling tool that processes uploaded photographs and stores reviewed annotations for authorized operators to export through authenticated endpoints.'
THIRD = 'Built a Python scheduler that queues maintenance jobs and records completed runs for authorized operators to audit through a reporting page and exports.'
JD = JobDescription(title='Python Engineer', company='Example')


@pytest.fixture(autouse=True)
def settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def card(repo, title, summary, **extra):
    return {'full_name': repo, 'url': 'https://github.com/' + repo, 'title': title, 'summary': summary, 'results': [], 'tech_stack': ['Python'],
            'substance': 3, 'fields': ['Python'], 'head_sha': hashlib.sha1((repo + summary).encode()).hexdigest(), 'owned': True, 'kind': 'project',
            'created_at': '2026-01-01T00:00:00Z', **extra}


A = card('owner/PulsePanel', 'PulsePanel — sensor observation service', FIRST + ' ' + SECOND)
A_COPY = card('collaborator/PulsePanel', 'PulsePanel: Read-Only Sensor Dashboard', FIRST + ' ' + SECOND + ' ')   # em-dash vs colon title alias
IMAGES = card('owner/ImageAtlas', 'ImageAtlas — Image Review', OTHER + ' ' + SECOND)
SCHED = card('owner/Scheduler', 'Maintenance Scheduler', THIRD + ' ' + SECOND)


def wire(monkeypatch, cards, response=None, known=None):
    monkeypatch.setattr(P.repo_cards, 'refresh', lambda *a, **k: copy.deepcopy(cards))
    monkeypatch.setattr(P.repo_cards, 'rank', lambda pool, *a, **k: [(1 - i * .1, c) for i, c in enumerate(pool)])
    calls = []

    def reply(*args, **kwargs):
        calls.append(1)
        if response is None:
            raise TimeoutError('scripted outage')
        return response
    monkeypatch.setattr(P.llm, 'structured_invoke', reply)
    if known is not None:
        monkeypatch.setattr(P, 'known_entries', lambda tex: known)
    return calls


def picks(*cards):
    return P.Projects(projects=[P.ProjectPick(repo=c['full_name'], title=c['title'], stack=['Python'], paragraph_sentences=[1, 2], short_sentences=[1])
                                for c in cards], notes='scripted')


def run(**kw):
    return P.choose(SimpleNamespace(tex='%'), JD, object(), 'owner', log=lambda *a: None, **kw)


def repos(chosen):
    return [e.repo.lower() for e in chosen.entries]


# --- identity cues ----------------------------------------------------------------------------------------------------------------------
def test_aliases_with_em_dash_and_colon_titles_share_an_identity():
    assert P.identity_cues(A) & P.identity_cues(A_COPY)


def test_a_generic_leaf_or_a_generic_title_alone_never_merges():
    a, b = card('x/service', 'Sensor Hub', FIRST), card('y/service', 'Image Hub', OTHER)
    c, d = card('x/sensor', 'Event Service', FIRST), card('y/images', 'Event Service', OTHER)
    assert not P.identity_cues(a) & P.identity_cues(b)
    assert not P.identity_cues(c) & P.identity_cues(d)


def test_explicit_fork_metadata_and_the_same_commit_are_identity():
    original, fork = card('x/Alpha', 'Alpha Engine', FIRST), card('y/Renamed', 'Totally Different Name', FIRST, fork_of='X/alpha')
    assert P.identity_cues(original) & P.identity_cues(fork)
    twin = card('z/Other', 'Other Name', OTHER, head_sha=original['head_sha'])
    assert P.identity_cues(original) & P.identity_cues(twin)


# --- choose: model, retry exhaustion, outage, preference, cache ---------------------------------------------------------------------------
def test_a_model_choice_with_a_repeated_project_keeps_one_and_the_remaining_unique_ones(monkeypatch):
    calls = wire(monkeypatch, [A, A_COPY, IMAGES, SCHED], picks(A, A_COPY, IMAGES))
    assert repos(run()) == ['owner/pulsepanel', 'owner/imageatlas'] and len(calls) == 1


def test_persistent_duplicates_continue_to_the_next_ranked_unique_candidate_not_the_last_draft(monkeypatch):
    calls = wire(monkeypatch, [A, A_COPY, IMAGES], picks(A, A_COPY))
    assert repos(run()) == ['owner/pulsepanel', 'owner/imageatlas'] and len(calls) == 3


def test_the_owner_template_project_represents_a_duplicated_identity(monkeypatch):
    wire(monkeypatch, [A, A_COPY, IMAGES], picks(A, A_COPY, IMAGES), known={'collaborator/pulsepanel': ('PulsePanel', '2026')})
    chosen = run()
    assert repos(chosen) == ['collaborator/pulsepanel', 'owner/imageatlas'] and chosen.entries[0].title == 'PulsePanel'


def test_outage_fallback_skips_a_collaborator_copy_and_fork_metadata_but_keeps_distinct_projects(monkeypatch):
    fork = card('z/Renamed', 'Unrelated Name', THIRD + ' ' + SECOND, fork_of='owner/PulsePanel')
    wire(monkeypatch, [A, A_COPY, fork, IMAGES, SCHED])
    assert repos(run()) == ['owner/pulsepanel', 'owner/imageatlas', 'owner/scheduler']


def test_outage_fallback_keeps_unrelated_projects_that_share_a_generic_leaf(monkeypatch):
    a, b = card('x/service', 'Sensor Hub', FIRST + ' ' + SECOND), card('y/service', 'Image Hub', OTHER + ' ' + SECOND)
    wire(monkeypatch, [a, b])
    assert repos(run()) == ['x/service', 'y/service']


def test_too_few_unique_projects_fails_instead_of_publishing_a_duplicate(monkeypatch):
    wire(monkeypatch, [A, A_COPY], picks(A, A_COPY))
    with pytest.raises(RuntimeError):
        run()


def test_a_cached_duplicate_selection_is_deduplicated_the_same_way(monkeypatch):
    wire(monkeypatch, [A, A_COPY, IMAGES])
    stored = picks(A, A_COPY).model_dump()
    cache = SimpleNamespace(get=lambda *a: stored, record=lambda *a: None, put=lambda *a: None)
    assert repos(run(cache=cache, model_name='m')) == ['owner/pulsepanel', 'owner/imageatlas']


# --- normal validation and swaps ------------------------------------------------------------------------------------------------------------
def state(cards, kept=None):
    entries = [P.from_card(c).model_dump() for c in cards]
    return {'ranked': entries, 'kept': len(entries) if kept is None else kept, 'short': [], 'years': {}, 'cards': {},
            'evidence': {c['full_name'].lower(): P.evidence_snapshot(c) for c in cards}}


def test_content_findings_name_the_repeated_visible_project_only(monkeypatch):
    found = P.duplicate_findings(state([A, IMAGES, A_COPY]))
    assert found == ['project 3 (collaborator/PulsePanel) is the same project as project 1: show it once and use another repository']
    assert P.duplicate_findings(state([A, IMAGES, A_COPY], kept=2)) == []
    assert P.content_findings(state([A, A_COPY]), {}) and not any('same project' in f for f in P.content_findings(state([A, IMAGES]), {}))


def test_an_entry_without_a_saved_card_is_still_compared_by_repo_and_title():
    st = state([A, A_COPY])
    st['evidence'] = {}
    entries = st['ranked']
    entries[0]['title'] = entries[1]['title'] = 'PulsePanel'
    assert len(P.duplicate_findings(st)) == 1   # no saved card: the entry's own repo leaf and title are the cues


def ctx(tmp_path, st, pool):
    (tmp_path / 'projects.json').write_text(json.dumps(st))
    return {'job_dir': tmp_path, 'r': SimpleNamespace(tex='%', roles=[]), 'cards': {c['full_name'].lower(): c for c in pool}, 'jd': JD, 'log': lambda *_: None}


def swap(project, c):
    return SimpleNamespace(project=project, entry=P.ProjectPick(repo=c['full_name'], title=c['title'], stack=['Python'], paragraph_sentences=[1, 2], short_sentences=[]))


def test_a_swap_that_duplicates_another_visible_project_is_rejected_and_not_applied(tmp_path):
    st = state([A, IMAGES], kept=2)
    c = ctx(tmp_path, st, [A, IMAGES, A_COPY])
    result = SimpleNamespace(projects=[], swaps=[swap(2, A_COPY)])
    with pytest.raises(ValueError, match='same project as displayed owner/PulsePanel'):
        M._check_project_edits(c, st, result)
    M._merge(c, FILLED, {'projects': result})
    assert [e['repo'] for e in json.loads((tmp_path / 'projects.json').read_text())['ranked']] == ['owner/PulsePanel', 'owner/ImageAtlas']


def test_a_swap_may_replace_the_project_it_duplicates_and_a_distinct_swap_is_applied(tmp_path):
    st = state([A, IMAGES], kept=2)
    c = ctx(tmp_path, st, [A, IMAGES, A_COPY, SCHED])
    M._check_project_edits(c, st, SimpleNamespace(projects=[], swaps=[swap(1, A_COPY), swap(2, SCHED)]))
    M._merge(c, FILLED, {'projects': SimpleNamespace(projects=[], swaps=[swap(2, SCHED)])})
    assert [e['repo'] for e in json.loads((tmp_path / 'projects.json').read_text())['ranked']] == ['owner/PulsePanel', 'owner/Scheduler']
