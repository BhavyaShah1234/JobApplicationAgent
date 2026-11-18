"""A wizard's read-only document section must keep the upload's semantic identity."""
from pathlib import Path
from html import escape
import pytest
from playwright.sync_api import sync_playwright
from jobagent.tools import forms
from jobagent.steps import submit

FIXTURE=Path(__file__).parents[1]/'fixtures/workday-review-upload.html'


@pytest.fixture
def page():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True)
        page=browser.new_page();page.route('**/*',lambda route:route.abort())
        page.set_content(FIXTURE.read_text());yield page;browser.close()


def record(**kw):
    return {'kind':'file','label':'Upload a file (5MB max) * / Drop files here or Select files',
            'section':'Resume/CV','answer':'resume','required':True,'frame':0,'verified':True,
            'filled':'/app/outputs/fixture/1/resume.pdf',**kw}


def blockers(page,r=None):
    return forms.upload_blockers(page,forms.read_page(page),[r or record()],['resume.pdf','cover_letter.pdf'])


@pytest.mark.parametrize('caption',['Resume/CV','Resume / CV','CV','Curriculum Vitae'])
def test_current_readonly_section_binds_to_its_recorded_document_purpose(page,caption):
    page.locator('h4').evaluate('(e,value)=>e.textContent=value',caption)
    assert blockers(page)==[]


def test_readonly_attachment_name_is_observed_but_its_bytes_are_not_fabricated(page):
    before=submit._attachments(page,forms.read_page(page),[record()]);assert len(before)==1
    assert before[0]['observed']=='resume.pdf' and before[0]['files'] is None
    assert 'only the name is shown' in before[0]['unverified']
    page.locator('[data-automation-id=file-upload-item-name]').evaluate('e=>e.textContent="changed.pdf"')
    after=submit._attachments(page,forms.read_page(page),[record()]);assert after[0]['observed']=='changed.pdf'
    assert before!=after


def test_a_removed_file_cannot_be_approved_using_an_old_success_message_or_known_disk_name(page):
    page.locator('[data-automation-id=file-upload-item-name]').evaluate('e=>e.remove()')
    assert blockers(page)


def test_a_cover_letter_or_filename_elsewhere_is_not_the_resumes_attachment(page):
    other=FIXTURE.read_text().replace('Resume/CV-section','Cover-letter-section').replace('>Resume/CV<','>Cover letter<')
    page.locator('[data-automation-id=file-upload-item-name]').evaluate('e=>e.remove()')
    page.locator('body').evaluate('(e,html)=>e.insertAdjacentHTML("beforeend",html)',other+'<p>resume.pdf</p>')
    assert blockers(page)


def test_duplicate_document_sections_are_ambiguous(page):
    page.locator('body').evaluate('(e,html)=>e.insertAdjacentHTML("beforeend",html)',FIXTURE.read_text())
    assert blockers(page)


@pytest.mark.parametrize('change',[
    'e=>e.style.display="none"',
    'e=>e.setAttribute("aria-hidden","true")',
    'e=>e.setAttribute("aria-busy","true")',
    'e=>e.insertAdjacentHTML("beforeend","<input type=file aria-label=Upload>")',
    'e=>e.insertAdjacentHTML("beforeend","<h4>Cover letter</h4><span data-file-name=cover.pdf>cover.pdf</span>")',
])
def test_a_hidden_busy_live_or_mixed_upload_section_is_not_readonly_evidence(page,change):
    page.locator('[role=group]').first.evaluate(change)
    assert blockers(page)


@pytest.mark.parametrize('changes',[
    {'section':'My Experience'}, {'answer':'cover_letter'}, {'answer':['resume']}, {'frame':8},
])
def test_only_a_matching_typed_section_and_its_own_frame_can_supply_the_evidence(page,changes):
    assert blockers(page,record(**changes))


def test_a_named_upload_in_another_frame_does_not_supply_a_parent_frames_resume(page):
    page.set_content('<iframe srcdoc="'+escape(FIXTURE.read_text(),quote=True)+'"></iframe>')
    page.frames[1].locator('[data-automation-id=file-upload-item-name]').wait_for()
    assert blockers(page)
