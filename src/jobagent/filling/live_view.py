"""Incremental form execution; a structural change invalidates the captured view."""
from dataclasses import replace
from jobagent.tools import forms

WATCH = r'''() => {
 if(window.__jobagentFormWatch)return;
 const controls='input:not([type=hidden]),select,textarea,[role=combobox],button[aria-pressed]';
 const menu=n=>n.nodeType===1 && n.closest('[role=listbox],[role=option],[role=menu],.react-datepicker');
 const contains=n=>n.nodeType===1 && !menu(n) && (n.matches(controls)||n.querySelector(controls));
 const state={dirty:false};
 const accept=records=>{for(const r of records){
  if(menu(r.target))continue;
  if(r.type==='attributes' && contains(r.target))state.dirty=true;
  if(r.type==='childList' && [...r.addedNodes,...r.removedNodes].some(contains))state.dirty=true;
 }};
 state.observer=new MutationObserver(accept);
 state.observer.observe(document,{subtree:true,childList:true,attributes:true,
  attributeFilter:['hidden','style','disabled','aria-hidden','required','aria-required']});
 state.changed=()=>{accept(state.observer.takeRecords());return state.dirty};
 window.__jobagentFormWatch=state;
}'''

READ = r'''(e,kind)=>{
 if(!e.isConnected)return null;
 const clean=t=>(t||'').replace(/\s+/g,' ').trim();
 const root=e.getRootNode(),tag=e.getAttribute('data-jf');
 const members=[...root.querySelectorAll('[data-jf]')].filter(x=>x.getAttribute('data-jf')===tag&&x.hasAttribute('data-jf-opt'));
 const caption=(__JOBAGENT_CAPTION__);
 const byIds=(x,attr)=>clean((x.getAttribute(attr)||'').split(/\s+/)
  .map(id=>x.getRootNode().getElementById(id)).filter(Boolean).map(caption).join(' '));
 const label=x=>{
  // Native groups use their associated visible captions in the full reader.
  // innerText joins punctuation around links differently and includes helper
  // text; it cannot serve as a second definition of the same option.
  if(x.matches('input')){
   const own=x.closest('label');
   const associated=x.id&&x.getRootNode().querySelector(`label[for="${CSS.escape(x.id)}"]`);
   return (own&&caption(own))||(associated&&caption(associated))||clean(x.getAttribute('aria-label'))||x.value;
  }
  return byIds(x,'aria-labelledby')||clean(x.getAttribute('aria-label'))||caption(x);
 };
 let value=e.isContentEditable?clean(e.innerText):(e.value||'');
 if(kind==='select')value=[...e.selectedOptions].map(x=>clean(x.text)).join('; ');
 if(kind==='checkbox')value=e.checked||e.getAttribute('aria-checked')==='true'?'checked':'';
 if(kind==='buttons')value=clean(e.querySelector('button[aria-pressed=true]')?.innerText);
 if(kind==='radio'||kind==='checkboxes')value=members.filter(x=>x.checked||x.getAttribute('aria-checked')==='true').map(label).join('; ');
 if(kind==='file')value=[...(e.files||[])].map(x=>x.name).join(', ');
 if(kind==='combobox'){
  const widgetValue=(__JOBAGENT_WIDGET_VALUE__)(e);
  if(widgetValue !== null)return {value:widgetValue,invalid:e.getAttribute('aria-invalid')==='true'||
    (e.matches('.select2-search__field')&&e.closest('.select2-selection--multiple')?.getAttribute('aria-invalid')==='true')||!!(e.validity&&!e.validity.valid)};
  if(e.tagName==='BUTTON')value=clean(e.innerText).replace(/^(select all that apply|select( an?)?( answer| option| one)?)\b\s*/i,'');
  const wd=e.closest('[data-automation-id=multiSelectContainer]');
  if(wd)value=[...wd.querySelectorAll('[data-automation-id=selectedItemList] li')].map(x=>clean(x.innerText)).join('; ');
  else {const box=e.closest('[class*=control i]')||e.parentElement;
   const picked=box?.querySelector('[class*=singleValue],[class*=single-value],[class*=single_value]');
   if(picked)value=clean(picked.innerText);}
 }
 return {value,invalid:e.getAttribute('aria-invalid')==='true'||!!(e.validity&&!e.validity.valid)};
}'''.replace('__JOBAGENT_WIDGET_VALUE__', forms.WIDGET_VALUE_JS).replace('__JOBAGENT_CAPTION__', forms.CAPTION_JS)


class LiveView:
    def __init__(self,page,reader):
        self.page=page;self.reader=reader;self.view=None;self.reads=0;self.catalogues={}

    def capture(self):
        self.view=self.reader(self.page);self.reads+=1
        for frame in self.view.frames.values():
            frame.evaluate(WATCH)
            frame.evaluate('()=>{const s=window.__jobagentFormWatch;s.observer.takeRecords();s.dirty=false}')
        for f in self.view.fields:
            cached=self.catalogues.get(f.locator_ref)
            if f.kind=='combobox' and cached and not f.options:
                f.options,f.options_state=list(cached[0]),cached[1]
        return self.view

    def changed(self):
        if self.view is None:return True
        try:
            return any(frame.evaluate('()=>!window.__jobagentFormWatch||window.__jobagentFormWatch.changed()') for frame in self.view.frames.values())
        except Exception:return True

    def current(self):
        return self.capture() if self.changed() else self.view

    def remember_options(self,fields):
        for f in fields:
            if f.kind=='combobox' and f.options:self.catalogues[f.locator_ref]=(list(f.options),f.options_state)

    def refresh(self,f):
        if f.kind=='date_parts':return None
        try:
            info=forms.locate(self.view,f).evaluate(READ,f.kind)
            if info is None:return None
            current=replace(f,**info)
            self.view.fields=[current if x.locator_ref==f.locator_ref else x for x in self.view.fields]
            # Geography is a dependency: changing Country/State invalidates only its child pickers.
            if f.value!=current.value and forms._norm(f.label).strip(' *') in ('country','state','province'):
                for child in self.view.fields:
                    if child.kind=='combobox' and child.locator_ref!=f.locator_ref and any(w in forms._norm(child.label) for w in ('state','province','city','location')):
                        self.catalogues.pop(child.locator_ref,None);child.options=[]
            return current
        except Exception:return None
