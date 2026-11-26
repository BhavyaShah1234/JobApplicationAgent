"""Country/state/city ATS labels retain their city without weakening state-only policy."""
import pytest
from jobagent.tools import application_address as A


@pytest.mark.parametrize('label,code,city', [
    ('USA, CO, Boulder', 'CO', 'Boulder'),
    ('USA, Colorado, Boulder', 'CO', 'Boulder'),
    ('United States of America, CA, San Francisco', 'CA', 'San Francisco'),
    ('CO, Boulder', 'CO', 'Boulder'),
    ('Boulder, CO', 'CO', 'Boulder'),
    ('Boulder, Colorado, USA', 'CO', 'Boulder'),
    ('Washington, DC', 'DC', 'Washington'),
    ('USA, DC, Washington', 'DC', 'Washington'),
    ('Colorado Springs, CO', 'CO', 'Colorado Springs'),
    ('Wilmington, NC, US, 28405', 'NC', 'Wilmington'),
    ('Wilmington, NC 28405', 'NC', 'Wilmington'),
    ('Wilmington, North Carolina, United States, 28405-1234', 'NC', 'Wilmington'),
    ('Wilmington, NC, 28405, US', 'NC', 'Wilmington'),
    ('USA, NC, Wilmington 28405', 'NC', 'Wilmington'),
    ('New York, NY 10001', 'NY', 'New York'),
])
def test_explicit_city_survives_both_location_orders(label, code, city):
    assert A._state_from_label(label) == code
    assert A._explicit_city(label, code) == city
    assert A._target([label], lambda *a: None, allow_geocode=False) == {
        'city': city, 'state': A.STATE_CITIES[code][0], 'state_code': code,
    }


@pytest.mark.parametrize('label', ['Colorado', 'CO', 'USA, CO', 'Remote, Colorado', 'CO 80001', 'CO, US, 80001'])
def test_state_only_still_uses_its_configured_city(label):
    result = A._target([label], lambda *a: None, allow_geocode=False)
    assert result == {'city': A.STATE_CITIES['CO'][1], 'state': 'Colorado', 'state_code': 'CO'}


def test_country_first_city_beats_separate_state_only_research():
    assert A._target(['USA, CO, Boulder', 'Colorado'], lambda *a: None, allow_geocode=False)['city'] == 'Boulder'


@pytest.mark.parametrize('label', ['Canada, ON, Toronto', 'USA', 'Remote'])
def test_non_us_or_unspecific_labels_do_not_gain_a_state(label):
    assert A._state_from_label(label) is None
