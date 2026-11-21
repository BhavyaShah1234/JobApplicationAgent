"""Independent scope evidence controls written before reviewing Claude's patch."""
from jobagent.tools import projects as P


def findings(claim,source,other_source=None):
    state={'ranked':[{'repo':'owner/a','title':'A','stack':['Python'],'paragraph':claim,'short':claim}], 'kept':1,'short':[]}
    cards={'owner/a':{'full_name':'owner/a','summary':source,'tech_stack':['Python']}}
    if other_source:cards['owner/b']={'full_name':'owner/b','summary':other_source,'tech_stack':['Python']}
    return P.content_findings(state,cards)


def test_a_primary_corpus_does_not_establish_exclusive_input_scope():
    assert findings('Answers questions using only official documentation.',
                    'The main corpus contains official ASU documentation. Retrieval falls back to the open web and user-provided URLs.')


def test_an_unrelated_only_elsewhere_in_the_same_card_cannot_vouch():
    assert findings('Uses only PostgreSQL for storage.',
                    'Data uses PostgreSQL and metadata uses MongoDB. Only verified answers are displayed.')


def test_a_denied_restriction_cannot_support_an_affirmative_one():
    assert findings('Uses only PostgreSQL for storage.',
                    'It does not use only PostgreSQL for storage; metadata also uses MongoDB.')


def test_another_repositorys_genuine_restriction_cannot_be_borrowed():
    assert findings('Answers questions using only official documentation.',
                    'Uses an official documentation corpus with optional open-web retrieval.',
                    'Answers questions using only official documentation.')


def test_not_only_is_an_additive_statement_not_an_exclusive_one():
    assert not findings('Uses not only PostgreSQL but also MongoDB.',
                        'Data uses PostgreSQL and metadata uses MongoDB.')


def test_a_real_inference_restriction_does_not_forbid_unrelated_web_retrieval():
    assert not findings('Uses only local Ollama models for inference, with optional web retrieval.',
                        'Inference uses only local Ollama models. Retrieval can fall back to web search.')


def test_a_common_faithful_exclusivity_paraphrase_keeps_its_source_support():
    assert not findings('Answers using only official docs.',
                        'Answers using exclusively official documentation.')


def test_a_shared_five_letter_prefix_is_not_the_same_scope_concept():
    assert findings('Uses only private data.', 'Uses only privacy-safe data.')


def test_modifiers_cannot_be_recombined_across_different_objects_in_a_restriction():
    assert findings('Uses only private reports.', 'Uses only public reports and private datasets.')
