import json
import shutil

import pytest

from jobagent.state import JobDescription
from jobagent.tools.memory import PortalMemory, portal_key


@pytest.mark.parametrize(
    "url,key",
    [("https://job-boards.greenhouse.io/stripe/jobs/1", "greenhouse"),
     ("https://boards.greenhouse.io/acme/jobs/2", "greenhouse"),
     ("https://jobs.lever.co/palantir/abc", "lever"),
     ("https://nvidia.wd5.myworkdayjobs.com/en-US/X/job/Y", "workday"),
     ("https://jobs.ashbyhq.com/openai/abc", "ashby"),
     ("https://www.amazon.jobs/en/jobs/1/x", "amazon.jobs"),
     ("https://careers.example.co.uk/jobs/9", "example.co.uk")],
)
def test_portal_key(url, key):
    assert portal_key(url) == key


def test_memory_round_trip_and_notes(tmp_path):
    m = PortalMemory("lever", tmp_path)
    assert not m.known
    m.note("job description lives in `.posting-page`")
    m.note("job description lives in `.posting-page`")  # deduped
    m.record_visit("https://jobs.lever.co/a/1", ok=True, content_selector=".posting-page", has_jsonld=True)
    again = PortalMemory("lever", tmp_path)
    assert again.known and again.data["content_selector"] == ".posting-page" and len(again.data["notes"]) == 1
    assert "jobs.lever.co" in (tmp_path / "lever.md").read_text()


def test_missing_core():
    jd = JobDescription(title="SWE", company="Acme", locations=["Remote"],
                        responsibilities=["a", "b"], basic_qualifications=["c"])
    assert jd.missing_core() == []
    assert set(JobDescription(title="SWE", company="Acme").missing_core()) == {"responsibilities/qualifications",
                                                                              "locations"}


@pytest.mark.skipif(shutil.which("google-chrome") is None, reason="needs Chrome (run in the container)")
def test_jsonld_and_remembered_selector():
    from playwright.sync_api import sync_playwright

    from jobagent.tools.scrape import _best_text, _jsonld_jobposting, _jsonld_text

    posting = {"@context": "https://schema.org", "@type": "JobPosting", "title": "Backend Engineer",
               "description": "<p>Build <b>APIs</b></p><ul><li>Go</li></ul>",
               "hiringOrganization": {"name": "Acme"}}
    long = "Responsibilities. " * 80
    html = (f'<script type="application/ld+json">{json.dumps({"@graph": [posting]})}</script>'
            f'<main><div class="nav">menu</div><div class="job-description">{long}</div></main>')
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page()
        page.set_content(html)
        found = _jsonld_jobposting(page)
        assert found and found["title"] == "Backend Engineer"
        assert "Build\nAPIs" in _jsonld_text(found) or "APIs" in _jsonld_text(found)
        sel, text = _best_text(page, None)
        assert sel == ".job-description" and text.startswith("Responsibilities")
        assert _best_text(page, "main")[0] == "main"  # the remembered selector wins when it still has the text
        browser.close()
