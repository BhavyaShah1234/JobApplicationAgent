"""Independent controls for reviewed patches, including cases their own tests omitted."""
import pytest

from jobagent.tools import projects as P
from test_figure_provenance import check


def test_actual_mapping_capability_cannot_establish_achieved_compliance():
    card = {'summary': 'Compliance mapping for SOC 2/HIPAA/ISO 27001.', 'results': []}
    assert P.standard_outcome_claims('The project achieved HIPAA compliance.', card)


def test_mapping_paraphrase_is_still_a_capability():
    card = {'summary': 'Maps controls to HIPAA.', 'results': []}
    assert not P.standard_outcome_claims('The system provides HIPAA compliance mapping.', card)


def test_unknown_source_level_cannot_establish_a_specific_achievement():
    card = {'results': ['The project conforms to WCAG.']}
    assert P.standard_outcome_claims('The project conforms to WCAG 2.2 AAA.', card)


def test_level_matching_is_case_insensitive():
    card = {'results': ['The project conforms to wcag 2.1 aa.']}
    assert P.standard_outcome_claims('The project conforms to WCAG 2.1 AAA.', card)


@pytest.mark.parametrize('technology', ['C++', 'C#', 'F#', '.NET', 'Node.js', 'CI/CD'])
def test_real_technology_punctuation_survives_exact_source_matching(technology):
    kept, dropped = P.ground_stack([technology], {'tech_stack': [technology]})
    assert kept == [technology] and not dropped


def test_cpp_is_not_evidence_of_a_distinct_c_implementation():
    _, dropped = P.ground_stack(['C'], {'tech_stack': ['C++']})
    assert dropped == ['C']


def test_dotnet_is_not_a_neural_net_mentioned_in_prose():
    _, dropped = P.ground_stack(['.NET'], {'tech_stack': ['Python'], 'summary': 'Trains a neural net.'})
    assert dropped == ['.NET']


def test_a_one_character_source_language_is_a_valid_repair_fallback():
    kept, dropped = P.ground_stack(['InventedFramework'], {'tech_stack': ['R']})
    assert kept == ['R'] and dropped == ['InventedFramework']


def test_a_percent_tag_cannot_cover_a_single_new_multiplier():
    tags = check('Improved throughput 25x.', estimated=['25%'])
    assert '25%' not in tags and any('25x' in tag.lower() for tag in tags)


def test_a_word_percentage_keeps_its_confirmation_provenance():
    tags = check('Reduced errors by twenty percent.', estimated=['20%'])
    assert tags


def test_a_legacy_bare_number_still_covers_one_percentage():
    assert check('Reduced errors by 22%.', estimated=['22']) == ['22']
