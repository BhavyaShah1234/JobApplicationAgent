"""A fresh file inventory cannot move an upload onto a reordered same-caption field."""
import pytest
from playwright.sync_api import sync_playwright
from jobagent.tools import forms
from jobagent.filling.planned import PlannedFiller
from jobagent.filling.adaptive import AdaptiveFiller
from test_filling_strategy import world


@pytest.fixture
def page():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True)
        yield browser.new_page()
        browser.close()


def test_reordering_identical_upload_captions_preserves_the_authored_input(page,tmp_path):
    page.set_content('''<form><div id=one data-automation-id=formField-one><label for=first>Resume</label><input id=first name=resume type=file data-jf-upload-label=Resume></div>
        <div id=two data-automation-id=formField-two><label for=second>Resume</label><input id=second name=resume type=file data-jf-upload-label=Resume></div></form>''')
    view=forms.read_page(page);fields=[f for f in view.fields if f.kind=='file']
    assert len(fields)==2 and fields[0].label==fields[1].label
    target=next(f for f in fields if f.identity=='id:first')
    page.locator('#two').evaluate('e=>e.parentElement.prepend(e)')
    path=tmp_path/'resume.pdf';path.write_bytes(b'%PDF-1.4 intended input bytes')
    forms.fill_field(page,view,target,str(path),{})
    assert page.locator('#first').evaluate('e=>e.files.length')==1
    assert page.locator('#second').evaluate('e=>e.files.length')==0


def test_changed_primary_caption_does_not_redirect_to_another_control(page,tmp_path):
    page.set_content('''<form><label for=first>Resume</label><input id=first name=upload type=file>
        <label for=second>Cover Letter</label><input id=second name=upload type=file></form>''')
    view=forms.read_page(page);target=next(f for f in view.fields if f.identity=='id:first')
    page.locator('[for=first]').evaluate("e=>e.textContent='Cover Letter'")
    page.locator('[for=second]').evaluate("e=>e.textContent='Resume'")
    path=tmp_path/'resume.pdf';path.write_bytes(b'%PDF-1.4 intended input bytes')
    with pytest.raises(forms.FillError):forms.fill_field(page,view,target,str(path),{})
    assert page.locator('#first').evaluate('e=>e.files.length')==0
    assert page.locator('#second').evaluate('e=>e.files.length')==0


@pytest.mark.parametrize('changed', [{'frame_identity':'replacement-document'},{'section':'Cover Letter'}])
def test_upload_rebinding_cannot_cross_a_document_or_section_boundary(changed):
    from dataclasses import replace
    previous=forms.Field(id=0,kind='file',label='Upload a file',identity='id:attachment',
                         frame_identity='original-document',section='Resume/CV')
    assert forms._current_upload_controls(previous,[replace(previous,**changed)])==[]


@pytest.mark.parametrize('engine',[PlannedFiller,AdaptiveFiller])
def test_generic_cover_upload_uses_cover_letter_bytes_in_native_filler(world,tmp_path,engine):
    world.setup('''<h1>Application</h1><section><h2>Cover Letter</h2>
        <div data-automation-id=formField-cover><label for=attachment>Upload a file</label>
        <input id=attachment type=file required></div></section>
        <button type=submit onclick="window.sent=true">Submit Application</button>''')
    world.page.goto('https://fixture.test/apply')
    field=next(f for f in forms.read_page(world.page).fields if f.kind=='file')
    assert field.section=='Cover Letter'
    resume=tmp_path/'resume.pdf';resume.write_bytes(b'%PDF-1.4 resume bytes')
    cover=tmp_path/'cover_letter.pdf';cover.write_bytes(b'%PDF-1.4 cover letter bytes')
    result=engine([],lambda *_:pytest.fail('Documents already supplied')).fill(world.page,
        files={'resume':str(resume),'cover_letter':str(cover)},advance_pages=False,max_rounds=3)
    assert result['state']=='page_complete',result['blockers']
    assert world.page.locator('#attachment').evaluate('e=>e.files[0].name')=='cover_letter.pdf'
    assert not world.page.evaluate('!!window.sent')
