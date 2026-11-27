"""Existing completed attachment slots must prove the requested bytes, not a filename."""
import json
from types import SimpleNamespace
import pytest
from playwright.sync_api import APIRequestContext
from jobagent.tools import forms
from test_filling_strategy import world

HTML=r'''<h1>Application</h1><form id=application onsubmit="window.sent++;return false">
<div class="RCMFormField attachmentField" id=resumeOwner><label id=resumeLabel>Resume/CV</label><div class=attachmentComponentInput></div></div>
<div class="RCMFormField attachmentField" id=coverOwner><label id=coverLabel>Cover Letter</label><div class=attachmentComponentInput></div></div>
<button type=submit>Apply</button></form>
<style>[role=button]{display:inline-block;min-width:24px;min-height:24px}.hiddenAriaContent{display:none}[role=dialog]{display:block;position:fixed;background:white;inset:20px;z-index:20}</style>
<script>
window.sent=0;window.dialogMode='normal';window.names={resume:'resume.pdf',cover:'cover_letter.pdf'};
function render(role,complete){
 const n=role==='resume'?'62':'64',root=document.querySelector('#'+role+'Owner .attachmentComponentInput');
 root.innerHTML='<div id="'+n+':_attach" class="'+(complete?'successBtn':'attachmentBtn')+'">'+
 (complete?'<div role=button id="'+n+':_attachDownloadLabel" onclick="location.href=\'/attachmentdownload?role='+role+'\'"><span id="'+n+':_attachDownloadLabelLink">'+names[role]+'</span></div>':'')+
 '<div id="'+n+':_attachSuccess" role=alert style="display:none">File is uploaded successfully</div>'+
 '<div class=attachActions><span role=button tabindex=0 id="'+n+':_attachIcon" class="'+(complete?'removeAttachments':'addAttachments')+'" aria-labelledby="'+role+'Label '+role+'Action" onclick="'+(complete?'removeDoc':'uploadDoc')+'(\''+role+'\')">'+(complete?'Delete':'+')+'</span></div>'+
 '<span class=hiddenAriaContent id='+role+'Action>'+(complete?'Delete document '+names[role]+' Opens a dialog':'Upload '+(role==='resume'?'a Resume':'a Cover Letter')+' Opens a dialog')+'</span></div>';
}
function removeDoc(role){
 const d=document.createElement('div');d.setAttribute('role','dialog');d.setAttribute('aria-modal','true');
 d.innerHTML='<p>'+(dialogMode==='wrong'?'Delete all account information?':'Are you sure you want to delete this file?')+'</p><button type=button>Cancel</button><button type=button>OK</button>';
 d.querySelector('button').onclick=()=>d.remove();d.querySelectorAll('button')[1].onclick=async()=>{await fetch('/delete',{method:'POST',body:JSON.stringify({role})});render(role,false);d.remove()};
 if(dialogMode==='input')d.insertAdjacentHTML('beforeend','<input aria-label="Unrelated applicant answer">');
 document.body.append(d);
 if(dialogMode==='duplicate')document.body.append(d.cloneNode(true));
}
function uploadDoc(role){
 const d=document.createElement('div');d.setAttribute('role','dialog');d.setAttribute('aria-modal','true');d.setAttribute('aria-label','Select a source for your file upload');
 d.innerHTML='<input type=file name=fileData form=application aria-label="Upload from Device">';
 d.querySelector('input').onchange=async e=>{const f=e.target.files[0];const bytes=Array.from(new Uint8Array(await f.arrayBuffer()));await fetch('/upload',{method:'POST',body:JSON.stringify({role,name:f.name,bytes})});names[role]=f.name;render(role,true);document.getElementById((role==='resume'?'62':'64')+':_attachSuccess').style.display='block';d.remove()};
 document.body.append(d);
}
render('resume',true);render('cover',true);
</script>'''


def setup(world,tmp_path,monkeypatch,*,same=False,old_name='resume.pdf'):
    world.setup(HTML);page=world.page;page.goto('https://fixture.test/apply')
    path=tmp_path/'resume.pdf';path.write_bytes(b'%PDF-1.4 intended current resume bytes')
    server={'resume':path.read_bytes() if same else b'%PDF-1.4 stale resume bytes',
            'cover':b'%PDF-1.4 untouched cover letter','deletes':[],'uploads':[],'status':200}
    page.evaluate('(name)=>{names.resume=name;render("resume",true)}',old_name)
    def dispatch(route):
        req=route.request
        if req.url.endswith('/delete'):
            role=json.loads(req.post_data)['role'];server['deletes'].append(role);server[role]=b''
            route.fulfill(status=200,body='ok')
        elif req.url.endswith('/upload'):
            data=json.loads(req.post_data);server[data['role']]=bytes(data['bytes']);server['uploads'].append(data['role']);route.fulfill(status=200,body='ok')
        else:
            role=req.url.rsplit('=',1)[-1];name=path.name if role=='resume' and server['uploads'] else old_name if role=='resume' else 'cover_letter.pdf'
            route.fulfill(status=200,content_type='application/pdf',body=server[role],headers={'content-disposition':f'attachment; filename="{name}"'})
    for pattern in ['**/delete','**/upload','**/attachmentdownload?*']:page.route(pattern,dispatch)
    def get(self,url,**kwargs):
        role=url.rsplit('=',1)[-1];content=server[role]
        assert kwargs.get('max_redirects')==0
        return SimpleNamespace(ok=server['status']==200,status=server['status'],headers={'content-length':str(len(content))},body=lambda:content,dispose=lambda:None)
    monkeypatch.setattr(APIRequestContext,'get',get)
    return page,path,server


