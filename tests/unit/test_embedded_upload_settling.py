"""A branded shell cannot establish that its embedded resume parser has settled."""
import time
from types import SimpleNamespace

import pytest
from playwright.sync_api import sync_playwright

from jobagent.filling.planned import PlannedFiller, settle_after_upload, read_view
from jobagent.filling.facts import Fact


@pytest.fixture
def page():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=True)
        try:
            yield browser.new_page()
        finally:
            browser.close()


def test_settle_waits_for_embedded_parser_values_not_unchanging_outer_shell(page):
    page.set_content('<h1>Company careers</h1><iframe srcdoc="<input id=name value=initial>"></iframe>')
    frame = page.frames[1]
    frame.evaluate("setTimeout(()=>document.getElementById('name').value='parsed',2500)")
    start = time.monotonic()
    settle_after_upload(page, min_s=0, max_s=8)
    assert frame.locator('#name').input_value() == 'parsed'
    assert time.monotonic() - start >= 4.8  # two stable observations AFTER the change


def test_visible_parser_progress_prevents_premature_settled_result(page):
    page.set_content('<input value=unchanged><div id=progress role=progressbar>Parsing resume</div>')
    page.evaluate("setTimeout(()=>document.getElementById('progress').remove(),3500)")
    settle_after_upload(page, min_s=0, max_s=9)
    assert page.locator('#progress').count() == 0


def test_lost_upload_proof_blocks_redispatch_without_claiming_file_verified(page, tmp_path):
    page.set_content('<form onsubmit="return false"><div class=file-upload><label for=resume>Resume</label>'
                     '<input type=file id=resume required onchange="window.uploads++"></div>'
                     '<label>Password<input type=password value=fixture-only required></label>'
                     '<label>Confirm password<input type=password value=fixture-only required></label>'
                     '<button>Submit application</button></form><script>window.uploads=0</script>')
    pdf = tmp_path / 'resume.pdf'
    pdf.write_bytes(b'%PDF-1.4 current expected bytes')
    field = next(f for f in read_view(page).fields if f.kind == 'file')
    filler = PlannedFiller([], lambda _: {})
    filler.uploaded_labels[(0, field.label)] = SimpleNamespace(verify=lambda: False)
    result = filler.fill(page, files={'resume': str(pdf)}, max_rounds=2)
    assert page.evaluate('window.uploads') == 0
    assert result['state'] == 'blocked'
    assert not any('password' in str(b).lower() for b in result['blockers'])
    assert not any(r.get('kind') == 'file' and r.get('outcome') == 'verified' for r in result['records'])
    assert any('not uploading it again automatically' in r.get('error', '') for r in result['records'])


def test_resume_parser_records_are_discovered_before_adding_empty_entity_rows(page,tmp_path):
    page.set_content('''<h1>Application</h1><form onsubmit="window.sent=true;return false">
      <label>Resume<input type=file id=resume></label><h2>Work Experience</h2><div id=rows></div>
      <button type=button id=add>Add</button><button>Submit application</button></form>
      <script>window.sent=false;window.adds=0;window.uploads=0;
      add.onclick=()=>{window.adds++;rows.innerHTML='<h3>Work Experience 1</h3><label>Employer<input></label>'};
      resume.onchange=()=>{window.uploads++;rows.innerHTML='<h3>Work Experience 1</h3><label>Employer<input></label>'};</script>''')
    pdf=tmp_path/'resume.pdf';pdf.write_bytes(b'%PDF-1.4 supplied resume')
    filler=PlannedFiller([Fact('employer',['Employer'],'Example Employer','profile','Work Experience 1')],
                         lambda _:pytest.fail('The employer is in the saved profile'))
    result=filler.fill(page,files={'resume':str(pdf)},max_rounds=4,advance_pages=False)
    assert result['state']=='page_complete'
    assert page.evaluate('window.adds')==0
    assert page.evaluate('window.uploads')==1
    assert page.locator('#rows input').input_value()=='Example Employer'
    assert not page.evaluate('window.sent')
