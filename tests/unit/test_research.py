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


@pytest.mark.parametrize('url,content_type',[
    ('https://assets.example.org/mission.pdf','application/pdf'),
    ('https://assets.example.org/document','application/octet-stream')])
def test_official_pdf_is_read_as_pdf_instead_of_a_browser_viewer(tmp_path,monkeypatch,url,content_type):
    from types import SimpleNamespace
    from jobagent.tools.tailor import compile_pdf
    pages,error=compile_pdf(r'\documentclass{article}\begin{document}Acme builds reliable software. Our mission is accessible tools.\end{document}',tmp_path)
    assert pages==1 and not error
    response=SimpleNamespace(status_code=200,url=url,headers={'content-type':content_type},
                             content=(tmp_path/'resume.pdf').read_bytes())
    monkeypatch.setattr(r.httpx,'get',lambda *a,**kw:response)
    monkeypatch.setattr(r,'_fetch_browser',lambda *a:pytest.fail('A PDF viewer cannot supply article text'))
    final,text=r._fetch(url)
    assert final==url and 'Acme builds reliable software.' in text and 'accessible tools' in text


def test_invalid_and_oversized_pdfs_are_not_treated_as_official_text(monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess,'run',lambda *a,**kw:pytest.fail('Reject invalid input before launching the reader'))
    assert r._pdf_text(b'<html>Verification required</html>')==''
    assert r._pdf_text(b'%PDF-'+b'x'*r.MAX_PDF_BYTES)==''


def _research_profile_fixture(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from jobagent import llm
    monkeypatch.setattr(r,'get_settings',lambda:SimpleNamespace(state_dir=tmp_path))
    draft=r.ProfileDraft(name='Acme',what_they_do=[r.Cited(text='Builds software',sources=[1])],
                         products=[r.Cited(text='Development tools',sources=[1])])
    monkeypatch.setattr(llm,'structured',lambda *a,**kw:SimpleNamespace(invoke=lambda _:draft))
    return tmp_path/'companies'/'acme.json'


def test_incomplete_fresh_cache_is_refetched_then_the_valid_result_is_reused(tmp_path,monkeypatch):
    from datetime import datetime,UTC
    path=_research_profile_fixture(tmp_path,monkeypatch);path.parent.mkdir()
    old=r.CompanyProfile(name='Acme',website='https://acme.example',
        what_they_do=[r.Claim(text='Builds software',sources=[1])],sources={1:{'kind':'wikipedia','url':'https://example.org'}},
        researched_at=datetime.now(UTC).isoformat())
    path.write_text(old.model_dump_json());calls=[]
    def gather(*a):
        calls.append(1);return 'https://acme.example',[{'kind':'official','url':'https://acme.example/about','text':'Builds software and development tools.'}]
    monkeypatch.setattr(r,'gather_sources',gather)
    assert r.usable_profile(r.research('Acme',log=lambda _:None)) and len(calls)==1
    assert r.usable_profile(r.research('Acme',log=lambda _:None)) and len(calls)==1


def test_failed_refresh_preserves_the_last_good_profile_and_skips_model_work(tmp_path,monkeypatch):
    from datetime import datetime,UTC
    from jobagent import llm
    path=_research_profile_fixture(tmp_path,monkeypatch);path.parent.mkdir()
    old=r.CompanyProfile(name='Acme',website='https://acme.example',
        what_they_do=[r.Claim(text='Builds software',sources=[1])],sources={1:{'kind':'official','url':'https://acme.example'}},
        researched_at=datetime.now(UTC).isoformat())
    original=old.model_dump_json();path.write_text(original);path.with_suffix('.md').write_text('Previous verified profile')
    monkeypatch.setattr(r,'gather_sources',lambda *a:('https://acme.example',[
        {'kind':'wikipedia','url':'https://example.org','text':'Builds software.'}]))
    monkeypatch.setattr(llm,'structured',lambda *a,**kw:pytest.fail('No official input means no model call'))
    with pytest.raises(RuntimeError,match='official company source'):r.research('Acme',refresh=True)
    assert path.read_text()==original and path.with_suffix('.md').read_text()=='Previous verified profile'


def test_a_profile_omitting_all_official_citations_is_not_published(tmp_path,monkeypatch):
    path=_research_profile_fixture(tmp_path,monkeypatch)
    monkeypatch.setattr(r,'gather_sources',lambda *a:('https://acme.example',[
        {'kind':'wikipedia','url':'https://example.org','text':'Builds software and development tools.'},
        {'kind':'official','url':'https://acme.example/about','text':'We build software and development tools.'}]))
    with pytest.raises(RuntimeError,match='did not cite'):r.research('Acme')
    assert not path.exists() and not path.with_suffix('.md').exists()


def test_strip_boilerplate():
    text = ("Intel creates world-changing technology.\nIntel uses cookies and similar technologies to improve your "
            "experience.\nWe collect device information for ad targeting.\nAccept All\nCustomer first: we listen.")
    assert r.strip_boilerplate(text) == "Intel creates world-changing technology.\nCustomer first: we listen."


def test_strip_boilerplate_catches_reworded_banner_text():
    text = ("Intel uses technologies to count device visits and traffic sources.\nWe collect aggregated data.\n"
            "Intel invents processors.")
    assert r.strip_boilerplate(text) == "Intel invents processors."


def test_the_employers_own_posting_is_an_official_source_when_its_website_cannot_be_read():
    """Peraton's site answers a script with 403; its iCIMS posting carries its own 'Peraton Overview'."""
    blurb = "Peraton is a next-generation national security company that drives missions of consequence spanning the globe and extending to the farthest reaches of the galaxy."
    src = r.posting_source({"url": "https://careers-peraton.icims.com/jobs/173607/x/job", "text": blurb})
    assert src == {"kind": "official", "url": "https://careers-peraton.icims.com/jobs/173607/x/job", "text": blurb}
    assert r.posting_source({"url": "https://x.test/j", "text": "too short"}) is None
    assert r.posting_source({"url": "", "text": blurb}) is None and r.posting_source(None) is None


def test_research_goes_on_with_the_posting_source_instead_of_failing_when_no_official_page_is_readable(monkeypatch, tmp_path):
    from types import SimpleNamespace
    blurb = "Peraton is a next-generation national security company that drives missions of consequence spanning the globe and beyond. " * 2
    monkeypatch.setattr(r, "gather_sources", lambda company, hint="": (None, [{"kind": "news", "url": "https://n.test/a", "text": "Peraton wins a contract"}]))
    monkeypatch.setattr(r, "get_settings", lambda: SimpleNamespace(state_dir=tmp_path))
    seen = {}
    def fake_structured(*a, **k):
        class Fake:
            def invoke(self, messages):
                seen["prompt"] = messages[-1].content
                raise RuntimeError("stop here: the sources were built")
        return Fake()
    import jobagent.llm as llm
    monkeypatch.setattr(llm, "structured", fake_structured)
    logs = []
    try:
        r.research("Peraton", posting={"url": "https://careers-peraton.icims.com/jobs/1/x/job", "text": blurb}, log=logs.append)
    except Exception:
        pass
    assert any("employer's own posting" in x for x in logs)
    assert "(official) https://careers-peraton.icims.com/jobs/1/x/job" in seen["prompt"]
    with pytest.raises(RuntimeError, match="official company source"):
        r.research("Peraton", log=logs.append)       # without the posting the old, strict behavior stays
