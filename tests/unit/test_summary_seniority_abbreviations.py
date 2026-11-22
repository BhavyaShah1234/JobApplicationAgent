"""Standard seniority spelling must not create an extra sentence or a wrong-title finding."""
import pytest
from jobagent.steps import multi_agent as M


@pytest.mark.parametrize('prefix', ['Sr.', 'Jr.'])
def test_opening_seniority_is_not_a_sentence(prefix, monkeypatch):
    monkeypatch.setattr(M.T, 'declared_claims', lambda: {'years_experience': 5})
    text = f'{prefix} Machine Learning Engineer building Python model services. Operates Kubernetes deployments. Improves production monitoring.'
    assert M.summary_issues(text, False) == []


def test_four_real_sentences_still_fail(monkeypatch):
    monkeypatch.setattr(M.T, 'declared_claims', lambda: {'years_experience': 5})
    text = 'Sr. Machine Learning Engineer building Python services. Operates deployments. Improves monitoring. Mentors engineers.'
    assert any('4 sentences' in issue for issue in M.summary_issues(text, False))


@pytest.mark.parametrize('summary,title', [
    ('Senior Machine Learning Engineer building Python services.', 'Sr. Machine Learning Engineer'),
    ('Sr. Machine Learning Engineer building Python services.', 'Senior Machine Learning Engineer'),
    ('Junior Software Engineer building services.', 'Jr. Software Engineer'),
])
def test_equivalent_seniority_keeps_the_same_target_role(summary, title):
    assert M._title_prefix(summary, title)


def test_seniority_equivalence_never_changes_the_job_family():
    assert not M._title_prefix('Senior Data Scientist building models.', 'Sr. Machine Learning Engineer')


def test_seniority_does_not_relax_tenure_or_length(monkeypatch):
    monkeypatch.setattr(M.T, 'declared_claims', lambda: {'years_experience': 5})
    assert any('declared 5' in issue for issue in M.summary_issues('Sr. Engineer with 6 years of experience.', False))
    assert any('characters' in issue for issue in M.summary_issues('Sr. Engineer ' + 'building reliable systems ' * 25 + '.', False))
