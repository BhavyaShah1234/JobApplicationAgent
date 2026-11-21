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


@pytest.mark.parametrize('written,full', [('Completed builds in 40s.', 'Completed builds in 40 seconds.'), ('Reduced builds from 40s to 8s.', 'Reduced builds from 40 seconds to 8 seconds.')])
def test_time_prepositions_do_not_turn_build_durations_into_decades(written, full):
    assert NC.metric_tokens(written) == NC.metric_tokens(full)
