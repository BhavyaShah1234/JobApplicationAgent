import json

from jobagent.config import get_settings
from jobagent.tools import careers as C


def test_ats_families_and_board_tokens():
    assert C.ats_of("https://boards.greenhouse.io/carvana/jobs/1") == "greenhouse"
    assert C.board_of("https://job-boards.greenhouse.io/carvana/jobs/8188615")["token"] == "carvana"
    assert C.board_of("https://boards.greenhouse.io/embed/job_board?for=acme")["token"] == "acme"
    assert C.board_of("https://jobs.lever.co/palantir/abc-123")["token"] == "palantir"
    assert C.board_of("https://jobs.ashbyhq.com/mercor/799ecfcc")["token"] == "mercor"
    wd = C.board_of("https://intel.wd1.myworkdayjobs.com/en-US/External/job/US-CA/Engineer_JR1")
    assert wd == {"family": "workday", "token": "intel/wd1/External", "url": "https://intel.wd1.myworkdayjobs.com/en-US"}
    assert C.ats_of("https://www.acme.com/careers") == "" and C.board_of("https://www.acme.com/careers") is None


def test_the_board_behind_a_careers_page_is_found_in_links_frames_and_scripts():
    page = """<html><body><a href="/about">About</a><iframe src="https://boards.greenhouse.io/embed/job_board?for=acme">
      </iframe></body></html>"""
    assert C.scan_for_board(page, "https://www.acme.com/careers")["token"] == "acme"
    page2 = '<a href="https://jobs.lever.co/acme">Open roles</a>'
    assert C.scan_for_board(page2, "https://acme.com/c")["family"] == "lever"
    assert C.scan_for_board("<a href='/x'>x</a>", "https://acme.com/c") is None


def test_search_result_must_be_the_companys_own_careers_page():
    hits = [{"href": "https://www.indeed.com/cmp/Acme/jobs", "title": "Acme jobs"},
            {"href": "https://www.acme.com/about", "title": "About"},
            {"href": "https://news.example.com/acme-careers-story", "title": "story"},
            {"href": "https://careers.acme.com/us/en", "title": "Careers at Acme"}]
    assert C.pick_from_search(hits, "Acme Corp")["url"] == "https://careers.acme.com/us/en"
    assert C.pick_from_search([{"href": "https://boards.greenhouse.io/acme", "title": "x"}], "Acme")["url"].endswith("/acme")
    assert C.pick_from_search([{"href": "https://careers.other.com/", "title": "x"}], "Acme") is None


def test_footer_links_prefer_careers_hosts_and_ignore_the_rest_of_the_page():
    page = """<html><body><a href="/careers-fake-in-body">Careers</a><footer>
      <a href="/privacy">Privacy</a><a href="/company/careers">Careers</a>
      <a href="https://jobs.acme.com/">Join our team</a><a href="/blog">Blog</a></footer></body></html>"""
    got = C.footer_links(page, "https://www.acme.com")
    assert got == ["https://jobs.acme.com/", "https://www.acme.com/company/careers"]
    # no <footer>: the last 15% of the page's links
    bare = "<html><body>" + "".join(f'<a href="/p{i}">Page {i}</a>' for i in range(30)) + '<a href="/jobs">Jobs</a></body></html>'
    assert C.footer_links(bare, "https://acme.com") == ["https://acme.com/jobs"]


def test_resolve_search_then_footer_then_model_and_caches(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    get_settings.cache_clear()
    pages = {"https://careers.acme.com/": ("https://careers.acme.com/", '<a href="https://boards.greenhouse.io/acme">x</a>', 200, False),
             "https://www.beta.com/": ("https://www.beta.com/", '<footer><a href="/careers">Careers</a></footer>', 200, False),
             "https://www.beta.com/careers": ("https://www.beta.com/careers", "<html></html>", 200, False),
             "https://x.com/gamma-jobs": ("https://x.com/gamma-jobs", "<html>Just a moment</html>", 403, True)}
    monkeypatch.setattr(C, "_get", lambda url, timeout=20: pages.get(url, (url, "", 404, False)))
    monkeypatch.setattr(C, "_browser_html", lambda url: "")
    searches = {"Acme careers": [{"href": "https://careers.acme.com/", "title": "Careers"}], "Beta careers": [],
                "Gamma careers": [{"href": "https://x.com/gamma-jobs", "title": "Gamma jobs"}]}
    search = lambda q, n=8: searches.get(q, [])  # noqa: E731
    a = C.resolve("Acme", search=search, log=lambda m: None)
    assert (a["how"], a["board"]["family"], a["url"]) == ("search", "greenhouse", "https://careers.acme.com/")
    b = C.resolve("Beta", website="https://www.beta.com/", search=search, log=lambda m: None)
    assert (b["how"], b["url"]) == ("footer", "https://www.beta.com/careers")
    g = C.resolve("Gamma", search=search, log=lambda m: None, pick_llm=lambda c, h: h[0])
    assert g["how"].startswith("llm") and g["blocked"] is True  # reported, never worked around
    # the second call is the cache: no search
    assert C.resolve("Acme", search=lambda *a: (_ for _ in ()).throw(AssertionError("searched"))) == a
    assert json.loads((tmp_path / "companies" / "acme.careers.json").read_text())["how"] == "search"
    get_settings.cache_clear()


def test_a_greenhouse_board_is_guessed_only_when_its_own_name_matches():
    class R:
        def __init__(self, code, name=""):
            self.status_code, self._n = code, name
        def json(self):
            return {"name": self._n}
    boards = {"carvana": R(200, "Carvana"), "stripe": R(200, "Stripe Payments Ltd"), "delta": R(200, "Delta Dental")}
    get = lambda u: boards.get(u.rsplit("/", 1)[-1], R(404))  # noqa: E731
    assert C.guess_greenhouse("Carvana", "https://www.carvana.com", get)["token"] == "carvana"
    assert C.guess_greenhouse("Delta Air Lines", "https://www.delta.com", get) is None  # same token, another company
    assert C.guess_greenhouse("Nobody", "", get) is None


def test_a_miss_is_not_cached(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    get_settings.cache_clear()
    monkeypatch.setattr(C, "_get", lambda url, timeout=20: (url, "", 404, False))
    monkeypatch.setattr(C, "_browser_html", lambda url: "")
    miss = C.resolve("Nowhere", website="https://nowhere.example", search=lambda q, n=8: [], pick_llm=None, log=lambda m: None)
    assert miss["url"] == "" and C.cached("Nowhere") is None
    get_settings.cache_clear()
