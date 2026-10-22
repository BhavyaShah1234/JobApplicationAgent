import io
import shutil
import zipfile

import pytest

from jobagent.state import JobDescription
from jobagent.tools import jdtext

HTML = """
<div>
  <p>Cafe Valley is a leading bakery.</p>
  <h3>Job Description</h3>
  <p>The Project Engineer manages capital projects.</p>
  <p><strong>Duties &amp; Responsibilities:</strong></p>
  <ul><li>Manage projects from concept to closeout.</li><li>Develop  scopes and   schedules.</li></ul>
  <p><b>You might thrive in this role if you</b></p>
  <ul><li>Have shipped production ML systems</li></ul>
  <p><b>Requirements:</b><br>Bachelor's degree in engineering<br>5 years of experience</p>
  <div style="display:none">hidden duplicate</div>
  <script>var x = 1;</script>
</div>
"""

EXPECTED = """Cafe Valley is a leading bakery.

Job Description

The Project Engineer manages capital projects.

Duties & Responsibilities

- Manage projects from concept to closeout.
- Develop scopes and schedules.

You might thrive in this role if you

- Have shipped production ML systems

Requirements

Bachelor's degree in engineering
5 years of experience
"""


@pytest.fixture(scope="module")
def page():
    if shutil.which("google-chrome") is None:
        pytest.skip("needs Chrome (run in the container)")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        yield browser.new_page()
        browser.close()


def test_html_string_rendered_as_is(page):
    text = jdtext.render(jdtext.blocks(page, html=HTML))
    assert text == EXPECTED  # inline-hidden elements and scripts dropped even without rendering


def test_live_element_skips_hidden_and_keeps_structure(page):
    page.set_content(f'<main id="jd">{HTML}</main><footer>Share this job</footer>')
    text = jdtext.render(jdtext.blocks(page, selector="#jd"))
    assert text == EXPECTED and "Share this job" not in text


def test_sections(page):
    secs = jdtext.sections(jdtext.blocks(page, html=HTML))
    assert [s.heading for s in secs] == ["", "Job Description", "Duties & Responsibilities",
                                         "You might thrive in this role if you", "Requirements"]
    assert secs[2].bullets == ["Manage projects from concept to closeout.", "Develop scopes and schedules."]
    assert secs[4].items == ["Bachelor's degree in engineering", "5 years of experience"]


def test_attachment_links(page):
    page.set_content('<div id="jd"><a href="/files/Job_Description_Project_Engineer.pdf">Job Description</a>'
                     '<a href="/files/TalentCare-Terms-of-Use-2021.pdf">Terms</a>'
                     '<a href="/files/privacy.docx">Privacy notice</a><a href="/apply">Apply</a></div>')
    links = jdtext.attachment_links(page, "#jd")
    assert [label for _, label in links] == ["Job Description"]


def test_docx_text():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", "<w:document><w:body><w:p><w:r><w:t>Duties</w:t></w:r></w:p>"
                                        "<w:p><w:r><w:t>Lead installs</w:t></w:r></w:p></w:body></w:document>")
    assert jdtext.attachment_text(buf.getvalue(), "x.docx").splitlines() == ["Duties", "Lead installs"]


def test_classification_falls_back_to_heading_rules(monkeypatch):
    from jobagent import llm

    def broken(*a, **k):
        raise ConnectionError("voyager down")

    monkeypatch.setattr(llm, "structured", broken)
    secs = [jdtext.Section("Minimum requirements", ["BS"]), jdtext.Section("Perks", ["401k"]),
            jdtext.Section("About us", [], ["We bake."])]
    assert jdtext.classify_sections(secs, log=lambda _: None) == {0: "basic_qualifications", 1: "benefits", 2: "other"}


def _b(kind, text):
    return jdtext.Block(kind, text)


def test_choose_source_prefers_the_fuller_page_copy():
    from jobagent.tools.scrape import choose_source

    overview = [_b("h", "Who Is Cafe Valley"), _b("p", "x" * 1500)]                    # JSON-LD: overview only
    page = overview + [_b("h", "Duties"), _b("li", "y" * 400), _b("h", "Requirements"), _b("li", "z" * 400)]
    assert choose_source(dom=page, ld=overview) is page
    flat_ld = [_b("p", "a" * 3000)]                                                  # Lever: headings lost
    structured_dom = [_b("h", "Core Responsibilities"), _b("li", "a" * 1000), _b("h", "What We Require"), _b("li", "b" * 900)]
    assert choose_source(dom=structured_dom, ld=flat_ld) is structured_dom
    thin_dom = [_b("p", "Apply now")]
    assert choose_source(dom=thin_dom, ld=flat_ld) is flat_ld
    assert choose_source(dom=[], ld=flat_ld) is flat_ld and choose_source(dom=page, ld=[]) is page


