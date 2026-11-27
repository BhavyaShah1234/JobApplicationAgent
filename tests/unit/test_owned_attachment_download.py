"""A refreshed profile's attachment is proved by its current server bytes."""
from types import SimpleNamespace

import pytest
from playwright.sync_api import APIRequestContext

from jobagent.tools import forms
from test_filling_strategy import world
from test_sap_portaled_upload import HTML


def setup(world, tmp_path, monkeypatch):
    world.setup(HTML)
    page = world.page
    page.goto('https://fixture.test/apply')
    page.evaluate('''()=>{const widget=document.getElementById('62:_attach');
      const owner=document.createElement('div');owner.className='RCMFormField attachmentField';
      owner.innerHTML='<label>Resume/CV</label><div class="attachmentComponentInput" id="111:_fieldComponentInput"></div>';
      widget.before(owner);owner.lastElementChild.append(widget)}''')
    path = tmp_path/'resume.pdf'
    path.write_bytes(b'%PDF-1.4 the expected application resume')
    server = {'bytes': path.read_bytes(), 'status': 200, 'requests': []}
    page.route('**/attachmentdownload?*', lambda route: route.fulfill(
        status=200, content_type='application/pdf', body=server['bytes'],
        headers={'content-disposition': 'attachment; filename="resume.pdf"'}))

    def get(self, url, **kwargs):
        server['requests'].append((url, kwargs))
        return SimpleNamespace(ok=server['status']==200, status=server['status'],
                               headers={'content-length': str(len(server['bytes']))},
                               body=lambda: server['bytes'], dispose=lambda: None)

    monkeypatch.setattr(APIRequestContext, 'get', get)
    view = forms.read_page(page)
    field = next(f for f in view.fields if f.kind=='file' and f.identity=='id:62:_attachIcon')
    assert forms.fill_field(page, view, field, str(path), {}) == str(path)
    proof = page._jobagent_upload_proofs[-1]
    assert proof.verify()
    # The real SuccessFactors parser replaces the entire profile field,
    # including the old widget and attachmentComponentInput owner.
    page.locator('.RCMFormField.attachmentField').evaluate('''owner=>{
      const next=owner.cloneNode(false);next.innerHTML=`<label>Resume/CV</label>
      <div class="attachmentComponentInput" id="211:_fieldComponentInput"><div class="successBtn" id="162:_attach">
      <div role="button" id="162:_attachDownloadLabel" onclick="location.href='/attachmentdownload?file=current'">
        <span id="162:_attachDownloadLabelLink">resume.pdf</span></div>
      <span role="button" class="removeAttachments" id="162:_attachIcon">Delete</span>
      </div></div>`;owner.replaceWith(next)}''')
    return page, proof, server


def test_download_proves_the_current_file_and_rechecks_changes(world, tmp_path, monkeypatch):
    page, proof, server = setup(world, tmp_path, monkeypatch)
    assert proof.verify()
    assert proof.method=='current owned server attachment download bytes'
    assert server['requests']==[('https://fixture.test/attachmentdownload?file=current',
                                 {'timeout': 5000, 'max_redirects': 0})]
    server['bytes']=b'%PDF-1.4 a different file with the same filename'
    assert not proof.verify()
    requests=len(server['requests'])
    assert not proof.verify() and len(server['requests'])==requests, 'do not repeatedly click a mismatched file'
    assert not page.evaluate('window.sent')


@pytest.mark.parametrize('change', ['other_owner', 'ambiguous', 'wrong_name', 'new_document', 'error'])
def test_a_name_or_an_unowned_or_invalid_widget_never_proves_the_file(world, tmp_path, monkeypatch, change):
    page, proof, server = setup(world, tmp_path, monkeypatch)
    if change=='other_owner':page.locator('.RCMFormField > label').evaluate("e=>e.textContent='Cover Letter'")
    if change=='ambiguous':page.locator('.RCMFormField').evaluate('e=>e.after(e.cloneNode(true))')
    if change=='wrong_name':page.locator('[id$="_attachDownloadLabelLink"]').evaluate("e=>e.textContent='other.pdf'")
    if change=='new_document':page.reload()
    if change=='error':page.locator('.successBtn').evaluate("e=>e.setAttribute('aria-invalid','true')")
    assert not proof.verify() and server['requests']==[]


@pytest.mark.parametrize('change', ['foreign', 'redirect', 'restricted'])
def test_foreign_downloads_and_refused_or_redirected_responses_stop(world, tmp_path, monkeypatch, change):
    page, proof, server = setup(world, tmp_path, monkeypatch)
    if change=='foreign':
        page.locator('[id$="_attachDownloadLabel"]').evaluate(
            "e=>e.onclick=()=>location.href='https://another.test/attachmentdownload?file=current'")
    else:server['status']=302 if change=='redirect' else 403
    assert not proof.verify()
    requests=len(server['requests'])
    assert not proof.verify() and len(server['requests'])==requests
    assert not page.evaluate('window.sent')


@pytest.mark.parametrize('block', ['challenge', 'scope'])
def test_download_verification_obeys_browser_scope_and_human_verification(world, tmp_path, monkeypatch, block):
    page, proof, server = setup(world, tmp_path, monkeypatch)
    if block=='challenge':
        from jobagent.tools import challenge
        monkeypatch.setattr(challenge,'detect',lambda _:SimpleNamespace(kind='captcha'))
    else:
        def outside_scope(_):raise RuntimeError('The application left its approved scope')
        page.context.jobagent_policy=outside_scope
    assert not proof.verify() and server['requests']==[]
