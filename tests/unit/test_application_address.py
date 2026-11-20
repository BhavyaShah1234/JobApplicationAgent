import json

import pytest

from jobagent.tools import application_address as A


@pytest.mark.parametrize("location", [
    "San Jose", "San Jose, United States of America", "San Jose, United States",
    "San Jose, USA", "San Jose, US",
])
def test_city_lookup_uses_a_single_country_qualifier(monkeypatch, location):
    queries = []
    def geocode(query):
        queries.append(query)
        return {"address": {"country_code": "us", "state": "California", "city": "San Jose", "ISO3166-2-lvl4": "US-CA"}}
    monkeypatch.setattr(A.lookup, "geocode", geocode)
    target = A._target([location], lambda message: None)
    assert queries == ["San Jose, United States"]
    assert target["state_code"] == "CA"
    assert target["city"] == "San Jose"


@pytest.fixture
def prepared_address_lookups(monkeypatch):
    targets = []
    monkeypatch.setattr(A.research, "_search", lambda *args, **kwargs: [])

    def unexpected_geocode(query):
        raise AssertionError(f"resolved states should not need geocoding: {query}")

    monkeypatch.setattr(A.lookup, "geocode", unexpected_geocode)

    def mapped_address(target, locations, log):
        targets.append(target)
        return {
            "location_policy": A.ADDRESS_LOCATION_POLICY,
            "jd_locations": locations,
            "source_url": "https://example.com/address",
            "state_code": target["state_code"],
            "address": {
                "line1": "123 Example Street",
                "line2": "",
                "city": target["city"],
                "state": target["state"],
                "postal_code": "12345",
                "country": "United States",
            },
        }

    monkeypatch.setattr(A, "_mapped_address", mapped_address)
    return targets


def _context(state="Minnesota", city="Saint Paul"):
    return {
        "_jd": {
            "locations": ["Corporate Headquarters, United States of America"],
            "locations_resolved": [{
                "city": city,
                "state": state,
                "country": "United States",
            }],
        },
        "_applicant": {},
    }


def test_headquarters_uses_resolved_state_and_reuses_selection(
    tmp_path, prepared_address_lookups,
):
    ctx = _context()
    first = A.prepare(ctx, tmp_path, None, log=lambda message: None)
    second = A.prepare(_context(), tmp_path, None, log=lambda message: None)

    assert first["address"]["city"] == "Saint Paul"
    assert first["address"]["state"] == "Minnesota"
    assert second == first
    assert len(prepared_address_lookups) == 1
    assert ctx["_application_address"] == first
    assert "Saint Paul" in ctx["APPLICANT"]


def test_changed_resolved_state_invalidates_cached_address(
    tmp_path, prepared_address_lookups,
):
    A.prepare(_context(), tmp_path, None, log=lambda message: None)
    selected = A.prepare(
        _context("Colorado", "Denver"), tmp_path, None, log=lambda message: None,
    )

    assert selected["address"]["city"] == "Denver"
    assert selected["state_code"] == "CO"
    assert len(prepared_address_lookups) == 2
    assert json.loads((tmp_path / "address.json").read_text()) == selected


def test_foreign_resolved_location_falls_back_to_original_us_state(
    tmp_path, prepared_address_lookups,
):
    ctx = _context()
    ctx["_jd"]["locations"] = ["Phoenix, AZ"]
    ctx["_jd"]["locations_resolved"] = [{
        "city": "Vancouver", "state": "British Columbia", "country": "Canada",
    }]

    selected = A.prepare(ctx, tmp_path, None, log=lambda message: None)

    assert selected["state_code"] == "AZ"
    assert selected["address"]["city"] == "Phoenix"


@pytest.mark.parametrize('location', ['Scottsdale, AZ', 'Scottsdale, Arizona', 'Scottsdale Arizona, United States'])
def test_explicit_job_city_wins_over_state_dictionary(monkeypatch, location):
    monkeypatch.setattr(A.lookup, 'geocode', lambda _: pytest.fail('Explicit city/state needs no lookup'))
    assert A._target([location], lambda _: None) == {'city': 'Scottsdale', 'state': 'Arizona', 'state_code': 'AZ'}


def test_dictionary_applies_only_when_no_city_is_given(monkeypatch):
    monkeypatch.setattr(A.lookup, 'geocode', lambda _: pytest.fail('State-only needs no lookup'))
    assert A._target(['Arizona'], lambda _: None)['city'] == 'Phoenix'
    assert A._target(['Arizona', 'Scottsdale, AZ'], lambda _: None)['city'] == 'Scottsdale'


def test_original_explicit_city_precedes_different_research_city(tmp_path, prepared_address_lookups):
    ctx = _context('Arizona', 'Phoenix')
    ctx['_jd']['locations'] = ['Scottsdale, AZ']
    assert A.prepare(ctx, tmp_path, None, log=lambda _: None)['address']['city'] == 'Scottsdale'


@pytest.mark.parametrize('legacy', [True, False])
def test_wrong_city_cache_is_rejected_even_with_same_jd_labels(tmp_path, prepared_address_lookups, legacy):
    ctx = _context('Arizona', 'Scottsdale')
    ctx['_jd']['locations'] = ['Scottsdale, AZ']
    A.prepare(ctx, tmp_path, None, log=lambda _: None)
    path = tmp_path/'address.json'; bad = json.loads(path.read_text())
    bad['address']['city'] = 'Phoenix'
    if legacy:
        bad['location_policy'] = 'state-known-city-v2'
        bad.pop('target')
    path.write_text(json.dumps(bad))
    selected = A.prepare(ctx, tmp_path, None, log=lambda _: None)
    assert selected['address']['city'] == 'Scottsdale'
    assert len(prepared_address_lookups) == 2


def test_city_only_original_cannot_be_replaced_by_a_different_research_city(monkeypatch):
    monkeypatch.setattr(A.lookup, 'geocode', lambda _: {'address': {'country_code':'us', 'state':'Arizona', 'city':'Scottsdale'}})
    assert A._target(['Scottsdale', 'Phoenix, Arizona'], lambda _: None)['city'] == 'Scottsdale'


def test_matching_resolved_city_avoids_redundant_geocoding(monkeypatch):
    monkeypatch.setattr(A.lookup, 'geocode', lambda _: pytest.fail('Already resolved same city'))
    assert A._target(['Scottsdale', 'Scottsdale, Arizona'], lambda _: None)['city'] == 'Scottsdale'


def test_unresolved_explicit_city_does_not_become_the_state_dictionary_city(monkeypatch):
    monkeypatch.setattr(A.lookup, 'geocode', lambda _: {'address': {'country_code':'us', 'state':'Arizona', 'city':'Phoenix'}})
    with pytest.raises(RuntimeError, match='city/state'):
        A._target(['Scottsdale', 'Arizona'], lambda _: None)
