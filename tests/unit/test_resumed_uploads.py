"""A resumed document widget must replace its own old file without accepting its old acknowledgement."""
import hashlib
import shutil

import pytest
from playwright.sync_api import sync_playwright

from jobagent.tools import forms


@pytest.fixture
def upload_page():
    if not shutil.which('google-chrome'):
        pytest.skip('Chrome required')
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=True)
        page = browser.new_page()
        yield page
        browser.close()


def widget(page, count=2, button_type='button', delete_label='Delete resume.pdf'):
    page.set_content('''<form onsubmit="window.sent=true;return false">
      <div id=resume data-automation-id=formField-resume><label>Resume</label>
      <input id=upload type=file><div id=cards></div></div>
      <div id=other data-automation-id=formField-cover><label>Cover letter</label><input type=file>
      <div data-automation-id=file-upload-item><div data-automation-id=file-upload-item-name>resume.pdf</div>
      <button type=button data-automation-id=delete-file aria-label="Delete resume.pdf"
      onclick="window.otherDeletes++">Delete</button></div></div></form>
      <script>
      window.deleted=[];window.uploads=0;window.otherDeletes=0;
      function card(name,type='button',caption='Delete '+name){
        const c=document.createElement('div');c.dataset.automationId='file-upload-item';
        const n=document.createElement('div');n.dataset.automationId='file-upload-item-name';n.textContent=name;c.append(n);
        const b=document.createElement('button');if(type)b.type=type;b.dataset.automationId='delete-file';
        b.setAttribute('aria-label',caption);b.textContent='Delete';
        b.onclick=()=>{window.deleted.push(name);setTimeout(()=>c.remove(),150)};c.append(b);cards.append(c);
      }
      upload.onchange=()=>{window.uploads++;card(upload.files[0].name);upload.value=''};
      </script>''')
    page.evaluate('args=>{for(let i=0;i<args.count;i++)card("resume.pdf",args.type,args.label);card("cover_letter.pdf")}',
                  {'count': count, 'type': button_type, 'label': delete_label})


def test_resumed_upload_replaces_same_named_files_only_in_its_own_widget(upload_page, tmp_path):
    page = upload_page
    widget(page)
    pdf = tmp_path / 'resume.pdf'
    pdf.write_bytes(b'%PDF-1.4 the current source bytes')
    view = forms.read_page(page)
    field = next(f for f in view.fields if f.kind == 'file' and f.label.startswith('Resume /'))
    forms.fill_field(page, view, field, str(pdf), {})
    assert page.evaluate('window.uploads') == 1
    assert page.evaluate('window.deleted') == ['resume.pdf', 'resume.pdf']
    assert page.evaluate('window.otherDeletes') == 0
    assert page.locator('#cards [data-automation-id=file-upload-item-name]').all_inner_texts() == ['cover_letter.pdf', 'resume.pdf']
    proof = page._jobagent_upload_proofs[-1]
    assert proof.verify() and proof.expected == hashlib.sha256(pdf.read_bytes()).hexdigest()
    assert proof.replaced_count == 2 and proof.method == 'dispatched bytes and widget acknowledgement'
    assert page.evaluate('!window.sent')
    proof.close()


@pytest.mark.parametrize('button_type,delete_label', [
    ('submit', 'Delete resume.pdf'), ('', 'Delete resume.pdf'), ('button', 'Delete different.pdf'),
])
def test_uncertain_or_submitting_delete_control_blocks_before_dispatch(upload_page, tmp_path, button_type, delete_label):
    page = upload_page
    widget(page, count=1, button_type=button_type, delete_label=delete_label)
    pdf = tmp_path / 'resume.pdf'
    pdf.write_bytes(b'%PDF-1.4 current')
    view = forms.read_page(page)
    field = next(f for f in view.fields if f.kind == 'file' and f.label.startswith('Resume /'))
    with pytest.raises(ValueError, match='unambiguous non-submitting delete'):
        forms.fill_field(page, view, field, str(pdf), {})
    assert page.evaluate('window.uploads') == 0
    assert page.evaluate('window.deleted') == []
    assert page.evaluate('!window.sent')


def test_excessive_duplicates_are_not_partially_removed(upload_page, tmp_path):
    page = upload_page
    widget(page, count=9)
    pdf = tmp_path / 'resume.pdf'
    pdf.write_bytes(b'%PDF-1.4 current')
    view = forms.read_page(page)
    field = next(f for f in view.fields if f.kind == 'file' and f.label.startswith('Resume /'))
    with pytest.raises(ValueError, match='Too many same-named'):
        forms.fill_field(page, view, field, str(pdf), {})
    assert page.evaluate('window.uploads') == 0
    assert page.evaluate('window.deleted') == []


def test_every_same_named_card_is_validated_before_removing_the_first(upload_page, tmp_path):
    page = upload_page
    widget(page, count=2)
    page.locator('#cards [data-automation-id=delete-file]').nth(1).evaluate("e=>e.type='submit'")
    pdf = tmp_path / 'resume.pdf'
    pdf.write_bytes(b'%PDF-1.4 current')
    view = forms.read_page(page)
    field = next(f for f in view.fields if f.kind == 'file' and f.label.startswith('Resume /'))
    with pytest.raises(ValueError, match='unambiguous non-submitting delete'):
        forms.fill_field(page, view, field, str(pdf), {})
    assert page.evaluate('window.uploads') == 0
    assert page.evaluate('window.deleted') == []


def test_one_differently_named_primary_resume_is_replaced(upload_page,tmp_path):
    page=upload_page;widget(page,count=0)
    page.locator('#cards').evaluate('e=>e.innerHTML=""')
    page.evaluate("card('previous_resume.pdf')")
    pdf=tmp_path/'resume.pdf';pdf.write_bytes(b'%PDF-1.4 new intended resume')
    view=forms.read_page(page);field=next(f for f in view.fields if f.identity=='id:upload')
    forms.fill_field(page,view,field,str(pdf),{})
    assert page.evaluate('window.deleted')==['previous_resume.pdf']
    assert page.evaluate('window.uploads')==1 and page.evaluate('window.otherDeletes')==0
    assert page._jobagent_upload_proofs[-1].verify()


def test_unknown_multiple_prior_documents_are_not_emptied_or_appended_to(upload_page,tmp_path):
    page=upload_page;widget(page,count=0)
    page.evaluate("card('previous_resume.pdf')")
    pdf=tmp_path/'resume.pdf';pdf.write_bytes(b'%PDF-1.4 new intended resume')
    view=forms.read_page(page);field=next(f for f in view.fields if f.identity=='id:upload')
    with pytest.raises(ValueError,match='unambiguous primary document'):
        forms.fill_field(page,view,field,str(pdf),{})
    assert page.evaluate('window.deleted')==[] and page.evaluate('window.uploads')==0


def test_one_known_cover_letter_in_the_resume_group_is_preserved(upload_page,tmp_path):
    page=upload_page;widget(page,count=0)
    pdf=tmp_path/'resume.pdf';pdf.write_bytes(b'%PDF-1.4 new intended resume')
    view=forms.read_page(page);field=next(f for f in view.fields if f.identity=='id:upload')
    with pytest.raises(ValueError,match='unambiguous primary document'):
        forms.fill_field(page,view,field,str(pdf),{})
    assert page.evaluate('window.deleted')==[] and page.evaluate('window.uploads')==0
