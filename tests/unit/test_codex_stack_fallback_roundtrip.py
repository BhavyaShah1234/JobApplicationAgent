"""A grounded fallback must survive the next validation without repair loops."""
import pytest
from jobagent.tools import projects as P

@pytest.mark.parametrize('stack,expected', [(['.NET'], ['.NET']), (['Python / FastAPI'], ['Python', 'FastAPI'])])
def test_own_source_fallback_is_grounded_and_stable(stack, expected):
    card = {'full_name': 'owner/example', 'tech_stack': stack}
    kept, dropped = P.ground_stack(['unsupported-framework'], card)
    assert kept == expected
    assert dropped == ['unsupported-framework']
    assert P.ground_stack(kept, card) == (expected, [])