def test_completeness_problems_do_not_trust_the_labels():
    from jobagent.tools.scrape import completeness_problems

    jd = JobDescription(title="PE", company="Cafe Valley", locations=["Marion, IN"], benefits=["PTO"])
    secs = [jdtext.Section("Requirements", ["Bachelor's degree"]), jdtext.Section("What You'll Enjoy", ["PTO"])]
    labels = {0: "benefits", 1: "benefits"}  # the model mislabelled the requirements
    problems = completeness_problems(jd, secs, labels, "short", "p" * 5000)
    assert any("requirements heading" in p for p in problems)
    assert any("both responsibilities and qualifications" in p for p in problems)
    assert any("jd.txt looks short" in p for p in problems)
    ok = JobDescription(title="PE", company="CV", locations=["X"], basic_qualifications=["Bachelor's degree"])
    assert completeness_problems(ok, secs, {0: "basic_qualifications", 1: "benefits"}, "t" * 4000, "p" * 5000) == []


def test_apply_labeled_fills_lists_verbatim():
    from jobagent.tools.scrape import apply_labeled

    jd = JobDescription(title="PE", company="Cafe Valley", preferred_qualifications=["Have shipped production ML systems"],
                        technical_skills=["AutoCAD"])
    secs = [jdtext.Section("Duties", ["Manage projects"]), jdtext.Section("You might thrive if", ["Have shipped production ML systems"]),
            jdtext.Section("Requirements", [], ["Bachelor's degree", "5 years"]), jdtext.Section("Overview", [], ["Role text."])]
    labels = {0: "responsibilities", 1: "preferred_qualifications", 2: "basic_qualifications", 3: "role_summary"}
    apply_labeled(jd, secs, labels)
    assert jd.responsibilities == ["Manage projects"] and jd.basic_qualifications == ["Bachelor's degree", "5 years"]
    assert jd.preferred_qualifications == ["Have shipped production ML systems"]
    assert jd.role_summary == "Role text." and jd.technical_skills == ["AutoCAD"]


NVIDIA_LIKE = """
<div><p>We are looking for a New College Graduate SoC Software Engineer.</p>
<p><span><b>What you’ll be doing:</b></span></p><ul><li>Develop SoC software</li></ul>
<p>What we need to see:</p><ul><li>BS in CS</li><li>C/C++</li></ul>
<p><span><span><strong>Ways to stand out from the crowd:</strong></span></span></p><ul><li>Linux kernel</li></ul>
<p>Your base salary will be determined based on your location.</p></div>
"""


def test_nested_bold_and_colon_headings(page):
    secs = jdtext.sections(jdtext.blocks(page, html=NVIDIA_LIKE))
    assert [s.heading for s in secs] == ["", "What you’ll be doing", "What we need to see",
                                         "Ways to stand out from the crowd"]
    assert secs[2].bullets == ["BS in CS", "C/C++"]
    labels = jdtext.classify_sections(secs[1:], log=lambda _: None)  # all recognised by rules: no model call
    assert labels == {0: "responsibilities", 1: "basic_qualifications", 2: "preferred_qualifications"}


@pytest.mark.parametrize(
    "url,job_id",
    [("https://job-boards.greenhouse.io/stripe/jobs/8130881", "8130881"),
     ("https://stripe.com/jobs/search?gh_jid=8130881", "8130881"),
     ("https://www.kpmguscareers.com/jobdetail/?jobId=133129", "133129"),
     ("https://recruiting2.ultipro.com/X/JobBoard/e886/OpportunityDetail?opportunityId=e313c510-8511-4fac-b22c-d35fafa70083",
      "e313c510-8511-4fac-b22c-d35fafa70083"),
     ("https://nvidia.wd5.myworkdayjobs.com/en-US/X/job/US-CA/SoC-Software-Engineer_JR2026744", "JR2026744"),
     ("https://genmills.wd1.myworkdayjobs.com/en-US/G/job/Powai/Software-Engineer-II_10128543", "10128543"),
     ("https://www.amazon.jobs/en/jobs/10585694/software-development-engineer", "10585694"),
     ("https://jobs.lever.co/palantir/10dfc8bc-99ad-4ca2-ab76-853cb90a92c2", "10dfc8bc-99ad-4ca2-ab76-853cb90a92c2"),
     ("https://cafevalleyjobs.com/project-engineer-marion-in-179452/", "179452")],
)
def test_job_id_is_stable(url, job_id):
    from jobagent.tools.scrape import job_id_for

    assert job_id_for(url) == job_id
