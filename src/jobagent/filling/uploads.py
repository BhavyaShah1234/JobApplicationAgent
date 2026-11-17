"""Evidence for one upload: dispatched bytes plus acknowledgement by that widget."""
import base64
import hashlib
from pathlib import Path


class UploadProof:
    def __init__(self, path, trigger=None):
        self.path=Path(path)
        content=self.path.read_bytes()
        self.expected=hashlib.sha256(content).hexdigest();self.size=len(content);self.name=self.path.name
        self.native=None;self.widget=None;self.token=None;self.form=None
        self.method=''
        if trigger:
            self.widget=self.widget_for(trigger)
            self.form=trigger.evaluate_handle('e=>e.form||e.closest("form")').as_element()
        self.before=self.widget.inner_text() if self.widget else ''

    @staticmethod
    def widget_for(element):
        handle=element.evaluate_handle('''e=>{
            const field=e.parentElement?.closest('fieldset,[data-field-path],[data-field-entry-id],[data-automation-id^=formField-],.file-upload,.form-field,.form-group');
            if(field && field.querySelectorAll('input[type=file]').length<=1 &&
               ![...field.querySelectorAll('input:not([type=file]):not([type=hidden]),select,textarea,[role=combobox]')].some(c=>c!==e))return field;
            let candidate=null;
            for(let a=e.parentElement;a && !a.matches('form,body,html');a=a.parentElement){
                const controls=[...a.querySelectorAll('input,select,textarea,[role=combobox],button,[role=button]')];
                if(controls.some(c=>c!==e && !e.contains(c)))break;
                candidate=a;
                if(a.matches('fieldset,[data-field-path],[data-field-entry-id],[class*=upload i],.form-group,.form-field'))break;
            }
            return candidate;
        }''')
        return handle.as_element()

    def bind(self, native):
        self.native=native.element_handle() if hasattr(native,'element_handle') else native
        self.token=self.native.evaluate('''e=>{
            const w=e.ownerDocument.defaultView, token=crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
            w.__jobagentUploadProofs ||= new Map();
            const state={file:null,digestFile:null,digest:null};w.__jobagentUploadProofs.set(token,state);
            const capture=event=>{if(event.target===e && e.files?.length===1)state.file=e.files[0]};
            e.ownerDocument.addEventListener('input',capture,true);
            e.ownerDocument.addEventListener('change',capture,true);
            state.cleanup=()=>{e.ownerDocument.removeEventListener('input',capture,true);
                e.ownerDocument.removeEventListener('change',capture,true)};
            return token;
        }''')
        if not self.widget:
            self.widget=self.widget_for(self.native)
            self.before=self.widget.inner_text() if self.widget else ''
        self.initially_inside=bool(self.widget and self.widget.evaluate('(box,n)=>box.contains(n)',self.native))
        self.form_owned=bool(self.form and self.native.evaluate('(n,form)=>form.isConnected && n.form===form && !!n.name && !n.disabled',self.form))

    def owns(self, element):
        try:
            return bool(self.native and element.evaluate('(e,n)=>e===n',self.native) or
                        self.widget and element.evaluate('(e,box)=>box.isConnected && box.contains(e)',self.widget))
        except Exception:return False

    def verify(self):
        if not self.native:return False
        try:
            if self.widget and self.widget.evaluate('''(box,args)=>{
                const visible=e=>!!(e.getBoundingClientRect().width&&e.getBoundingClientRect().height)&&getComputedStyle(e).visibility!=='hidden';
                return !box.isConnected || (args.inside&&args.native.isConnected&&!box.contains(args.native)) ||
                    [...box.querySelectorAll('input[type=file]')].some(e=>e!==args.native&&e.files?.length) ||
                    [...box.querySelectorAll('[aria-invalid=true],[role=alert],[aria-busy=true],[role=progressbar],progress,[class*=progress]')].some(visible);
            }''',{'native':self.native,'inside':getattr(self,'initially_inside',False)}):return False
            evidence=self.native.evaluate('''async (e,token)=>{
                const state=e.ownerDocument.defaultView.__jobagentUploadProofs?.get(token);
                const retained=e.isConnected && e.files?.[0];
                const f=retained || state?.file;if(!f || f.size>5000000)return null;
                let digest=state?.digestFile===f ? state.digest : null;
                if(!digest){
                    const bytes=new Uint8Array(await f.arrayBuffer());
                    if(globalThis.crypto?.subtle){const sha=await crypto.subtle.digest('SHA-256',bytes);digest={sha256:[...new Uint8Array(sha)].map(n=>n.toString(16).padStart(2,'0')).join('')};}
                    else {let s='';for(let i=0;i<bytes.length;i+=8192)s+=String.fromCharCode(...bytes.subarray(i,i+8192));digest={bytes:btoa(s)};}
                    if(state){state.digestFile=f;state.digest=digest;}
                }
                return {...digest,name:f.name,retained:!!retained,invalid:e.isConnected &&
                    (e.getAttribute('aria-invalid')==='true' || (retained && e.validity && !e.validity.valid))};
            }''',self.token)
            if not evidence or evidence['invalid']:return False
            actual=evidence.get('sha256') or hashlib.sha256(base64.b64decode(evidence['bytes'])).hexdigest()
            if actual!=self.expected:return False
            self.name=evidence['name']
            form_owned=bool(getattr(self,'form_owned',False) and self.native.evaluate('(n,form)=>form.isConnected && n.form===form && !!n.name && !n.disabled',self.form))
            if evidence['retained'] and (not self.widget or getattr(self,'initially_inside',False) or form_owned):
                self.method='native bytes';return True
            if evidence['name'] not in self.before and self.widget and self.widget.evaluate('''(box,name)=>box.isConnected &&
                (box.innerText||'').includes(name) && !box.querySelector('[aria-invalid=true],[role=alert]')''',evidence['name']):
                self.method='dispatched bytes and widget acknowledgement';return True
        except Exception:
            pass
        return False

    def close(self):
        if self.native:
            try:self.native.evaluate('(e,t)=>{const m=e.ownerDocument.defaultView.__jobagentUploadProofs;m?.get(t)?.cleanup();m?.delete(t)}',self.token)
            except Exception:pass
        for handle in (self.native,self.widget,self.form):
            if handle:
                try:handle.dispose()
                except Exception:pass
        self.native=None;self.widget=None;self.form=None
