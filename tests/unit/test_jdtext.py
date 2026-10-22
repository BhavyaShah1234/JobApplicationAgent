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
