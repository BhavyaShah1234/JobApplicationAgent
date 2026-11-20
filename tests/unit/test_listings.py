import pytest

from jobagent.tools import listings as L


def test_greenhouse_lever_ashby_smartrecruiters_parsers():
    gh = {"jobs": [{"id": 8188615, "title": "Data Product Engineer", "location": {"name": "Tempe, AZ"},
                    "absolute_url": "https://www.carvana.com/careers/apply?gh_jid=8188615", "updated_at": "2026-11-01T10:00:00-04:00", "requisition_id": "See Opening ID"}]}
    a = L.parse_greenhouse(gh, "Carvana")[0]
    assert (a.title, a.locations, a.req_id, a.posted, a.portal) == ("Data Product Engineer", ["Tempe, AZ"], "8188615", "2026-11-01", "greenhouse")
    lv = [{"id": "abc", "text": "ML Engineer", "hostedUrl": "https://jobs.lever.co/x/abc", "createdAt": 1767225600000,
           "categories": {"location": "Palo Alto, CA", "allLocations": ["Palo Alto, CA", "Remote - US"]}, "descriptionPlain": "Train models"}]
    b = L.parse_lever(lv, "X")[0]
    assert b.locations == ["Palo Alto, CA", "Remote - US"] and b.posted == "2026-01-01" and b.snippet == "Train models"
    ab = {"jobs": [{"id": "1", "title": "SWE", "location": "Remote", "jobUrl": "https://jobs.ashbyhq.com/m/1", "publishedAt": "2026-10-30T00:00:00Z",
                    "secondaryLocations": [{"location": "Austin, TX"}]}, {"id": "2", "title": "Hidden", "isListed": False}]}
    c = L.parse_ashby(ab, "M")
    assert len(c) == 1 and c[0].locations == ["Remote", "Austin, TX"]
    sr = {"content": [{"id": "7", "name": "Data Analyst", "refNumber": "REF9", "location": {"city": "Austin", "region": "TX", "country": "us"},
                       "releasedDate": "2026-10-01T00:00:00Z"}]}
    d = L.parse_smartrecruiters(sr, "S", "sco")[0]
    assert d.url == "https://jobs.smartrecruiters.com/sco/7" and d.locations == ["Austin, TX, US"] and d.req_id == "REF9"


def test_workday_rows_get_the_sites_job_url_and_requisition_id():
    data = {"jobPostings": [{"title": "Software Engineer - Datacenter", "externalPath": "/job/US-California-Santa-Clara/Software-Engineer---Datacenter_JR0287686",
                             "locationsText": "US, California, Santa Clara", "postedOn": "Posted 3 Days Ago", "bulletFields": ["JR0287686"]}]}
    w = L.parse_workday(data, "Intel", "intel.wd1.myworkdayjobs.com", "External")[0]
    assert w.url == "https://intel.wd1.myworkdayjobs.com/en-US/External/job/US-California-Santa-Clara/Software-Engineer---Datacenter_JR0287686"
    assert w.req_id == "JR0287686" and w.locations == ["US, California, Santa Clara"]


def test_workday_spotlight_labels_do_not_merge_distinct_requisitions():
    data = {"jobPostings": [
        {"title": "Software Engineer", "externalPath": "/job/Phoenix/Software-Engineer_JR123",
         "bulletFields": ["Spotlight Job", "JR123"]},
        {"title": "Data Engineer", "externalPath": "/job/Phoenix/Data-Engineer_JR456",
         "bulletFields": ["Spotlight Job", "JR456"]},
        {"title": "ML Engineer", "externalPath": "/job/Phoenix/ML-Engineer",
         "bulletFields": ["Full time", "R789"]},
    ]}
    rows = L.parse_workday(data, "Acme", "acme.wd1.myworkdayjobs.com", "Careers")
    assert [r.req_id for r in rows] == ["JR123", "JR456", "R789"]


