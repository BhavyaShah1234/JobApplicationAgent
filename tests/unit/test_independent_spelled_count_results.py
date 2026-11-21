"""A supported count remains a result when the writer spells out its number."""
import pytest
from jobagent.tools import tailor as T


@pytest.mark.parametrize('text', [
    'Owned twelve REST endpoints using FastAPI.',
    'Trained fifteen technicians on Docker workflows.',
    'Served five million events per day using Kafka.',
])
def test_a_word_spelled_count_does_not_need_a_percentage_or_an_extra_outcome_verb(text):
    assert T.has_result(text), text
