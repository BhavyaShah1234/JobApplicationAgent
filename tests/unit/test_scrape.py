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


@pytest.mark.parametrize(
    "heading,field",
    [("Minimum requirements", "basic_qualifications"), ("Basic Qualifications", "basic_qualifications"),
     ("Preferred qualifications", "preferred_qualifications"), ("Nice to have", "preferred_qualifications"),
     ("Bonus points", "preferred_qualifications"), ("What you'll do", "responsibilities"),
     ("Responsibilities", "responsibilities"), ("Who you are", "values"), ("Benefits", "benefits"),
     ("What you'll need", "basic_qualifications"), ("What We Value", "values"),
     ("What We Require", "basic_qualifications"), ("Core Responsiblities", "responsibilities"),
     ("Technologies We Use", "technical_skills"), ("What you’ll be doing", "responsibilities"),
     ("What we need to see", "basic_qualifications"), ("Ways to stand out from the crowd", "preferred_qualifications"),
     ("What You’ll Enjoy", "benefits"), ("About Stripe", None)],
)
def test_classify_heading(heading, field):
    from jobagent.tools.scrape import classify_heading

    assert classify_heading(heading) == field


def test_apply_sections_replaces_lists_with_page_bullets():
    from jobagent.tools.scrape import apply_sections

    jd = JobDescription(title="SWE", company="Stripe", basic_qualifications=[],
                        preferred_qualifications=["model version"], technical_skills=["Go"])
    bullets = [{"heading": "Minimum requirements", "text": "BS in CS"},
               {"heading": "Minimum requirements", "text": "Programming experience"},
               {"heading": "Preferred qualifications", "text": "Code review"},
               {"heading": "Our offices", "text": "Dublin"}]
    assert apply_sections(jd, bullets) == ["Our offices"]
    assert jd.basic_qualifications == ["BS in CS", "Programming experience"]
    assert jd.preferred_qualifications == ["Code review"] and jd.technical_skills == ["Go"]


def test_model_items_misfiled_from_other_page_lists_are_dropped():
    from jobagent.tools.scrape import apply_sections

    # Lever-like: no preferred heading; the model copied the "What We Value" bullets into preferred.
    jd = JobDescription(title="SWE", company="Palantir",
                        preferred_qualifications=["Deep understanding of distributed systems.", "UK clearance"])
    apply_sections(jd, [{"heading": "What We Value", "text": "Deep understanding of distributed systems"}])
    assert jd.values == ["Deep understanding of distributed systems"]
    assert jd.preferred_qualifications == ["UK clearance"]


def test_jsonld_locations():
    from jobagent.tools.scrape import jsonld_locations

    assert jsonld_locations({"jobLocation": [
        {"address": {"addressLocality": "London", "addressCountry": "United Kingdom"}},
        {"address": {"addressLocality": "Denver", "addressRegion": "CO", "addressCountry": {"name": "US"}}},
    ]}) == ["London, United Kingdom", "Denver, CO, US"]
    assert jsonld_locations({"jobLocationType": "TELECOMMUTE"}) == ["Remote"]
    assert jsonld_locations({}) == []


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

        from jobagent.tools.scrape import _sections

        page.set_content("<main><h2>Who we are</h2><p>Intro</p><h3>Minimum requirements</h3><ul><li>BS in CS</li>"
                         "<li>Go or Java</li></ul><p><strong>Preferred qualifications</strong></p>"
                         "<ul><li>Code review</li></ul></main>")
        assert _sections(page, "main") == [
            {"heading": "Minimum requirements", "text": "BS in CS"},
            {"heading": "Minimum requirements", "text": "Go or Java"},
            {"heading": "Preferred qualifications", "text": "Code review"},
        ]
        browser.close()
