"""Project evidence IDs preserve scope without asking a model to reconstruct quotes."""
import pytest
from pydantic import ValidationError
from jobagent.tools import incremental_documents as I
from jobagent.tools.artifact_cache import ArtifactCache


def projects():
    return [dict(project=1,repo='owner/a',title='Camera Driver',stack=['Python'],
                 text='Built a camera driver.',evidence={'summary':'Supports cameras.'},owner_template_title='Camera Driver'),
            dict(project=2,repo='owner/b',title='Robot',stack=['C++'],
                 text='Built a robot.',evidence={'summary':'Supports robots.'},owner_template_title='Robot')]


def selected(bank,project,kind,scope):
    return next(k for k,v in bank.items() if v['project']==project and v['kind']==kind and v['scope']==scope)


def reference(bank,**overrides):
    return I.ProjectReferenceVerdict(finding=0,project=1,status=overrides.pop('status','rejected'),
        claim_id=overrides.pop('claim_id',selected(bank,1,'claim','text')),
        source_id=overrides.pop('source_id',selected(bank,1,'source','repo')),
        reason='The supplied source supports this claim.',**overrides)


def test_references_resolve_to_exact_original_text_with_no_model_retyping():
    bank=I.project_quote_bank(projects(),'Impact estimates allowed.')
    out=I.resolve_project_reference(reference(bank),bank)
    assert out.claim_quote=='Built a camera driver.'
    assert out.source_quote=='Supports cameras.' and out.evidence_source=='repo'
    schema=I.project_reference_schema(bank)
    obj=schema(verdicts=[reference(bank).model_dump()])
    assert 'claim_quote' not in obj.verdicts[0].model_dump()
    assert 'source_quote' not in obj.verdicts[0].model_dump()


@pytest.mark.parametrize('side',['claim_id','source_id'])
def test_another_repositorys_reference_cannot_establish_this_projects_claim(side):
    bank=I.project_quote_bank(projects(),'Policy')
    other=selected(bank,2,'claim' if side=='claim_id' else 'source','text' if side=='claim_id' else 'repo')
    with pytest.raises(ValueError,match='named project'):
        I.resolve_project_reference(reference(bank,**{side:other}),bank)


def test_unknown_reference_cannot_enter_a_structured_verdict():
    bank=I.project_quote_bank(projects(),'Policy');data=reference(bank).model_dump();data['source_id']='invented'
    with pytest.raises(ValidationError):I.project_reference_schema(bank)(verdicts=[data])


@pytest.mark.parametrize('status',['confirmed','rejected'])
def test_definitive_verdicts_require_real_evidence(status):
    bank=I.project_quote_bank(projects(),'Policy')
    with pytest.raises(ValueError,match='needs a supplied source'):
        I.resolve_project_reference(reference(bank,status=status,source_id=None),bank)


def test_uncertainty_needs_no_invented_quote_and_remains_uncacheable(tmp_path,monkeypatch):
    payload={'displayed':[{k:v for k,v in projects()[0].items() if k not in ['project','evidence','owner_template_title']}],
             'own_repo_sources':{'owner/a':{'summary':'Supports cameras.'}},'owner_claims_policy':'Policy'}
    def invoke(cache,stage,name,model,schema,messages,**kw):
        import json
        bank=json.loads(messages[1].content)['quote_bank']
        out=schema(verdicts=[reference(bank,status='uncertain',source_id=None).model_dump()])
        assert kw['cache_when'](out) is False
        kw['validator'](out)
        return out
    monkeypatch.setattr(I,'invoke',invoke);monkeypatch.setattr(I,'review_model',lambda *a,**kw:object())
    finding=I.ProjectFinding(project=1,issue='Unknown support.',severity='error')
    out=I.adjudicate_projects({'cache':ArtifactCache(tmp_path),'log':lambda _:None},payload,[finding])
    assert len(out)==1 and out[0].severity=='error' and 'uncertain' in out[0].issue


def test_long_evidence_is_indexed_as_exact_contiguous_excerpts():
    ps=projects();raw=' '.join(f'originalword{i}' for i in range(350));ps[0]['evidence']={'readme':raw}
    bank=I.project_quote_bank(ps,'Policy')
    spans=[v['text'] for v in bank.values() if v['project']==1 and v['kind']=='source' and v['scope']=='repo']
    assert len(spans)>1 and all(len(s)<=700 and s in raw for s in spans)
    assert raw[-80:] in spans[-1]


def test_owner_title_references_still_cannot_prove_implementation_features(tmp_path,monkeypatch):
    ps=projects();payload={'displayed':[{k:v for k,v in ps[0].items() if k not in ['project','evidence','owner_template_title']}],
        'own_repo_sources':{'owner/a':ps[0]['evidence']},'owner_template_titles':{'owner/a':'Camera Driver'},'owner_claims_policy':'Policy'}
    def invoke(cache,stage,name,model,schema,messages,**kw):
        import json
        bank=json.loads(messages[1].content)['quote_bank']
        return schema(verdicts=[reference(bank,source_id=selected(bank,1,'source','owner_template')).model_dump()])
    monkeypatch.setattr(I,'invoke',invoke);monkeypatch.setattr(I,'review_model',lambda *a,**kw:object())
    with pytest.raises(ValueError,match='implementation features'):
        I.adjudicate_projects({'cache':ArtifactCache(tmp_path),'log':lambda _:None},payload,
            [I.ProjectFinding(project=1,issue='Unsupported feature.',severity='error')])