def resume_field(page):
    view=forms.read_page(page);fields=[f for f in view.fields if f.kind=='file' and f.label=='Resume/CV']
    assert len(fields)==1
    return view,fields[0]


def test_completed_resume_and_cover_slots_remain_in_the_upload_inventory(world,tmp_path,monkeypatch):
    page,_,_=setup(world,tmp_path,monkeypatch)
    assert [(f.label,f.value) for f in forms.read_page(page).fields if f.kind=='file']==[
        ('Resume/CV','resume.pdf'),('Cover Letter','cover_letter.pdf')]


@pytest.mark.parametrize('strategy',['experimental','adaptive'])
@pytest.mark.parametrize('reload_after',['delete','upload'])
def test_native_application_coverage_retains_owned_proof_when_attachment_ids_change(world,tmp_path,monkeypatch,strategy,reload_after):
    from jobagent.steps import apply
    from test_filling_strategy import SourceModel
    original_setup=world.setup
    def rerendering_setup(html):
        html=html.replace("const n=role==='resume'?'62':'64'", "const n=role==='resume'?(sessionStorage.getItem('parsed')?'300':'62'):'64'")
        html=html.replace("(role==='resume'?'62':'64')", "(role==='resume'?(sessionStorage.getItem('parsed')?'300':'62'):'64')")
        html=html.replace("render('resume',true);render('cover',true);",
                          "render('resume',!sessionStorage.getItem('deleted'));render('cover',true);")
        html=html.replace('render(role,false);d.remove()',
                          "render(role,false);if(role==='resume')sessionStorage.setItem('deleted','1');d.remove()")
        html=html.replace('names[role]=f.name;render(role,true)',
                          "names[role]=f.name;if(role==='resume')sessionStorage.removeItem('deleted');render(role,true)")
        marker="sessionStorage.setItem('deleted','1');d.remove()};" if reload_after=='delete' else "style.display='block';d.remove()};"
        html=html.replace(marker,marker[:-2]+";if(role==='resume'&&!sessionStorage.getItem('parsed')){sessionStorage.setItem('parsed','1');location.reload()}};")
        original_setup(html)
    monkeypatch.setattr(world,'setup',rerendering_setup)
    page,path,server=setup(world,tmp_path,monkeypatch)
    (world.job/'resume.pdf').write_bytes(path.read_bytes())
    # A successful parser POST replaces the document and renumbers its slot;
    # current server bytes and the owning caption remain independently readable.
    monkeypatch.setattr(apply,'_start',lambda *a,**k:forms.read_page(page))
    result=apply.run(page,world.job,'fixture',model=SourceModel(),log=lambda _:None,
                     filling_strategy=strategy)
    assert result['state']=='review',{
        'blockers':result.get('blockers'),'fields':result.get('fields'),
        'attempts':[e for p in result.get('experimental_pages',[]) for e in p.get('attempts',[]) if e.get('kind')=='file']}
    assert server['uploads']==server['deletes']==['resume']
    assert server['resume']==path.read_bytes()
    assert page.evaluate('window.sent')==0
    held=[f for f in result['fields'] if f['kind']=='file' and f['label']=='Resume/CV']
    assert len(held)==1 and held[0]['verified']


@pytest.mark.parametrize('change',['none','bytes','caption','route','duplicate','neighbor'])
def test_refreshed_attachment_requires_current_bytes_and_the_original_owner(world,tmp_path,monkeypatch,change):
    from jobagent.filling.uploads import UploadProof
    page,path,server=setup(world,tmp_path,monkeypatch,same=True)
    view,field=resume_field(page)
    proof=UploadProof(path,forms.locate(view,field))
    assert proof.verify()
    page.reload()
    assert not proof.verify()  # Old document handles never become fresh proof.
    if change=='bytes':server['resume']=b'%PDF-1.4 different server bytes'
    if change=='caption':page.locator('#resumeLabel').evaluate("e=>e.textContent='Different document'")
    if change=='route':page.evaluate("history.pushState({},'', '/different-application')")
    if change=='duplicate':page.locator('#resumeOwner').evaluate('e=>e.after(e.cloneNode(true))')
    element=page.locator('[id="64:_attach"]' if change=='neighbor' else '[id="62:_attach"]').first.element_handle()
    refreshed=proof.refreshed_attachment(element)
    try:
        if change=='none':
            assert refreshed is not None and refreshed.verify()
            assert refreshed.expected==proof.expected and refreshed.reused_existing
        else:assert refreshed is None
        assert not server['deletes'] and not server['uploads']
        assert not page.evaluate('window.sent')
    finally:
        if refreshed is not None:refreshed.close()
        proof.close()


