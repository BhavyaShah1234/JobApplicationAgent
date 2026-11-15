"""Bounded passive discovery; never reveals branches by changing applicant answers."""
import json
import re
import time

INVENTORY = r'''() => {
const roots=[document], out=[];
for(let i=0;i<roots.length;i++) for(const e of roots[i].querySelectorAll('*')) {
 const r=e.shadowRoot || window.__jobagentClosedRoots?.get(e); if(r) roots.push(r);
 if(!e.matches('input:not([type=hidden]),select,textarea,[role=combobox]'))continue;
 const rect=e.getBoundingClientRect(), s=getComputedStyle(e);
 const lab=e.getAttribute('aria-label') || (e.labels?.[0]?.textContent) || e.placeholder || '';
 if(lab) out.push({label:lab.trim(), type:e.type||e.getAttribute('role')||e.tagName.toLowerCase(),
   visible:rect.width>1&&rect.height>1&&s.visibility!=='hidden'&&s.display!=='none',
   required:e.required||e.getAttribute('aria-required')==='true',format:e.placeholder||'',source:'DOM'});
} return out;
}'''


def extract(data, source, depth=0, inherited_condition=None, section=''):
    if depth > 12:
        return []
    out=[]
    if isinstance(data, dict):
        condition=data.get('condition',inherited_condition)
        if condition is not None and condition!='' and not condition:condition={'expression':condition}
        if inherited_condition is not None and condition!=inherited_condition:
            condition={'parent':inherited_condition,'own':condition}
        section=str(data.get('section') or section)
        label=data.get('label') or data.get('question')
        kind=data.get('type') or data.get('controlType')
        if isinstance(label,str) and isinstance(kind,str):
            out.append({'label':label[:300],'type':kind[:80], 'source':source,
                        'required':bool(data.get('required')), 'visible':False,
                        'condition':condition, 'potential':True,
                        'applicable':data.get('applicable') is True,
                        'section':section,
                        'options':data.get('options') if isinstance(data.get('options'),list) and all(isinstance(x,str) for x in data['options']) else [],
                        'schema_id':str(data.get('id') or data.get('name') or ''),
                        'format':str(data.get('format') or '')})
        for name,value in list(data.items())[:300]:
            if name!='condition':out.extend(extract(value,source,depth+1,condition,section))
    elif isinstance(data,list):
        for value in data[:300]:out.extend(extract(value,source,depth+1,inherited_condition,section))
    return out


class Discovery:
    def __init__(self, page, budget=30):
        self.page=page;self.budget=budget;self.delivered=[];self.responses=0
        self.page.on('requestfinished',self.received)

    def received(self, request):
        if self.responses>=32:return
        response=request.response()
        if response is None:return
        kind=response.headers.get('content-type','')
        if 'json' not in kind and 'javascript' not in kind:return
        length=response.headers.get('content-length')
        if length and (not length.isdigit() or int(length)>100000):return
        self.responses+=1
        try:
            self.parse(response.text(), 'delivered JSON' if 'json' in kind else 'loaded JavaScript')
        except Exception:pass

    def parse(self,text,source):
        if len(text)>100000:return
        if source.endswith('JavaScript'):
            # Only JSON-compatible schema literals; no eval and no arbitrary bundle analysis.
            for m in re.finditer(r'(?:formSchema|questionnaire|questions)\s*[:=]\s*',text):
                try:
                    value,_=json.JSONDecoder().raw_decode(text[m.end():]);self.delivered+=extract(value,source)
                except ValueError:pass
        else:
            try:self.delivered+=extract(json.loads(text),source)
            except ValueError:pass

    def inventory(self):
        deadline=time.monotonic()+self.budget;out=[];problems=[]
        from jobagent.tools.forms import frames_of
        for frame in frames_of(self.page):
            if time.monotonic()>=deadline:
                problems.append('upfront discovery budget exhausted');break
            try:
                # DOM evaluation is local and contains no async work or network calls.
                out+=frame.evaluate(INVENTORY)
                scripts=frame.locator('script[type="application/json"],script:not([src]):not([type])')
                for text in scripts.all_text_contents()[:20]:
                    if time.monotonic()>=deadline:break
                    self.parse(text,'embedded JSON' if text.lstrip().startswith(('{','[')) else 'loaded JavaScript')
            except Exception as e:problems.append(type(e).__name__)
        out+=self.delivered
        unique={json.dumps(x,sort_keys=True):x for x in out}
        return {'fields':list(unique.values()),'problems':problems}

    def close(self):
        self.page.remove_listener('requestfinished',self.received)
