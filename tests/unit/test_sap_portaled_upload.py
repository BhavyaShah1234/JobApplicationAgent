"""A clicked file-source callout remains bound to its original attachment widget."""
import pytest
from playwright.sync_api import sync_playwright
from jobagent.tools import forms

HTML = """<form id=application onsubmit="window.sent=true;return false">
<h2 id=resumeTitle>Resume</h2><div class=attachmentBtn id="62:_attach">
<div id="62:_attachLoading" role=alert style="display:none">Uploading...</div>
<div id="62:_attachSuccess" role=alert style="display:none">File is uploaded successfully</div>
<div id="62:_filename"></div><div class=attachActions>
<span id="62:_attachIcon" role=button tabindex=0 aria-labelledby="resumeTitle actionLabel" onclick="openUpload()">+</span>
<span id=actionLabel>Upload a Resume Opens a dialog</span></div></div>
<h2>Cover Letter</h2><div class=attachmentBtn id="64:_attach"><div id="64:_filename"></div></div></form>
<style>[role=button]{display:inline-block;width:24px;height:24px} [role=dialog]{display:block;width:400px;min-height:80px}</style>
<script>
window.sent=false;window.mode='normal';window.target='62';
window.openUpload=()=>{
 if(window.mode==='none')return;
 const d=document.createElement('div');d.setAttribute('role','dialog');d.setAttribute('aria-modal','true');d.setAttribute('aria-label',window.mode==='wrong_title'?'Unrelated file upload':'Select a source for your file upload');
 d.innerHTML='<span>Upload from Device</span><input id="63:_file" type=file form=application name=fileData1 aria-label="Upload from Device">';
 if(window.mode==='disabled')d.querySelector('input').disabled=true;
 if(window.mode==='fields')d.insertAdjacentHTML('beforeend','<input type=email aria-label="Candidate email">');
 if(window.mode==='two_files')d.insertAdjacentHTML('beforeend','<input type=file aria-label="Other file">');
 d.querySelector('input').onchange=event=>{
  if(window.mode==='no_ack')return;
  const name=event.target.files[0].name;
  const own=document.getElementById('62:_attach');
  own.querySelector('[id$="_attachLoading"]').style.display='block';
  own.querySelector('[id$="_attachSuccess"]').style.display='none';
  setTimeout(()=>{
   document.getElementById(window.target+':_filename').textContent=name;
   own.querySelector('[id$="_attachLoading"]').style.display='none';
   if(window.target==='62')own.querySelector('[id$="_attachSuccess"]').style.display='block';
   d.remove();
  },80);
 };
 document.body.appendChild(d);
 if(window.mode==='two_dialogs')document.body.appendChild(d.cloneNode(true));
};
</script>"""


@pytest.fixture
def env(tmp_path):
 with sync_playwright() as pw:
  browser=pw.chromium.launch(channel='chrome',headless=True);page=browser.new_page();page.set_content(HTML)
  wait=page.wait_for_timeout;page.wait_for_timeout=lambda ms:wait(min(ms,30))
  path=tmp_path/'resume.pdf';path.write_bytes(b'%PDF-1.4 actual expected file bytes')
  yield page,path
  for proof in getattr(page,'_jobagent_upload_proofs',[]):proof.close()
  browser.close()


def upload(page,path):
 view=forms.read_page(page);f=next(f for f in view.fields if f.kind=='file' and f.identity=='id:62:_attachIcon')
 return forms.fill_field(page,view,f,str(path),{})


def test_portaled_native_bytes_need_the_original_widget_ack_and_support_same_name_replacement(env):
 p,path=env
 assert upload(p,path)==str(path)
 assert p.locator('[id="62:_filename"]').inner_text()==path.name
 proof=p._jobagent_upload_proofs[-1]
 assert proof.widget.get_attribute('id')=='62:_attach'
 assert proof.portaled and proof.form_owned is False and proof.verify()
 p.locator('body').evaluate('''(e,name)=>{const a=document.createElement('div');a.setAttribute('role','alert');
 a.textContent='\ue203'+name+' File uploaded successfully.';e.appendChild(a)}''',path.name)
 assert forms.read_page(p).errors==[], 'a successful attachment alert is not a validation error'
 assert upload(p,path)==str(path)
 assert p._jobagent_upload_proofs[-1].method=='dispatched bytes and fresh widget success'
 assert not proof.verify(), 'a later attachment edit invalidates the old proof'
 assert p.evaluate('window.sent') is False


