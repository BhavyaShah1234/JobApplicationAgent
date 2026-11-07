import json

from jobagent.tools import places


def test_written_places_are_kept_and_jargon_is_not_a_place():
    assert places.written_place("Austin, TX")["state"] == "Texas"
    assert places.written_place("Minneapolis, Minnesota, United States")["city"] == "Minneapolis"
    assert places.written_place("Corporate Headquarters, United States of America") is None
    assert places.JARGON.search("Corporate Headquarters") and places.JARGON.search("Main Campus")
    assert places.JARGON.search("HQ - San Francisco") and not places.JARGON.search("Tempe, AZ")


def test_jargon_resolves_to_the_profiles_headquarters_or_named_site(tmp_path, monkeypatch):
    settings = type("S", (), {"state_dir": tmp_path})()
    monkeypatch.setattr(places, "get_settings", lambda: settings)
    (tmp_path / "companies").mkdir()
    (tmp_path / "companies" / "acme.json").write_text(json.dumps({
        "name": "Acme", "headquarters": {"city": "Golden Valley", "state": "Minnesota", "country": "United States"},
        "sites": [{"name": "Innovation Center", "city": "Boise", "state": "Idaho", "country": "United States"}]}))
    monkeypatch.setattr(places, "lookup_headquarters", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no lookup")))
    jd = {"company": "Acme", "locations": ["Corporate Headquarters, United States of America",
                                           "Innovation Center (HQ campus)", "Austin, TX"]}
    got = places.resolve(jd, "acme", log=lambda m: None)
    assert (got[0]["city"], got[0]["state"]) == ("Golden Valley", "Minnesota")
    assert got[1]["city"] == "Boise" and got[2]["city"] == "Austin"
    assert jd["locations_resolved"] == got