def test_workday_is_searched_once_per_target_role_and_paged_until_short():
    calls = []

    def fake(url, method="GET", body=None, timeout=25):
        calls.append((body["searchText"], body["offset"]))
        n = 20 if (body["searchText"] == "Software Engineer" and body["offset"] == 0) else 3
        return {"jobPostings": [{"title": f"T{body['searchText']}{body['offset']}{i}", "externalPath": f"/job/x/J_{body['offset']}{i}{body['searchText'][:2]}"} for i in range(n)]}
    careers = {"board": {"family": "workday", "token": "acme/wd5/Careers"}, "url": "https://acme.wd5.myworkdayjobs.com/Careers"}
    got = L.collect(careers, "Acme", ["Software Engineer", "Data Scientist"], get_json=fake, log=lambda m: None)
    assert calls == [("Software Engineer", 0), ("Software Engineer", 20), ("Data Scientist", 0)]
    assert len(got) == 26 and all(g.url.startswith("https://acme.wd5.myworkdayjobs.com/en-US/Careers/job/") for g in got)


def test_generic_pages_give_job_links_without_locations():
    page = """<a href="/jobs/1234-software-engineer">Software Engineer, Payments</a><a href="/privacy">Privacy policy</a>
      <a href="/careers/search?q=">Search all jobs</a><a href="/careers/us/data-analyst-77">Data Analyst</a><a href="/jobs/9">Apply now</a>"""
    got = L.parse_dom(page, "https://acme.com/careers", "Acme")
    assert [g.title for g in got] == ["Software Engineer, Payments", "Data Analyst"] and all(g.locations == [] for g in got)
    out = L.collect({"url": "https://acme.com/careers"}, "Acme", get_html=lambda u: page, log=lambda m: None)
    assert len(out) == 2


def test_a_refusal_is_reported_not_worked_around(monkeypatch):
    class R:
        status_code, text = 403, "Access denied"
    monkeypatch.setattr(L.httpx, "request", lambda *a, **k: R())
    with pytest.raises(L.Blocked):
        L._json("https://boards-api.greenhouse.io/v1/boards/x/jobs")


def test_no_careers_page_means_no_listings():
    assert L.collect({"url": "", "board": None}, "Nobody") == []
    assert L.target_queries({"roles": [{"title": "Software Engineer"}, {"title": "Software Engineer"}, {"id": "x"}]}) == ["Software Engineer"]


def test_wordpress_style_job_links_carry_the_requisition_and_the_city_from_the_title():
    page = ('<a href="https://cafevalleyjobs.com/sanitation-manager-marion-in-180034/">Sanitation Manager Job – Marion, IN</a>'
            '<a href="https://cafevalleyjobs.com/about-us/">About us</a>'
            '<a href="https://cafevalleyjobs.com/food-safety-technician-phoenix-az-179452/">Food Safety Technician Job – Phoenix, AZ</a>')
    got = L.parse_dom(page, "https://cafevalleyjobs.com/", "Cafe Valley")
    assert [(g.req_id, g.locations) for g in got] == [("180034", ["Marion, IN"]), ("179452", ["Phoenix, AZ"])]


# --- Workable, Rippling and Gem (shapes taken from the live public boards, 2026-11-20) -----------------------------

def test_workable_listings_get_the_apply_url_place_and_remote_flag():
    data = {"nextPage": "tok", "results": [
        {"shortcode": "19A136F8E2", "title": "Open-Source Machine Learning Engineer - US Remote", "remote": True, "state": "published", "published": "2026-05-29T00:00:00.000Z",
         "location": {"city": "New York", "region": "New York", "country": "United States", "countryCode": "US"}},
        {"shortcode": "INT1", "title": "Internal only", "isInternal": True, "state": "published", "location": {}},
        {"shortcode": "DRAFT", "title": "Draft", "state": "draft", "location": {}}]}
    got = L.parse_workable(data, "Hugging Face", "huggingface")
    assert len(got) == 1
    a = got[0]
    assert (a.url, a.locations, a.posted, a.req_id, a.snippet, a.portal) == (
        "https://apply.workable.com/huggingface/j/19A136F8E2/", ["New York, United States"], "2026-05-29", "19A136F8E2", "Remote", "workable")


