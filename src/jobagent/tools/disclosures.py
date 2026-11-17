"""Discover and open form sections without selecting applicant answers."""

INVENTORY = r'''() => {
 const roots=[document];
 for(let i=0;i<roots.length;i++)for(const e of roots[i].querySelectorAll('*')){
   const r=e.shadowRoot||window.__jobagentClosedRoots?.get(e);if(r)roots.push(r);
 }
 const all=s=>roots.flatMap(r=>[...r.querySelectorAll(s)]);
 const visible=e=>{for(let a=e;a;a=a.parentElement){const s=getComputedStyle(a);
   if(a.hidden||s.display==='none'||s.visibility==='hidden')return false;}
   const r=e.getBoundingClientRect();return r.width>1&&r.height>1;};
 const controls='input:not([type=hidden]):not([disabled]),select:not([disabled]),textarea:not([disabled]),[role=combobox]';
 const safe=e=>!e.disabled&&e.getAttribute('aria-disabled')!=='true'&&
   !(e.tagName==='BUTTON'&&e.form&&e.type!=='button')&&
   !(e.tagName==='A'&&e.getAttribute('href')&&!/^#/.test(e.getAttribute('href')));
 const rows=[];
 for(const e of all('button[aria-expanded=false],[role=button][aria-expanded=false],details:not([open]) > summary')){
   if(!visible(e)||e.hasAttribute('aria-haspopup'))continue;
   const root=e.getRootNode();
   const controlled=(e.getAttribute('aria-controls')||'').split(/\s+/).map(id=>root.getElementById(id)).filter(Boolean);
   const heading=e.closest('h1,h2,h3,h4,h5,h6,[role=heading]');
   const parent=heading?.parentElement;
   if(!controlled.length&&parent&&!parent.matches('body,form'))
     controlled.push(...[...parent.children].filter(c=>c!==heading&&!visible(c)));
   if(e.tagName==='SUMMARY')controlled.push(e.parentElement);
   if(!controlled.some(r=>(e.tagName==='SUMMARY'||!visible(r))&&(r.matches?.(controls)||r.querySelector(controls))))continue;
   const label=(e.getAttribute('aria-label')||e.innerText||'Unnamed form section').replace(/\s+/g,' ').trim().slice(0,160);
   if(/^(submit|send|apply|finish|save|continue|next)\b/i.test(label))continue;
   rows.push({element:e,label,can_open:safe(e)});
 }
 if(rows.length){
   const expand=all('button,[role=button],a').find(e=>visible(e)&&safe(e)&&
     /^expand all (sections|panels|details)$/i.test((e.getAttribute('aria-label')||e.innerText||'').trim()));
   if(expand)rows.unshift({element:expand,label:'Expand all sections',can_open:true});
 }
 all('[data-jf-disclosure]').forEach(e=>e.removeAttribute('data-jf-disclosure'));
 return rows.map((r,i)=>{r.element.setAttribute('data-jf-disclosure',String(i));
   return {id:i,label:r.label,can_open:r.can_open};});
}'''


def inventory(frame) -> list[dict]:
    return frame.evaluate(INVENTORY)


def expand(page, view, log=lambda _: None) -> bool:
    """Open one proved disclosure, then let the caller recapture its changed DOM.

    Default submit buttons inside a form are never clicked, even when labelled
    as headings. A separate non-submitting Expand all control can open them.
    """
    from jobagent.tools.shadow import locator
    for frame in view.frames.values():
        for row in inventory(frame):
            if not row['can_open']:
                continue
            target = locator(frame, f'[data-jf-disclosure="{row["id"]}"]')
            target.click(timeout=3000)
            page.wait_for_timeout(200)
            log(f"apply: expanded form section {row['label']!r}")
            return True
    return False
