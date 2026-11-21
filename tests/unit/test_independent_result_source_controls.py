"""Existing-API controls for own result evidence, authored before the results-grounding fix."""
import pytest
from jobagent.tools import projects as P

@pytest.mark.parametrize('feature',['file uploads','mmap streaming'])
def test_an_affirmed_fact_in_own_results_supports_the_same_prose(feature):
    sentence=f'The Python dashboard supports optional {feature} through an authenticated operator interface.'
    card={'full_name':'owner/panel','tech_stack':['Python'],'summary':'Built an operator dashboard.','results':[sentence]}
    assert P.prose_tech_claims(sentence,card,[feature])==[]

@pytest.mark.parametrize('result',['The dashboard does not support file uploads.','File uploads are planned for a future release.'])
def test_a_denial_or_plan_in_results_cannot_establish_implemented_support(result):
    card={'full_name':'owner/panel','tech_stack':['Python'],'summary':'Built an operator dashboard.','results':[result]}
    assert P.prose_tech_claims('The dashboard supports file uploads.',card,['file uploads'])

def test_other_repository_results_do_not_support_own_project_prose():
    card={'full_name':'owner/panel','tech_stack':['Python'],'summary':'Built an operator dashboard.','results':[]}
    foreign={'full_name':'other/site','tech_stack':['file uploads'],'results':['The site supports file uploads.']}
    assert P.prose_tech_claims('The dashboard supports file uploads.',card,P.tech_vocabulary([card,foreign]))

def test_words_in_separate_result_entries_do_not_assemble_a_feature():
    card={'full_name':'owner/panel','tech_stack':['Python'],'summary':'Built an operator dashboard.',
          'results':['The service validates an imported file.','Uploads are not implemented yet.']}
    assert P.prose_tech_claims('The dashboard supports file uploads.',card,['file uploads'])
