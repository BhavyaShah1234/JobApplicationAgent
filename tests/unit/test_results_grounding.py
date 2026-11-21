"""Own-controls for results as prose-only evidence (term-specific denial, printed stack, scattered words)."""
from jobagent.tools import projects as P

CARD = {'full_name': 'o/p', 'tech_stack': ['Python'], 'summary': 'Dashboard.'}


def test_denial_of_one_term_does_not_hide_another_affirmed_in_a_different_clause():
    card = {**CARD, 'results': ['Supports mmap streaming; does not support file uploads.']}
    assert P.prose_tech_claims('It supports mmap streaming.', card, ['mmap streaming', 'file uploads']) == []
    assert P.prose_tech_claims('It supports file uploads.', card, ['mmap streaming', 'file uploads']) == ['file uploads']


def test_results_never_support_the_printed_stack():
    card = {**CARD, 'results': ['Added Kafka ingestion.']}
    assert P.ground_stack(['Kafka'], card)[1] == ['Kafka']


def test_scattered_words_inside_one_entry_do_not_assemble_a_feature():
    card = {**CARD, 'results': ['Files, uploads of nothing; optional file handling and uploads.']}
    assert P.prose_tech_claims('It supports file uploads.', card, ['file uploads']) == ['file uploads']


def test_ambiguous_short_names_are_not_established_by_results():
    card = {**CARD, 'results': ['Rewrote the service in Go.']}
    assert P._results_establish(card, 'Go') is False
