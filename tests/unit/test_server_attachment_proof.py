"""Opaque replacement uploads need the actual current server bytes, not an old filename."""
import hashlib
from types import SimpleNamespace
import pytest
from playwright.sync_api import APIRequestContext
from jobagent.tools import forms
from jobagent.filling.uploads import UploadProof
from jobagent.steps import apply as A
from jobagent.steps.submit import freeze_payload
from test_filling_strategy import world

HTML='''<h1>Application</h1><form><div class=document-upload><label for=upload>Resume</label>
<a id=download href="/download/current">Download resume.pdf</a><input id=upload type=file></div></form>
<script>upload.onchange=()=>{const replacement=document.createElement('input');replacement.id='upload';replacement.type='file';upload.replaceWith(replacement)};</script>'''


def setup(world,tmp_path,monkeypatch):
    world.setup(HTML);world.page.goto('https://fixture.test/apply')
    path=tmp_path/'resume.pdf';path.write_bytes(b'%PDF-1.4 owner confirmed bytes')
    server={'bytes':path.read_bytes(),'requests':[]}
    def get(self,url,**kwargs):
        server['requests'].append(url)
        return SimpleNamespace(ok=True,headers={'content-length':str(len(server['bytes']))},body=lambda:server['bytes'],dispose=lambda:None)
    monkeypatch.setattr(APIRequestContext,'get',get)
    return path,server


def test_replacement_uses_current_server_bytes_and_rechecks_after_they_change(world,tmp_path,monkeypatch):
    path,server=setup(world,tmp_path,monkeypatch)
    view=forms.read_page(world.page);f=view.fields[0]
    assert forms.fill_field(world.page,view,f,'resume',{'resume':str(path)})==str(path)
    proof=world.page._jobagent_upload_proofs[-1]
    assert proof.verify() and proof.method=='dispatched bytes and current server attachment bytes'
    assert server['requests'] and all(u=='https://fixture.test/download/current' for u in server['requests'])
    server['bytes']=b'%PDF-1.4 a different resume'
    assert not proof.verify()
    server['bytes']=path.read_bytes()
    assert proof.verify()


@pytest.mark.parametrize('change',['foreign','fragment','ambiguous','other_widget'])
def test_unowned_or_ambiguous_downloads_never_supply_upload_proof(world,tmp_path,monkeypatch,change):
    path,server=setup(world,tmp_path,monkeypatch)
    native=world.page.locator('#upload');proof=UploadProof(path,native);proof.prepare();proof.bind(native)
    native.set_input_files(str(path))
    if change=='foreign':world.page.locator('#download').evaluate("e=>e.href='https://another.test/download/current'")
    if change=='fragment':world.page.locator('#download').evaluate("e=>e.href='#'")
    if change=='ambiguous':world.page.locator('#download').evaluate("e=>{let a=e.cloneNode(true);a.href='/download/another';e.after(a)}")
    if change=='other_widget':world.page.locator('#download').evaluate("e=>document.body.append(e)")
    assert not proof.verify() and server['requests']==[]


def test_original_filler_binds_opaque_server_proof_into_frozen_payload(world,tmp_path,monkeypatch):
    path,server=setup(world,tmp_path,monkeypatch)
    monkeypatch.setattr(A,'plan',lambda fields,*a,**k:({f.id:A.Answer(id=f.id,value='resume',source='applicant') for f in fields},[]))
    monkeypatch.setattr(A,'check',lambda *a:None);monkeypatch.setattr(A,'enforce',lambda *a:None)
    monkeypatch.setattr(A,'consult_learned',lambda *a:None)
    records=[];view,_=A.fill_page(world.page,{'_applicant':{},'_jd':{}},{'resume':str(path)},object(),lambda _:None,records,1,{})
    assert len(records)==1 and records[0]['verified']
    assert records[0]['field_key'][0]==world.page._jobagent_upload_proofs[-1].frame
    _,payload=freeze_payload(world.page,view,records)
    assert payload['identity']['resume_sha256']==hashlib.sha256(path.read_bytes()).hexdigest()