def test_workable_pages_are_followed_with_the_page_token_and_stop_when_there_is_none():
    calls = []

    def get_json(url, method="GET", body=None, **kw):
        calls.append((url, method, dict(body or {})))
        if "token" not in (body or {}):
            return {"nextPage": "P2", "results": [{"shortcode": "A", "title": "One", "state": "published", "location": {"country": "United States"}}]}
        return {"results": [{"shortcode": "B", "title": "Two", "state": "published", "location": {"country": "United States"}}]}
    careers = {"board": {"family": "workable", "token": "acme", "url": "https://apply.workable.com/acme"}, "url": "https://apply.workable.com/acme"}
    got = L.collect(careers, "Acme", get_json=get_json)
    assert [x.req_id for x in got] == ["A", "B"] and calls[0][1] == "POST" and calls[1][2]["token"] == "P2" and len(calls) == 2


def test_rippling_merges_the_same_posting_listed_once_per_location():
    items = [{"id": "x1", "name": "ML Engineer", "url": "https://ats.rippling.com/rippling/jobs/x1", "department": {"name": "AI"},
              "locations": [{"name": "San Francisco, CA", "city": "San Francisco", "state": "California", "country": "United States"}]},
             {"id": "x1", "name": "ML Engineer", "url": "https://ats.rippling.com/rippling/jobs/x1", "department": {"name": "AI"},
              "locations": [{"name": "Seattle, WA", "city": "Seattle", "state": "Washington", "country": "United States"}]},
             {"id": "x2", "name": "Remote role", "url": "https://ats.rippling.com/rippling/jobs/x2", "locations": [{"name": "Remote (US)"}]}]
    got = L.parse_rippling(items, "Rippling")
    assert [x.req_id for x in got] == ["x1", "x2"]
    assert got[0].locations == ["San Francisco, California, United States", "Seattle, Washington, United States"] and got[1].locations == ["Remote (US)"]
    assert got[0].snippet == "AI" and got[0].portal == "rippling"


def test_rippling_pages_are_read_until_the_last_one():
    pages = {0: {"items": [{"id": "a", "name": "A", "url": "u/a", "locations": []}], "totalPages": 2}, 1: {"items": [{"id": "b", "name": "B", "url": "u/b", "locations": []}], "totalPages": 2}}
    seen = []

    def get_json(url, *a, **k):
        page = int(url.split("page=")[1].split("&")[0])
        seen.append(page)
        return pages[page]
    got = L.collect({"board": {"family": "rippling", "token": "rippling"}, "url": "https://ats.rippling.com/rippling"}, "Rippling", get_json=get_json)
    assert [x.req_id for x in got] == ["a", "b"] and seen == [0, 1]


def test_gem_listings():
    data = [{"absolute_url": "https://jobs.gem.com/retool/4003629005", "title": "Software Engineer", "first_published_at": "2022-03-25T18:46:39.000Z",
             "id": "4003629005", "requisition_id": "16", "location": {"name": "San Francisco, United States"}, "content_plain": "ABOUT RETOOL"}]
    a = L.parse_gem(data, "Retool")[0]
    assert (a.url, a.locations, a.posted, a.req_id, a.snippet, a.portal) == (
        "https://jobs.gem.com/retool/4003629005", ["San Francisco, United States"], "2022-03-25", "16", "ABOUT RETOOL", "gem")


def test_the_new_boards_are_recognised_from_their_addresses_and_marketing_sites_are_not():
    from jobagent.tools.careers import board_of
    assert board_of("https://apply.workable.com/huggingface/j/19A136F8E2/")["token"] == "huggingface"
    assert board_of("https://ats.rippling.com/rippling/jobs/abc") == {"family": "rippling", "token": "rippling", "url": "https://ats.rippling.com/rippling"}
    assert board_of("https://jobs.gem.com/retool/4003629005")["family"] == "gem"
    assert board_of("https://www.rippling.com/careers") is None and board_of("https://www.gem.com/pricing") is None
