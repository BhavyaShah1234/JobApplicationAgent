"""The final live PDF prints 1.5s: compact or fractional time quantities must retain their units."""
import pytest
from jobagent.tools import number_claims as NC


@pytest.mark.parametrize('written,full', [('1.5s', '1.5 seconds'), ('40s', '40 seconds'), ('1.5 s', '1.5 seconds')])
def test_compact_fractional_durations_match_their_full_unit_spelling(written, full):
    expected = NC.metric_tokens('Reduced build time by ' + full + '.')
    assert expected
    assert NC.metric_tokens('Reduced build time by ' + written + '.') == expected


def test_compact_seconds_cannot_be_established_by_milliseconds():
    seconds = NC.metric_tokens('Reduced build time by 1.5s.')
    assert seconds
    assert seconds != NC.metric_tokens('Reduced build time by 1.5ms.')
