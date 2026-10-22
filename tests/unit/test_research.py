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


def test_parse_cited_keeps_only_valid_citations():
    items = ["Bakes cakes and muffins [2][3].", "Invented with no source", "Cites an unfetched source [9]",
             "Mixed [1][9]"]
    claims = r.parse_cited(items, {1, 2, 3})
    assert [(c.text, c.sources) for c in claims] == [("Bakes cakes and muffins", [2, 3]), ("Mixed", [1])]


def test_parse_news():
    news = r.parse_news(["2026-10-20 Q3 results — Sales fell 3% [4]", "Undated headline [2]", "No source"], {2, 4})
    assert [(n.date, n.title, n.summary, n.sources) for n in news] == [
        ("2026-10-20", "Q3 results", "Sales fell 3%", [4]), (None, "Undated headline", "", [2])]
    dated = r.parse_news(["CEO named [2]"], {2}, {2: "2026-10-01"})
    assert dated[0].date == "2026-10-01" and dated[0].summary == ""


@pytest.mark.parametrize(
    "text,hit",
    [("DOWL wins bridge contract in Anchorage", True), ("@PJ_Dowl replied", False), ("DLW1DOWL quote", False),
     ("Dow Jones advanced", False), ("Café Valley expands", False), ("Cafe Valley expands in Marion", True)],
)
def test_mentions_whole_name(text, hit):
    company = "DOWL" if "ow" in text.lower() and "valley" not in text.lower() else "Cafe Valley, Inc."
    assert r.mentions(company, text) is hit


@pytest.mark.parametrize("name,core", [("Cafe Valley, Inc.", "Cafe Valley"), ("KPMG LLP", "KPMG"),
                                        ("Palantir Technologies", "Palantir"), ("General Mills", "General Mills")])
def test_core_name(name, core):
    assert r.core_name(name) == core


def test_about_pages_accept_other_official_domains(monkeypatch):
    hits = [{"href": "https://www.aboutamazon.com/about-us", "title": "About Amazon"},
            {"href": "https://en.wikipedia.org/wiki/Amazon_(company)", "title": "Amazon - Wikipedia"},
            {"href": "https://www.amazon.jobs/en/jobs/1", "title": "Jobs"},
            {"href": "https://www.aboutamazon.com/news/company-news", "title": "Company news"}]
    monkeypatch.setattr(r, "_search", lambda q, n=8: hits)
    assert r.about_pages("Amazon", "https://www.amazon.com") == ["https://www.aboutamazon.com/about-us",
                                                                 "https://www.aboutamazon.com/news/company-news"]


def test_fetch_falls_back_to_browser_when_text_is_thin(monkeypatch):
    class Thin:
        status_code, url, text = 200, "https://www.intel.com/", "<html><body><div id=app></div></body></html>"

    monkeypatch.setattr(r.httpx, "get", lambda *a, **k: Thin())
    monkeypatch.setattr(r, "_fetch_browser", lambda url: ("https://www.intel.com/content/www/us/en/homepage.html", "x" * 900))
    assert r._fetch("https://www.intel.com") == ("https://www.intel.com/content/www/us/en/homepage.html", "x" * 900)


def test_strip_boilerplate():
    text = ("Intel creates world-changing technology.\nIntel uses cookies and similar technologies to improve your "
            "experience.\nWe collect device information for ad targeting.\nAccept All\nCustomer first: we listen.")
    assert r.strip_boilerplate(text) == "Intel creates world-changing technology.\nCustomer first: we listen."


def test_strip_boilerplate_catches_reworded_banner_text():
    text = ("Intel uses technologies to count device visits and traffic sources.\nWe collect aggregated data.\n"
            "Intel invents processors.")
    assert r.strip_boilerplate(text) == "Intel invents processors."
