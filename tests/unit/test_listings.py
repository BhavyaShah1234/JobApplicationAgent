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
