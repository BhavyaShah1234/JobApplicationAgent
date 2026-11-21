"""A conditional restriction cannot establish a broader unconditional one."""
from jobagent.tools import projects as P

SOURCE={'summary':'Uses only official documentation when it is available, otherwise retrieves sources from the open web.'}

def test_source_condition_cannot_be_dropped_from_the_same_restricted_object():
    assert P.scope_claims('Uses only official documentation.',SOURCE)


def test_a_faithful_conditional_restriction_with_its_fallback_remains_supported():
    assert not P.scope_claims('Uses only official docs when available, otherwise retrieves web sources.',SOURCE)


def test_unconditional_source_can_support_a_narrower_conditional_claim():
    assert not P.scope_claims('Uses only official documentation when available.',
                              {'summary': 'Uses only official documentation.'})


def test_negated_condition_cannot_support_its_opposite():
    assert P.scope_claims('Uses only official documentation when available.',
                         {'summary': 'Uses only official documentation when not available.'})


def test_and_condition_cannot_be_weakened_to_or():
    assert P.scope_claims('Uses only official documentation when available or verified.',
                         {'summary': 'Uses only official documentation when available and verified.'})


def test_unrelated_conditional_sentence_does_not_qualify_an_unconditional_source():
    assert not P.scope_claims('Uses only official documentation.',
                              {'summary': 'Uses only official documentation. When offline, it returns a cached result.'})


def test_a_comma_cannot_drop_a_conjoined_source_condition():
    assert P.scope_claims('Uses only official documentation when available.',
                         {'summary': 'Uses only official documentation when available, and verified.'})


def test_a_faithful_comma_separated_conjunction_remains_supported():
    assert not P.scope_claims('Uses only official docs when available and verified.',
                              {'summary': 'Uses only official documentation when available, and verified.'})
