import pytest

from jobagent.tools import locations as L

# (locations, title, verdict): a labelled set of postings' locations
CASES = [
    (["Austin, TX"], "", "us"), (["Santa Clara, California"], "", "us"), (["New York, NY, United States"], "", "us"),
    (["Remote - US"], "", "us"), (["Remote (US)"], "", "us"), (["Remote, United States"], "", "us"),
    (["Phoenix, Arizona"], "", "us"), (["Tempe, AZ"], "", "us"), (["Golden Valley, MN"], "", "us"),
    (["Washington, DC"], "", "us"), (["Seattle, Washington"], "", "us"), (["Hybrid - Mesa, AZ"], "", "us"),
    (["Austin Texas"], "", "us"), (["San Jose, CA"], "", "us"), (["Boston"], "", "us"), (["New Mexico"], "", "us"),
    (["Birmingham, AL"], "", "us"), (["Chicago, IL", "Dallas, TX"], "", "us"),
    (["Powai, Mumbai, India"], "", "non_us"), (["Hyderabad, IN"], "", "non_us"), (["London, UK"], "", "non_us"),
    (["Toronto, ON, Canada"], "", "non_us"), (["Vancouver, BC"], "", "non_us"), (["Berlin, Germany"], "", "non_us"),
    (["Bengaluru"], "", "non_us"), (["Remote - Europe"], "", "non_us"), (["EMEA"], "", "non_us"),
    (["Singapore"], "", "non_us"), (["Sydney, Australia"], "", "non_us"), (["Mexico City, Mexico"], "", "non_us"),
    (["Cambridge, UK"], "", "non_us"), (["Dublin, Ireland"], "", "non_us"), (["Tokyo"], "", "non_us"),
    (["Remote - Canada"], "", "non_us"), (["São Paulo, Brazil"], "", "non_us"), (["Pune, Maharashtra, India"], "", "non_us"),
    (["Remote"], "", "ambiguous"), ([], "", "ambiguous"), (["Remote", "Hybrid"], "", "ambiguous"),
    (["Austin, TX", "London, UK"], "", "ambiguous"), (["New York, NY", "Toronto, Canada"], "", "ambiguous"),
    (["Somewhere Nice"], "", "ambiguous"),
    # the title decides when the location doesn't
    (["Remote"], "Software Engineer (US)", "us"), (["Remote"], "Data Analyst - India", "non_us"),
    ([], "Account Executive, EMEA", "non_us"),
]


@pytest.mark.parametrize("locs,title,verdict", CASES)
def test_labelled_locations(locs, title, verdict):
    got = L.classify(locs, title)
    assert got.verdict == verdict, (locs, title, got)


def test_there_are_at_least_forty_labelled_locations():
    assert len(CASES) >= 40


def test_jsonld_country_decides_first():
    us = {"jobLocation": {"address": {"addressCountry": "US"}}}
    india = {"jobLocation": [{"address": {"addressCountry": {"name": "India"}}}]}
    both = {"jobLocation": [{"address": {"addressCountry": "US"}}, {"address": {"addressCountry": "GB"}}]}
    assert L.classify(["Powai, India"], "", us).verdict == "us"  # the structured field outranks the text
    assert L.classify(["Austin, TX"], "", india).verdict == "non_us"
    assert L.classify([], "", both).verdict == "ambiguous"
    remote = {"applicantLocationRequirements": {"@type": "Country", "name": "USA"}}
    assert L.classify(["Remote"], "", remote).verdict == "us"


def test_the_model_only_decides_what_the_cheap_signals_cannot():
    calls = []

    def judge(title, locs, snippet):
        calls.append(title)
        return L.Decision("us", "model", "the snippet says Denver")
    assert L.classify(["Austin, TX"], "x", judge=judge).how == "location text"
    assert calls == []
    assert L.classify(["Somewhere Nice"], "x", judge=judge).how == "model" and calls == ["x"]
    assert L.classify(["Remote"], "x", judge=lambda *a: (_ for _ in ()).throw(RuntimeError())).verdict == "ambiguous"
