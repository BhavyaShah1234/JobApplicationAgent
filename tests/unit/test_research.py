import pytest

from jobagent.tools import research as r


@pytest.mark.parametrize(
    "name,expected",
    [("Cafe Valley, Inc.", "cafe-valley"), ("KPMG LLP", "kpmg"), ("Intel Corporation", "intel"),
     ("General Mills", "general-mills"), ("Palantir Technologies", "palantir-technologies")],
)
def test_slug(name, expected):
    assert r.slug(name) == expected


def test_uncited_or_unknown_sources_are_dropped():
    claims = [r.Claim(text="Bakes cupcakes", sources=[1]), r.Claim(text="Invented", sources=[]),
              r.Claim(text="Cites a source that wasn't fetched", sources=[9]), r.Claim(text="Mixed", sources=[2, 9])]
    kept = r._valid(claims, {1, 2})
    assert [c.text for c in kept] == ["Bakes cupcakes", "Mixed"] and kept[1].sources == [2]


def test_find_website_skips_aggregators(monkeypatch):
    hits = [{"href": "https://en.wikipedia.org/wiki/General_Mills"}, {"href": "https://www.linkedin.com/company/general-mills"},
            {"href": "https://genmills.wd1.myworkdayjobs.com/x"}, {"href": "https://www.generalmills.com/about"}]
    monkeypatch.setattr(r, "_search", lambda q, n=8: hits)
    assert r.find_website("General Mills") == "https://www.generalmills.com"


def test_find_website_none_when_no_name_match(monkeypatch):
    monkeypatch.setattr(r, "_search", lambda q, n=8: [{"href": "https://www.unrelated-bakery.com/"}])
    assert r.find_website("DOWL") is None


def test_render_md_lists_cited_sources():
    p = r.CompanyProfile(name="Cafe Valley", what_they_do=[r.Claim(text="Bakes cakes", sources=[1])],
                         website="https://cafevalley.com", researched_at="2026-10-22T00:00:00+00:00",
                         sources={1: {"kind": "official", "url": "https://cafevalley.com"}})
    md = r.render_md(p)
    assert "- Bakes cakes [1]" in md and "1. (official) https://cafevalley.com" in md
