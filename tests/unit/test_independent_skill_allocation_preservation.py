"""Post-implementation integration review: optional padding cannot discard accepted work."""
import json
from types import SimpleNamespace as NS
import pytest
from jobagent.steps import multi_agent as M
from jobagent.tools import tailor as T,layout,skillgraph as SG


def fixture(tmp_path,monkeypatch,mutation):
    bullet=T.FillBullet.model_construct(text='Built 12 REST endpoints using FastAPI.',duties=[0],original=None,estimated=['12 endpoints'])
    f=T.Filled.model_construct(summary='Engineer with backend experience.',roles=[T.FillRole.model_construct(role=0,bullets=[bullet])],skills=[T.SkillLine(category='Databases',terms=['NoSQL']),T.SkillLine(category='Languages',terms=['Python']),T.SkillLine(category='Containers',terms=['Docker'])],notes='')
    original={'resume.pdf':b'original PDF','resume.tex':b'original TeX','projects.json':b'{"ranked": [{"repo": "own/project", "paragraph": "Original source paragraph."}], "kept": 1, "short": []}'}
    for n,v in original.items():(tmp_path/n).write_bytes(v)
    ctx={'job_dir':tmp_path,'r':NS(tex='original TeX',skills=[],roles=[NS(company='Atlas',title='Engineer',bullets=[bullet.text])]),'jd':JobDescription(title='Engineer',company='Example'),'graph':{'owned':[{'skill':'Redis','via':'databases','score':2}], 'review':[]},'log':lambda _:None}
    g=SG.Graph();g.add('skill','Redis','database')
    monkeypatch.setattr(SG,'load',lambda *a,**kw:g)
    monkeypatch.setattr(SG,'place_skills',lambda *a,**kw:([('Databases','Redis')],[]))
    lay=layout.Layout(pages=1,free_pt=10,skill_fill={'Databases':.3},short_skills=['Databases'])
    monkeypatch.setattr(M.layout,'check',lambda *a,**kw:lay)
    def render(state):
        candidate=state['f'].model_copy(deep=True)
        adding=any('Redis' in x.terms for x in candidate.skills)
        if adding:
            (tmp_path/'resume.pdf').write_bytes(b'trial PDF')
            (tmp_path/'resume.tex').write_bytes(b'trial TeX')
            if mutation=='drop_bullet':candidate.roles[0].bullets=[]
            if mutation in ('project_change','overflow'):(tmp_path/'projects.json').write_text('{"ranked": [], "kept": 0, "short": []}')
        else:
            (tmp_path/'resume.pdf').write_bytes(original['resume.pdf'])
            (tmp_path/'resume.tex').write_bytes(original['resume.tex'])
        return candidate,'trial TeX' if adding else 'original TeX',[],2 if adding and mutation=='overflow' else 1,''
    monkeypatch.setattr(M,'render',render)
    state={'ctx':ctx,'f':f,'reviewed':True}
    return f,state,lay,original

from jobagent.state import JobDescription

@pytest.mark.parametrize('mutation',['drop_bullet','project_change'])
def test_padding_cannot_keep_a_render_that_removes_existing_content(tmp_path,monkeypatch,mutation):
    f,state,lay,original=fixture(tmp_path,monkeypatch,mutation)
    result=M._allocate_graph_skills(state,f,'original TeX',[],1,'',lay,1)
    assert [b.text for r in result[0].roles for b in r.bullets]==[b.text for r in f.roles for b in r.bullets]
    assert (tmp_path/'projects.json').read_bytes()==original['projects.json']
    assert all('Redis' not in x.terms for x in result[0].skills)


def test_rejected_trial_restores_project_state_as_well_as_pdf(tmp_path,monkeypatch):
    f,state,lay,original=fixture(tmp_path,monkeypatch,'overflow')
    result=M._allocate_graph_skills(state,f,'original TeX',[],1,'',lay,1)
    for n,v in original.items():assert (tmp_path/n).read_bytes()==v,n
    assert all('Redis' not in x.terms for x in result[0].skills)
