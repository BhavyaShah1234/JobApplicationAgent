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
<script>function replaceUpload(){const old=document.getElementById('upload'),replacement=document.createElement('input');replacement.id='upload';replacement.type='file';replacement.onchange=replaceUpload;old.replaceWith(replacement)}upload.onchange=replaceUpload;</script>'''


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


def test_revisited_widget_has_one_current_attachment_and_server_changes_invalidate_approval(world,tmp_path,monkeypatch):
    path,server=setup(world,tmp_path,monkeypatch)
    monkeypatch.setattr(A,'plan',lambda fields,*a,**k:({f.id:A.Answer(id=f.id,value='resume',source='applicant') for f in fields},[]))
    monkeypatch.setattr(A,'check',lambda *a:None);monkeypatch.setattr(A,'enforce',lambda *a:None)
    monkeypatch.setattr(A,'consult_learned',lambda *a:None)
    records=[]
    for number in [1,2]:
        view,_=A.fill_page(world.page,{'_applicant':{},'_jd':{}},{'resume':str(path)},object(),lambda _:None,records,number,{})
    assert len(records)==2 and all(r['verified'] for r in records)
    records[0]['required']=True
    inventory=forms.upload_inventory_records(world.page,records)
    assert len(inventory)==1 and inventory[0]['page']==2 and inventory[0]['required']
    before,payload=freeze_payload(world.page,view,records)
    assert len(payload['attachments'])==1
    assert payload['identity']['resume_sha256']==hashlib.sha256(path.read_bytes()).hexdigest()
    assert len(payload['reviewed'])==2
    server['bytes']=b'%PDF-1.4 replaced with different contents under the same URL and filename'
    after,changed=freeze_payload(world.page,view,records)
    assert after!=before and changed['identity']['resume_sha256'] is None


def test_distinct_widgets_with_matching_record_metadata_are_not_merged(world,tmp_path,monkeypatch):
    path,server=setup(world,tmp_path,monkeypatch)
    first=world.page.locator('.document-upload').element_handle()
    world.page.locator('.document-upload').evaluate('e=>e.after(e.cloneNode(true))')
    second=world.page.locator('.document-upload').nth(1).element_handle()
    sha=hashlib.sha256(path.read_bytes()).hexdigest()
    world.page._jobagent_upload_proofs=[SimpleNamespace(identity='same',frame='frame',expected=sha,widget=w) for w in [first,second]]
    records=[{'kind':'file','verified':True,'identity':'same','field_key':['frame'],'held_file_sha256':sha,'label':'Resume','page':n} for n in [1,2]]
    assert forms.upload_inventory_records(world.page,records)==records


def test_positive_upload_notice_does_not_claim_attachment_or_hide_a_real_error(world):
    world.setup('''<h1>Application</h1><div role=alert>Your resume was uploaded successfully.</div>
    <div role=alert>Your resume was uploaded successfully, but another field is invalid.</div>
    <div role=alert aria-invalid=true>Your file was uploaded successfully.</div>''')
    world.page.goto('https://fixture.test/apply');view=forms.read_page(world.page)
    assert 'Your resume was uploaded successfully.' not in view.errors
    assert any('another field is invalid' in e for e in view.errors)
    assert 'Your file was uploaded successfully.' in view.errors
