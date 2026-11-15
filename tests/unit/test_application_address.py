import json

import pytest

from jobagent.tools import application_address as A


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


def _context(state="Minnesota"):
    return {
        "_jd": {
            "locations": ["Corporate Headquarters, United States of America"],
            "locations_resolved": [{
                "city": "Headquarters City",
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

    assert first["address"]["city"] == "Minneapolis"
    assert first["address"]["state"] == "Minnesota"
    assert second == first
    assert len(prepared_address_lookups) == 1
    assert ctx["_application_address"] == first
    assert "Minneapolis" in ctx["APPLICANT"]


def test_changed_resolved_state_invalidates_cached_address(
    tmp_path, prepared_address_lookups,
):
    A.prepare(_context(), tmp_path, None, log=lambda message: None)
    selected = A.prepare(
        _context("Colorado"), tmp_path, None, log=lambda message: None,
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
