"""Live growth exposed unrestricted category strings disappearing at graph insertion."""
import pytest
from pydantic import ValidationError
from jobagent.tools import skillgraph as S


@pytest.mark.parametrize('category',['','technique','library','unknown'])
def test_model_skill_categories_must_use_the_declared_vocabulary(category):
    with pytest.raises(ValidationError):S.SkillOut(name='Example Skill',category=category)


def test_legacy_category_migration_preserves_supported_edges_and_suggestions():
    g=S.Graph();role=S.ensure_role(g,'ml','Machine Learning Engineer')
    duty=g.add('duty','model training');seed=g.add('skill','TensorFlow','framework');neighbor=g.add('skill','PyTorch')
    g.link(role,duty,'posting');g.link(duty,seed,'posting');g.link(duty,neighbor,'posting')
    before=S.suggest(g,['TensorFlow'],'ml',{'pytorch'},set())
    edges={k:dict(v) for k,v in g.edges.items()}
    stats=S.build(g,[],[],merge=None)
    assert stats['category_defaults']==1 and g.nodes[neighbor]['category']=='tool'
    assert g.nodes[seed]['category']=='framework' and g.edges==edges
    assert S.suggest(g,['TensorFlow'],'ml',{'pytorch'},set())==before
    assert S.build(g,[],[],merge=None)['category_defaults']==0
    assert g.nodes[neighbor]['category_defaulted']
    g.add('skill','PyTorch','framework')
    assert g.nodes[neighbor]['category']=='framework' and 'category_defaulted' not in g.nodes[neighbor]