@pytest.mark.parametrize('old_name',['resume.pdf','older_resume.pdf'])
def test_only_the_owned_stale_resume_is_replaced_and_current_bytes_are_verified(world,tmp_path,monkeypatch,old_name):
    page,path,server=setup(world,tmp_path,monkeypatch,old_name=old_name);view,field=resume_field(page)
    assert forms.fill_field(page,view,field,'resume',{'resume':str(path)})==str(path)
    proof=page._jobagent_upload_proofs[-1]
    assert proof.verify() and proof.replaced_count==1
    assert server['resume']==path.read_bytes() and server['deletes']==server['uploads']==['resume']
    assert server['cover']==b'%PDF-1.4 untouched cover letter'
    assert page.evaluate('window.sent')==0


def test_matching_current_bytes_are_reused_without_delete_or_upload(world,tmp_path,monkeypatch):
    page,path,server=setup(world,tmp_path,monkeypatch,same=True);view,field=resume_field(page)
    assert forms.fill_field(page,view,field,'resume',{'resume':str(path)})==str(path)
    proof=page._jobagent_upload_proofs[-1]
    assert proof.verify() and proof.replaced_count==0 and not proof.dispatched_bytes_verified
    assert not server['deletes'] and not server['uploads']
    server['resume']=b'%PDF-1.4 changed after review'
    assert not proof.verify()


@pytest.mark.parametrize('mode',['wrong','input','duplicate'])
def test_unexpected_delete_confirmation_does_not_delete_anything(world,tmp_path,monkeypatch,mode):
    page,path,server=setup(world,tmp_path,monkeypatch);page.evaluate('(mode)=>window.dialogMode=mode',mode)
    view,field=resume_field(page)
    with pytest.raises(ValueError,match='confirmation'):forms.fill_field(page,view,field,'resume',{'resume':str(path)})
    assert server['deletes']==server['uploads']==[]
    assert not page.evaluate('window.sent')


def test_refused_download_stops_before_deleting_a_same_named_file(world,tmp_path,monkeypatch):
    page,path,server=setup(world,tmp_path,monkeypatch);server['status']=403;view,field=resume_field(page)
    with pytest.raises(ValueError,match='not deleting'):forms.fill_field(page,view,field,'resume',{'resume':str(path)})
    assert not server['deletes'] and not server['uploads']


def test_a_neighboring_document_delete_label_is_not_accepted(world,tmp_path,monkeypatch):
    page,path,server=setup(world,tmp_path,monkeypatch)
    page.locator('#resumeAction').evaluate("e=>e.textContent='Delete document cover_letter.pdf Opens a dialog'")
    view,field=resume_field(page)
    with pytest.raises(ValueError,match='delete action'):forms.fill_field(page,view,field,'resume',{'resume':str(path)})
    assert not server['deletes'] and not server['uploads']


@pytest.mark.parametrize('engine_name',['planned','adaptive'])
@pytest.mark.parametrize('same',[False,True])
def test_native_filler_proves_the_completed_resume_without_reupload_loops(world,tmp_path,monkeypatch,engine_name,same):
    from jobagent.filling.planned import PlannedFiller
    from jobagent.filling.adaptive import AdaptiveFiller
    page,path,server=setup(world,tmp_path,monkeypatch,same=same)
    engine=PlannedFiller if engine_name=='planned' else AdaptiveFiller
    agent=engine([],lambda *_:pytest.fail('Only a provided document is needed'))
    result=agent.fill(page,{'resume':str(path)},advance_pages=False,max_rounds=5)
    assert result['state']=='page_complete',result['blockers']
    records=[r for r in result['records'] if r.get('held_file_sha256')]
    assert len(records)==1 and records[0]['outcome']=='verified'
    assert server['uploads']==([] if same else ['resume'])
    assert server['deletes']==server['uploads']
    assert not page.evaluate('window.sent')


def test_duplicate_owners_stop_before_any_replacement(world,tmp_path,monkeypatch):
    from jobagent.filling.uploads import UploadProof
    page,path,server=setup(world,tmp_path,monkeypatch)
    proof=UploadProof(path,page.locator('[id="62:_attach"]').element_handle())
    page.locator('#resumeOwner').evaluate('e=>e.after(e.cloneNode(true))')
    with pytest.raises(ValueError,match='unique'):proof.prepare()
    assert server['deletes']==server['uploads']==[]
    proof.close()


def test_an_unrelated_open_dialog_prevents_attachment_actions(world,tmp_path,monkeypatch):
    page,path,server=setup(world,tmp_path,monkeypatch)
    page.locator('body').evaluate('e=>e.insertAdjacentHTML("beforeend", "<div role=dialog><p>Unrelated action</p></div>")')
    view,field=resume_field(page)
    with pytest.raises(ValueError,match='existing dialog'):forms.fill_field(page,view,field,'resume',{'resume':str(path)})
    assert server['deletes']==server['uploads']==[]