def test_completed_widget_class_keeps_its_byte_backed_upload_proof(env):
 p,path=env
 p.evaluate('''()=>{const original=window.openUpload;window.openUpload=()=>{
  original();const input=document.querySelector('[role=dialog] input');const accepted=input.onchange;
  input.onchange=event=>{accepted(event);setTimeout(()=>document.getElementById('62:_attach').className='successBtn',40)};
 }}''')
 assert upload(p,path)==str(path)
 proof=p._jobagent_upload_proofs[-1]
 assert proof.widget.get_attribute('class')=='successBtn'
 assert proof.verify() and proof.method=='dispatched bytes and fresh widget success'
 assert forms.read_page(p).errors==[]
 p.locator('[id="62:_attachSuccess"]').evaluate("e=>e.setAttribute('aria-invalid','true')")
 assert not proof.verify(), 'completed styling must not hide a real error'
 assert not p.evaluate('window.sent')


@pytest.mark.parametrize('completed', [False, True])
def test_staging_inside_the_same_form_and_an_old_success_cannot_acknowledge_a_new_upload(env,completed):
 p,path=env;p.evaluate("window.mode='no_ack'")
 if completed:p.locator('[id="62:_attach"]').evaluate("e=>e.className='successBtn'")
 p.locator('[id="62:_filename"]').evaluate('(e,n)=>e.textContent=n',path.name)
 p.locator('[id="62:_attachSuccess"]').evaluate("e=>e.style.display='block'")
 with pytest.raises(forms.FillError,match='never showed'):upload(p,path)
 assert not p._jobagent_upload_proofs[-1].verify()
 assert not p.evaluate('window.sent')


def test_other_attachment_acknowledgement_does_not_belong_to_this_upload(env):
 p,path=env;p.evaluate("window.target='64'")
 with pytest.raises(forms.FillError,match='never showed'):upload(p,path)
 assert p.locator('[id="64:_filename"]').inner_text()==path.name
 assert not p.locator('[id="62:_filename"]').inner_text()
 assert not p._jobagent_upload_proofs[-1].verify()


def test_success_caption_never_masks_explicit_invalidity(env):
 p,path=env
 p.locator('[id="62:_attachSuccess"]').evaluate("e=>e.setAttribute('aria-invalid','true')")
 with pytest.raises(forms.FillError,match='never showed'):upload(p,path)
 assert not p._jobagent_upload_proofs[-1].verify()
 assert forms.read_page(p).errors==['File is uploaded successfully']


@pytest.mark.parametrize('kind',['wrong_file','invalid','no_widget_ack'])
def test_a_global_success_toast_needs_its_own_valid_filename_acknowledgement(env,kind):
 p,path=env
 assert upload(p,path)==str(path)
 name='unrelated.pdf' if kind=='wrong_file' else path.name
 if kind=='no_widget_ack':p.locator('[id="62:_attachSuccess"]').evaluate("e=>e.style.display='none'")
 p.locator('body').evaluate('''(e,args)=>{const a=document.createElement('div');a.setAttribute('role','alert');
 if(args.invalid)a.setAttribute('aria-invalid','true');
 a.textContent='\ue203'+args.name+' File uploaded successfully.';e.appendChild(a)}''',{'name':name,'invalid':kind=='invalid'})
 assert any(name+' File uploaded successfully.' in text for text in forms.read_page(p).errors)


def test_an_existing_source_dialog_cannot_be_attributed_to_this_click(env):
 p,path=env;p.evaluate("window.mode='none'")
 p.locator('body').evaluate('''e=>e.insertAdjacentHTML('beforeend',
 '<div role="dialog" aria-modal="true" aria-label="Select a source for your file upload"><input type="file" aria-label="Unrelated upload"></div>')''')
 with pytest.raises(forms.FillError):upload(p,path)
 assert p.locator('input[type=file]').evaluate_all('(es)=>es.every(e=>e.files.length===0)')


@pytest.mark.parametrize('mode',['wrong_title','fields','two_files','two_dialogs','disabled'])
def test_ambiguous_or_unrelated_source_dialogs_stop_before_dispatch(env,mode):
 p,path=env;p.evaluate('(mode)=>window.mode=mode',mode)
 with pytest.raises(forms.FillError):upload(p,path)
 assert p.locator('input[type=file]').evaluate_all('(es)=>es.every(e=>e.files.length===0)')
 assert not p.evaluate('window.sent')
