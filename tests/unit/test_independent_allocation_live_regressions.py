"""Independent controls derived from the first native CVS run, kept separate from the original 32."""
import hashlib
import json
import pytest
from jobagent.tools import layout
from jobagent.steps import multi_agent as M
from test_independent_skill_allocation_preservation import fixture


@pytest.mark.parametrize('new_issue,accepted', [
    ('Technical Skills "Databases" fills only 55% of its last line: add relevant skills', True),
    ('text overflows the right margin', False),
])
def test_fill_warning_progress_is_allowed_but_new_real_damage_is_rejected(tmp_path, monkeypatch, new_issue, accepted):
    f, state, lay, original = fixture(tmp_path, monkeypatch, 'none')
    lay.issues = ['Technical Skills "Databases" fills only 38% of its last line: add relevant skills']
    lay.skill_fill = {'Databases': .38}
    later = layout.Layout(pages=1, free_pt=10, issues=[new_issue], short_skills=['Databases'], skill_fill={'Databases': .55})
    monkeypatch.setattr(M.layout, 'check', lambda *a, **k: later)
    out = M._allocate_graph_skills(state, f, 'original TeX', [], 1, '', lay, 1)
    assert any('Redis' in line.terms for line in out[0].skills) is accepted
    if not accepted:
        for n, data in original.items():
            assert (tmp_path / n).read_bytes() == data


def test_snapshot_allocation_is_bound_to_the_new_pdf_and_actual_skills(tmp_path, monkeypatch):
    f, state, lay, original = fixture(tmp_path, monkeypatch, 'none')
    out = M._allocate_graph_skills(state, f, 'original TeX', [], 1, '', lay, 1)
    assert 'Redis' in [term for line in out[0].skills for term in line.terms]
    later = out[0].model_copy(deep=True)
    for line in later.skills:
        line.terms = [term for term in line.terms if term != 'Redis']
    new_pdf = b'later accepted PDF without Redis'
    (tmp_path / 'resume.pdf').write_bytes(new_pdf)
    snapshot = M._snapshot(state['ctx'], later, [])
    allocation = json.loads(snapshot['allocation'])
    assert allocation['added'] == []
    assert allocation['resume_sha256'] == hashlib.sha256(new_pdf).hexdigest()
