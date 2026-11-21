"""Independent existing-API identity controls authored before reviewing the follow-up fix.

Two marked FIXTURE ADAPTATIONS vs the byte-identical original (kept beside the before-logs); no assertion was waived."""
import copy
import hashlib
from types import SimpleNamespace
import pytest
from jobagent.config import get_settings
from jobagent.state import JobDescription
from jobagent.tools import documents as D, projects as P

FIRST = 'Built a Python event service that validates incoming sensor readings and stores timestamped observations for authorized operators to review in a dashboard.'
SECOND = 'The prototype exposes read-only status pages, and automated tests cover malformed input, bounded report exports, authentication and scoped access checks.'
OTHER = 'Built a Python image labeling tool that processes uploaded photographs and stores reviewed annotations for authorized operators to export through authenticated endpoints.'

@pytest.fixture(autouse=True)
def settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()

def card(repo, title='PulsePanel — sensor observation service', summary=FIRST+' '+SECOND):
    return {'full_name':repo,'url':'https://github.com/'+repo,'title':title,'summary':summary,'results':[],
            'tech_stack':['Python'],'substance':3,'fields':['Python'],'head_sha':hashlib.sha1(summary.encode()).hexdigest(),'owned':True,'kind':'project','created_at':'2026-01-01T00:00:00Z'}

def pair():
    return [card('owner/PulsePanel'),card('collaborator/PulsePanel', 'PulsePanel — Read-Only Sensor Dashboard')]

def state(cards, kept=None):
    entries=[P.from_card(c).model_dump() for c in cards]
    return {'ranked':entries,'kept':len(entries) if kept is None else kept,'short':[], 'years':{},
            'evidence':{c['full_name'].lower():P.evidence_snapshot(c) for c in cards}}

def normal(tmp_path, monkeypatch, cards, kept=None):
    from test_scope_evidence_flow import normal_job
    return D.resume_findings(normal_job(tmp_path,monkeypatch,state(cards,kept)))['hard']

def test_two_visible_cards_for_the_same_project_cannot_pass_normal_validation(tmp_path,monkeypatch):
    assert normal(tmp_path,monkeypatch,pair())

def test_a_hidden_duplicate_alternative_does_not_block_one_displayed_project(tmp_path,monkeypatch):
    # FIXTURE ADAPTATION 2: kept=1 of 2 ranked can never pass (>=2 projects must be shown in full, an existing rule). Same intent: the duplicate is hidden.
    cs=[pair()[0],card('owner/ImageAtlas','ImageAtlas — Image Review',OTHER+' '+SECOND),pair()[1]]
    assert normal(tmp_path,monkeypatch,cs,kept=2)==[]

def test_same_repository_leaf_with_different_projects_is_not_enough_for_deduplication(tmp_path,monkeypatch):
    cs=[card('owner/service','PulsePanel — Sensor Dashboard'),card('other/service','ImageAtlas — Image Review',OTHER+' '+SECOND)]
    assert normal(tmp_path,monkeypatch,cs)==[]

def test_a_generic_shared_title_alone_does_not_merge_unrelated_projects(tmp_path,monkeypatch):
    cs=[card('owner/sensor','Event Service'),card('other/images','Event Service',OTHER+' '+SECOND)]
    assert normal(tmp_path,monkeypatch,cs)==[]

@pytest.mark.parametrize('outage',[False,True])
def test_real_choose_cannot_return_two_aliases_even_after_retry_exhaustion(monkeypatch,outage):
    cs=pair()+[card('owner/ImageAtlas','ImageAtlas — Image Review',OTHER+' '+SECOND)]
    monkeypatch.setattr(P.repo_cards,'refresh',lambda *a,**k:copy.deepcopy(cs))
    monkeypatch.setattr(P.repo_cards,'rank',lambda cards,*a,**k:[(1-i*.1,c) for i,c in enumerate(cards)])
    def response(*args,**kwargs):
        if outage:raise TimeoutError('scripted provider outage')
        return P.Projects(projects=[P.ProjectPick(repo=c['full_name'],title=c['title'],stack=['Python'],paragraph_sentences=[1,2],short_sentences=[1]) for c in cs[:2]],notes='scripted repeated duplicate selection')
    monkeypatch.setattr(P.llm,'structured_invoke',response)
    chosen=P.choose(SimpleNamespace(tex='%'),JobDescription(title='Python Engineer',company='Example'),object(),'owner',log=lambda *a:None)
    repos={e.repo.lower() for e in chosen.entries}
    assert len(repos)>=2
    assert not {'owner/pulsepanel','collaborator/pulsepanel'}<=repos
    assert 'owner/imageatlas' in repos
