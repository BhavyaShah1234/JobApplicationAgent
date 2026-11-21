"""Budget-bound live fragments must fail without changing text or source provenance."""
from types import SimpleNamespace
import pytest
from jobagent.tools import tailor as T, incremental_documents as I
from jobagent.steps import multi_agent as M
from test_figure_provenance import TEX2, jd
from test_multi_agent import _coverage_repair_fixture, _coverage_template

CLIPPED = [
    'Designed and automated regression test suites using Playwright and Selenium, increasing coverage by 40% while developing deep SaaS',
    'Collaborated with Agile teams to integrate QA processes, improving release cycle efficiency by 25% and supporting QA manager tasks',
]

def packet(text, original=None):
    return T.Filled.model_construct(summary='Engineer.', notes='', skills=[], roles=[
        T.FillRole.model_construct(role=0, bullets=[T.FillBullet.model_construct(
            text=text, duties=[0], original=original, estimated=['40%'])])])

def incomplete(issues):
    return [s for s in issues if 'complete sentence' in s]

@pytest.mark.parametrize('text', CLIPPED)
def test_actual_130_character_live_fragments_fail_shared_quality_without_mutation(text):
    assert len(text) == T.BULLET_MAX
    f=packet(text);before=f.model_dump()
    assert incomplete(T.quality(T.parse(TEX2),jd(),f))
    assert f.model_dump() == before

@pytest.mark.parametrize('tool,size', [('Python',129),('FastAPI',130)])
def test_complete_sentences_at_the_budget_boundary_remain_valid(tool,size):
    text=f'Built {tool} APIs, reducing median response latency by 20% and improving service reliability for distributed production services.'
    assert len(text)==size
    assert not T.quality(T.parse(TEX2),jd(),packet(text))

def test_complete_but_overlength_sentence_still_fails_the_existing_budget():
    text='Built FastAPI APIs, reducing average response latency by 20% and improving service reliability for distributed production services.'
    assert len(text)==131
    issues=T.quality(T.parse(TEX2),jd(),packet(text))
    assert any('131 characters' in s for s in issues)
    assert not incomplete(issues)

@pytest.mark.parametrize('suffix', ['."', '.)'])
def test_sentence_terminator_before_a_closing_quote_or_parenthesis_is_allowed(suffix):
    assert not incomplete(T.quality(T.parse(TEX2),jd(),packet('Built Python APIs, reducing latency by 20%'+suffix)))

def test_real_verbatim_no_period_source_is_preserved_but_a_forged_original_is_not_exempt():
    r=T.parse(TEX2)
    original='Built Python services, improving reporting speed by 20% for production teams with distributed customers and complex data sources'
    r.roles[0].bullets=[T.to_latex(original)]
    f=packet(original,original=0);before=f.model_dump()
    assert T.is_verbatim(r,0,f.roles[0].bullets[0])
    assert not incomplete(T.quality(r,jd(),f))
    assert f.model_dump()==before
    changed=packet(CLIPPED[0],original=0)
    assert not T.is_verbatim(r,0,changed.roles[0].bullets[0])
    assert incomplete(T.quality(r,jd(),changed))


def test_invalid_incremental_repair_is_rejected_without_leaking_text_coverage_or_estimates():
    posting,f=_coverage_repair_fixture();before=f.model_dump()
    patch=M.IncrementalRepair(fixes=[M.IncrementalBulletFix(role=0,bullet=1,text=CLIPPED[0],duties=[0,2],estimated=['40%'])])
    with pytest.raises(ValueError,match='complete sentence'):
        M.validate_incremental_repair(f,posting,patch,template=_coverage_template(),require_coverage=True)
    assert f.model_dump()==before


def test_cached_clipped_repair_is_revalidated_and_replaced_before_return(monkeypatch):
    posting,f=_coverage_repair_fixture()
    bad=M.IncrementalRepair(fixes=[M.IncrementalBulletFix(role=0,bullet=1,text=CLIPPED[0],duties=[0,2],estimated=['40%'])])
    good=M.IncrementalRepair(fixes=[M.IncrementalBulletFix(role=0,bullet=1,text=f.roles[0].bullets[0].text,duties=[0,2],estimated=['20%'])])
    events=[];calls=[];stored=[]
    cache=SimpleNamespace(get=lambda *a:bad.model_dump(),record=events.append,put=lambda *a:stored.append(a[-1]))
    def generate(*args,**kwargs):
        calls.append(1)
        return good
    monkeypatch.setattr(I.llm,'structured_invoke',generate)
    monkeypatch.setattr(I.llm,'last_call',lambda:{})
    result=I.invoke(cache,'experience','offline',object(),M.IncrementalRepair,[],
        validator=lambda value:M.validate_incremental_repair(f,posting,value,template=_coverage_template(),require_coverage=True))
    assert result==good and calls==[1]
    assert any(e.get('event')=='cache_rejected' for e in events)
    assert stored==[good.model_dump()]
