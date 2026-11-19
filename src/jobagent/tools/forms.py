"""Step 12: application forms. Read every visible field of the page (main page and iframes), match each one to an
answer, fill it, and read it back.

A field is one question: a text box, a `<select>`, a radio group, a checkbox (or a group of them), a file input, or
a combobox (ARIA comboboxes such as react-select on Greenhouse, Workday's listbox buttons and search prompts). Every
field gets a `data-jf` number on the page, so the code finds it again without asking a model. Option fields are
matched to the page's own options (rapidfuzz, then a model tie-break); every value is read back from the page after
filling. Submit buttons are never clicked here.
"""

import json
import re
from datetime import datetime
from dataclasses import dataclass, field, replace
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import Frame, Page, Error as PlaywrightError
from rapidfuzz import fuzz, process
from jobagent.tools.shadow import locator as shadow_locator, field_element

CAPTCHA_FRAME = re.compile(r"(?i)hcaptcha|recaptcha|google\.com/recaptcha|challenges\.cloudflare|arkoselabs|funcaptcha")

# Buttons that send the application: never clicked by the agent (the user submits after review, step 13)
SUBMIT = re.compile(r"(?i)^\s*(submit|send)\b|submit (my |your )?application|^\s*finish\b|complete (my |your )?"
                    r"application|^\s*apply\s*$")
TEXT_KINDS = ("date_parts", "text", "email", "tel", "url", "number", "date", "textarea", "spin", "password")
NEXT = re.compile(r"(?i)^\s*(next|continue|(?:save|skip) (and|&) continue|proceed|review( (my |your )?application)?)\b")

WIDGET_VALUE_JS = r"""e => {
  const clean = t => (t || '').replace(/\s+/g, ' ').trim();
  const multi = e.matches('.select2-search__field') && e.closest('.select2-selection--multiple');
  if (multi) {
    const original = multi.closest('.select2-container')?.previousElementSibling;
    if (original?.matches('select[multiple].select2-hidden-accessible'))
      return [...original.selectedOptions].filter(o => o.value && !o.disabled).map(o => clean(o.text)).join('; ');
    return [...multi.querySelectorAll('.select2-selection__choice[title]')].map(e => clean(e.title)).join('; ');
  }
  if (e.matches('.select2-selection--single[role=combobox]')) {
    if (e.querySelector('.select2-selection__placeholder')) return '';
    return clean(e.querySelector('.select2-selection__rendered')?.innerText);
  }
  if (e.matches('.iti__selected-flag[role=combobox]'))
    return clean(e.getAttribute('title')).replace(/:\s*(\+\d+)\s*$/, ' ($1)');
  return null;
}"""

CAPTION_JS = r"""e => {
    const clean = t => (t || '').replace(/\s+/g, ' ').trim();
    // Read the original nodes: detached clones lose CSS visibility and expose
    // hidden loading/error text. Exclude option subtrees before walking text.
    const text=(n,root=false)=>{
      if(n.nodeType===Node.TEXT_NODE)return n.textContent;
      if(n.nodeType!==Node.ELEMENT_NODE)return '';
      if(!root && n.matches('select,[role=listbox],[role=option]'))return '';
      const style=getComputedStyle(n);
      if(!root && (n.hidden || style.display==='none' || style.visibility==='hidden'))return '';
      return [...n.childNodes].map(c=>text(c)).join(' ');
    };
    return clean(text(e,true));
  }"""

FIELDS_JS = r"""(start) => {
  const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 1 && r.height > 1 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const shown = e => { for (let a = e; a && a !== document.body; a = a.parentElement) {
      const s = getComputedStyle(a); if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false; }
    return true; };
  const clean = t => (t || '').replace(/\s+/g, ' ').trim();
  const caption = (__JOBAGENT_CAPTION__);
  // F17: author roots, including closed roots recovered through the DOM protocol, share the same reader.
  const roots = [];
  const walk = r => { roots.push(r); for (const h of r.querySelectorAll('*')) {
    const s=h.shadowRoot || window.__jobagentClosedRoots?.get(h); if(s)walk(s); } };
  walk(document);
  const all = sel => roots.flatMap(r => [...r.querySelectorAll(sel)]);
  const anchor = e => { let x = e; while (x.getRootNode() instanceof ShadowRoot) x = x.getRootNode().host; return x; };
  const mediaControl = e => {
    // Media Chrome / native player controls remain media controls across their
    // author shadow roots. An ordinary application slider is still a field.
    for(let a=e;a;a=a.parentElement || a.getRootNode()?.host){
      if(a.matches?.('video,audio,media-controller,media-time-range,media-volume-range,mux-player'))return true;
    }
    return false;
  };
  const byIds = (ids, ctx) => clean((ids || '').split(/\s+/).map(i => (ctx ? ctx.getRootNode() : document).getElementById(i))
      .filter(Boolean).map(x => caption(x)).join(' '));
  const ownText = e => { const l = e.closest('label'); return l ? caption(l) : ''; };
  // a widget's own caption, never the question (Carvana's "SELECT ANSWER" / "SELECT ALL THAT APPLY" boxes)
  const GENERIC = /^(type your response|type here|enter (your )?answer|select( an?)?( answer| option| one| all that apply)?|choose( one| an option)?|please (select|choose)( one)?|-+ ?select ?-+|-+)$/i;
  const CONTROLS = 'input:not([type=hidden]), select, textarea, [role=combobox], [aria-haspopup=listbox]';
  const before = (a, b) => !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
  const nearLabel = e => {
    // the closest label / legend above the control, in its own block: no other control between the two, and not a
    // label[for] of another existing control (custom widgets whose label[for] names no element, e.g. Carvana)
    for (let a = e.parentElement, k = 0; a && k < 5; a = a.parentElement, k++) {
      const ls = [...a.querySelectorAll('label, legend')].filter(l => !l.contains(e) && before(l, e) && clean(l.innerText)
          && !GENERIC.test(clean(l.innerText)));
      if (!ls.length) continue;
      const l = ls[ls.length - 1];
      const f = l.getAttribute('for'); const tgt = f && e.getRootNode().getElementById(f);
      if (tgt && tgt !== e && !tgt.contains(e) && !e.contains(tgt)) return '';
      const between = [...a.querySelectorAll(CONTROLS)].some(c => c !== e && !c.contains(e) && !e.contains(c)
          && before(l, c) && before(c, e) && vis(c));
      return between ? '' : clean(l.innerText); }
    return ''; };
  const question = e => {
    // the question a control belongs to: aria, label[for], wrapping label, legend, or the nearest container text
    // Workday: the form field's own label (a list button's aria-label holds its value: "Select One Required")
    const ff = e.closest('[data-automation-id^=formField-]');
    const fl = ff && ff.querySelector(':scope > label, :scope > legend, :scope > fieldset > legend, label');
    if (fl && caption(fl)) return caption(fl);
    if (e.matches('.select2-search__field') && e.closest('.select2-selection--multiple')) {
      const original = e.closest('.select2-container')?.previousElementSibling;
      if (!original?.matches('select[multiple].select2-hidden-accessible')) return '';
      // The search input is empty even when countries are selected. Its
      // wrapping label contains the selected chips, not the question.
      return byIds(original.getAttribute('aria-labelledby'), original) ||
        [...original.labels].filter(l => !l.contains(original)).map(caption).join(' ') || nearLabel(original);
    }
    if (e.matches('.select2-selection--single[role=combobox]')) {
      const labels = (e.getAttribute('aria-labelledby') || '').split(/\s+/)
        .map(id => e.getRootNode().getElementById(id))
        .filter(x => x && !e.contains(x) && !x.matches('[role=alert], [id$="-error"]') && caption(x));
      const original = e.closest('.select2-container')?.previousElementSibling;
      const nativeLabels = original?.matches('select.select2-hidden-accessible') ? [...original.labels].filter(l => !l.contains(original)) : [];
      const label = labels.find(x => x.matches('label,legend')) || nativeLabels[0] || labels[0];
      // Select2 can rebuild its ARIA references with only the rendered value.
      // That value is never the question; retain the owning static caption.
      return (label && caption(label)) || clean(e.getAttribute('aria-label')) ||
        (original?.matches('select.select2-hidden-accessible') && byIds(original.getAttribute('aria-labelledby'), original)) ||
        nearLabel(original?.matches('select.select2-hidden-accessible') ? original : e);
    }
    let t = byIds(e.getAttribute('aria-labelledby'), e);
    if (!t && e.id) { const l = e.getRootNode().querySelector(`label[for="${CSS.escape(e.id)}"]`); if (l) t = caption(l); }
    if (!t) t = clean(e.getAttribute('aria-label'));
    if (!t) { const l = e.closest('label'); if (l) t = caption(l); }
    if (!t) {
      // Ashby and similar: the question is the first label of the field's block, the input itself has none
      const fc = e.closest('[data-field-path], [data-field-entry-id]');
      const l = fc && [...fc.querySelectorAll('label, legend')].find(x => !x.contains(e) && clean(x.innerText));
      if (l) t = caption(l); }
    if (!t) { const fs = e.closest('fieldset'); const lg = fs && fs.querySelector('legend'); if (lg) t = caption(lg); }
    if (!t || GENERIC.test(t)) {
      const block=e.closest('.application-question,[data-field-path],[data-field-entry-id]');
      const title=block && block.querySelector('.application-label,[class*=question-title],legend');
      if(title)t=caption(title);
    }
    if (!t || GENERIC.test(t)) t = nearLabel(e) || (GENERIC.test(t) ? '' : t);
    if (!t) { const p = clean(e.placeholder || e.getAttribute('title') || ''); if (!GENERIC.test(p)) t = p; }
    if (!t) t = around(e, ownOf(e)).replace(/^(select all that apply|select( an?)?( answer| option| one)?)\b\s*/i, '');
    return t; };
  const ownOf = e => e.tagName === 'SELECT' ? [...e.options].map(o => clean(o.text)) : [clean(e.value)];
  const around = (e, own) => {
    // the nearest container whose text, without the control's own options / value, says something: the question
    // (Lever and many custom forms put it in a sibling div, not a label)
    let a = e.parentElement;
    for (let k = 0; a && k < 6; k++, a = a.parentElement) {
      let x = caption(a);
      for (const o of own) if (o && own.length<100) x = x.split(o).join(' ');
      x = clean(x);
      if (x.length > 1 && x.length < 400) return x;
      if (x.length >= 400) break; }
    return ''; };
  const groupQuestion = (e, name) => {
    // radio / checkbox groups: the group's legend or aria label, else the container text before the options
    if (e.closest('[data-automation-id^=formField-]')) return question(e);  // Workday: the form field's label
    const blk = e.closest('[data-field-path], fieldset, .application-question');
    if (blk?.querySelector(':scope > .application-label'))return caption(blk.querySelector(':scope > .application-label'));
    if (blk) {  // the block's own question: a label that isn't one of the boxes' labels
      const ids = new Set([...blk.querySelectorAll('input')].map(b => b.id).filter(Boolean));
      const q = [...blk.querySelectorAll('label, legend')].find(l => !ids.has(l.getAttribute('for')) && !l.querySelector('input') && clean(l.innerText));
      if (q) return clean(q.innerText); }
    const g = e.closest('fieldset, [role=radiogroup], [role=group]');
    if (g) { const lg = g.querySelector('legend');
      const t = (lg && clean(lg.innerText)) || byIds(g.getAttribute('aria-labelledby'), g) || clean(g.getAttribute('aria-label'));
      if (t) return t; }
    const labels = [...e.getRootNode().querySelectorAll(`input[name="${CSS.escape(name)}"]`)].map(b => {
      const l = b.closest('label') || (b.id && b.getRootNode().querySelector(`label[for="${CSS.escape(b.id)}"]`));
      return l ? clean(l.innerText) : clean(b.value); });
    return around(e, labels) || question(e); };
  const section = e => {
    const ae = anchor(e);
    // Legacy repeating sections expose a real row number beside each row,
    // and name the owning group through aria-labelledby rather than h-tags.
    const row=ae.closest('.rcmSectionComponent[id$=":_sectionComponent"]');
    const group=row && row.closest('[role=group][aria-labelledby]');
    const title=group && byIds(group.getAttribute('aria-labelledby'),group);
    const entity=title && title.match(/^(Education|Work Experience|Employment History)(?:\s*\([^)]*\))?$/i);
    const number=row && row.getRootNode().getElementById(row.id.replace(/_sectionComponent$/,'_rowNum'));
    const index=number && clean(number.textContent).match(/^Row number (\d+)$/i);
    if(entity && index && Number(index[1])>0 && number.parentElement===row.parentElement)
      return (/^Education$/i.test(entity[1])?'Education':'Work Experience')+' '+Number(index[1]);
    // the nearest heading above the field (e.g. "Work Experience 2", "Voluntary Self-Identification")
    // headings only: a legend is a field's own label (Workday's "To*" date), not a section
    const hs = all('h1, h2, h3, h4, h5, [role=heading], [class*=header] > p, [class*=title] > p').filter(h => vis(h)
      && (/^H[1-5]$/.test(h.tagName) || h.getAttribute('role')==='heading' || /^(education|work experience|employment history|languages)(?: \d+)?$/i.test(clean(h.innerText)))
      && !h.closest('[role=dialog],[role=listbox],[role=menu],[class*=datepicker i],[class*=calendar i]'));
    let best = '';
    for (const h of hs) {
      if(h.tagName==='P' && !h.parentElement?.parentElement?.contains(e))continue;
      if (anchor(h).compareDocumentPosition(ae) & Node.DOCUMENT_POSITION_FOLLOWING) best = clean(h.innerText);
    }
    if(/^education$/i.test(best)) {
      const index=(e.id||'').match(/--(\d+)$/);
      if(index)return 'Education '+(Number(index[1])+1);
      return 'Education 1';
    }
    const education=e.closest('[class*=education--entry],[class*=education-entry], [data-education-index]');
    if(education && /^education$/i.test(best)) {
      const peers=all('[class*=education--entry],[class*=education-entry],[data-education-index]');
      return 'Education '+(peers.indexOf(education)+1);
    }
    return best.slice(0, 80); };
  // a required marker is "*" or a lookalike (Lever uses U+2731), at the end of the caption or followed by a space
  const star = q => /[*✱∗＊⁎]\s*$|[*✱∗＊⁎]\s|\(required\)/i.test(q||'');
  const required = (e, q) => {
    if(e.required || e.getAttribute('aria-required')==='true' || star(q))return true;
    // Inspect the original own caption and its CSS marker, independently of
    // the field kind's derived label or any shortened display representation.
    const field=e.closest('[data-automation-id^=formField-]');
    const own=field && field.querySelector(':scope > label, :scope > legend, :scope > fieldset > legend, label');
    if(own && (star(caption(own)) || star(getComputedStyle(own,'::after').content.replace(/["']/g,''))))return true;
    const box=e.closest('[data-field-path],[data-field-entry-id],fieldset,[class*=fieldEntry i]') || e.parentElement;
    const labels=[...(e.labels||[]),...(box ? box.querySelectorAll('legend,label') : [])];
    return labels.some(l=>{
      const target=l.htmlFor && l.getRootNode().getElementById(l.htmlFor);
      const bare=s=>clean(s).replace(/[*✱∗＊⁎]\s*$/,'').trim();
      const header=!l.querySelector('input,select,textarea') && bare(l.textContent)===bare(q) &&
        (!target || target===e || target.contains(e));
      if(l.contains(e) || (l.htmlFor && e.id && l.htmlFor===e.id) ||
          header || (box && box.querySelectorAll('input:not([type=hidden]),select,textarea,[role=combobox],[aria-haspopup=listbox]').length<=1)){
        return star(l.textContent) || star(getComputedStyle(l,'::after').content.replace(/["']/g,''));
      }
      return false;
    });
  };
  // Capture identity in the same DOM snapshot as the question and value.
  // Extra per-field browser round trips make remote desktop filling costly.
  const registry=window.__jobagentFieldNodes ||= {ids:new WeakMap(),next:0,
    epoch:crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`};
  const identity=e=>{
    const r=registry, root=e.getRootNode();
    if(!r.ids.has(e))r.ids.set(e,++r.next);
    if(root!==document && !r.ids.has(root))r.ids.set(root,++r.next);
    const prefix=root===document ? '' : 'root:'+r.ids.get(root)+':';
    const path=e.closest('[data-field-path],[data-field-entry-id]');
    const own=path && (path.getAttribute('data-field-path')||path.getAttribute('data-field-entry-id'));
    const unique=(attr,val)=>val && [...root.querySelectorAll('['+attr+']')].filter(x=>x.getAttribute(attr)===val).length===1;
    if(unique('id',e.id))return prefix+'id:'+e.id;
    if(own && [...root.querySelectorAll('input,select,textarea,[role=combobox]')].filter(x=>{
      const p=x.closest('[data-field-path],[data-field-entry-id]');
      return p && (p.getAttribute('data-field-path')||p.getAttribute('data-field-entry-id'))===own &&
        (x.name||x.type||x.tagName)===(e.name||e.type||e.tagName);
    }).length===1)return prefix+'path:'+own+':'+(e.name||e.type||e.tagName);
    if(unique('name',e.name))return prefix+'name:'+e.name;
    return 'node:'+r.epoch+':'+r.ids.get(e);
  };
  const out = []; let n = start; const seen = new Set(); let shadowFields = 0;
  const add = (e, f) => {
    // Legacy SuccessFactors uses a region/div masthead rather than a header tag.
    // Its Language picker is navigation, not a candidate question.
    if (e.closest('#headerNavBar, #rcmCareerLangDropDown, .userInfoMenu, .rcmUserLangDropDown')) return;
    e.setAttribute('data-jf', String(n));
    if (e.getRootNode() instanceof ShadowRoot) shadowFields++;
    f.id = n++;
    f.identity=identity(e);
    f.frame_identity=registry.epoch;
    f.locator_ref=registry.epoch+':'+f.identity;
    e.setAttribute('data-jf-ref',f.locator_ref);
    f.invalid=e.getAttribute('aria-invalid')==='true' || !!(e.validity && !e.validity.valid);
    const multi = e.matches('.select2-search__field') && e.closest('.select2-selection--multiple');
    if (multi) {
      f.invalid ||= multi.getAttribute('aria-invalid')==='true';
      f.required ||= multi.getAttribute('aria-required')==='true';
    }
    if(f.kind==='combobox')f.multiple=e.getAttribute('aria-multiselectable')==='true'||
      !!multi ||
      (!!e.closest('[data-uxi-widget-type=multiselect]')&&/\bskills\b|(?:select|check|choose) all/i.test(f.label));
    f.required=!!f.required || required(e,f.label);
    f.calendar=e.type!=='date' && e.tagName==='INPUT' &&
      (!!e.closest('.react-datepicker-wrapper,[data-datepicker]') || /(?:^|[\s_-])(?:datepicker|date-picker|input[-_]date|date[-_]input)(?:[\s_-]|$)/i.test(e.className||''));
    if(f.calendar && !f.format)f.format='YYYY-MM-DD';
    if(!f.format && /\b(MM|YYYY|YY|DD)\b/i.test(e.placeholder||''))f.format=e.placeholder;
    f.section = section(e);

    const box = e.closest(
      '[data-field-path], [data-field-entry-id], ' +
      '[data-automation-id^="formField-"], ' +
      '.form-group, .form-field, [class*="formcontrol" i]'
    ) || e.parentElement;

    const hints = box ? [...box.querySelectorAll(
      'small, p, [class*="description" i], [class*="helper" i], ' +
      '[class*="hint" i], [id*="description" i], [id*="helper" i]'
    )].filter(vis).map(x => x.innerText || x.textContent) : [];

    f.help = clean([
      byIds(e.getAttribute('aria-describedby'), e),
      ...hints
    ].join(' ')).slice(0, 1200);

    out.push(f);
  };
  // choice buttons: a question answered by pressing one of a few buttons (Ashby's Yes / No: aria-pressed)
  for (const g of new Set(all('button[aria-pressed]').map(b => b.parentElement))) {
    const btns = [...g.querySelectorAll(':scope > button[aria-pressed]')];
    if (btns.length < 2 || !vis(g) || !shown(g) || g.hasAttribute('data-jf')) continue;
    const blk = g.closest('[data-field-path], [data-field-entry-id], fieldset') || g.parentElement;
    const l = blk && [...blk.querySelectorAll('label, legend')].find(x => !x.contains(g) && clean(x.innerText));
    const q = l ? clean(l.innerText) : around(btns[0], btns.map(b => clean(b.innerText)));
    btns.forEach((b, k) => { b.setAttribute('data-jf-opt', String(k)); b.setAttribute('data-jf-btn', '1'); });
    const hidden = blk && blk.querySelector('input');
    add(g, {kind: 'buttons', label: q, required: !!(hidden && hidden.required) || /\*\s*$/.test(q),
            options: btns.map(b => clean(b.innerText)),
            value: btns.filter(b => b.getAttribute('aria-pressed') === 'true').map(b => clean(b.innerText)).join('; ')});
    btns.forEach(b => b.setAttribute('data-jf', String(n - 1)));
  }
  // Accessible custom radio controls do not always contain native inputs.
  for (const g of all('[role=radiogroup]')) {
    const radios = [...g.querySelectorAll('[role=radio]')].filter(e => e.tagName !== 'INPUT' && vis(e) && shown(e));
    if (!radios.length || g.querySelector('input[type=radio]') || !shown(g)) continue;
    const q = byIds(g.getAttribute('aria-labelledby'), g) || clean(g.getAttribute('aria-label')) || question(g);
    const opts = radios.map(e => byIds(e.getAttribute('aria-labelledby'), e) ||
      clean(e.getAttribute('aria-label') || e.innerText));
    add(g, {kind:'radio',label:q,required:required(g,q),options:opts,
      value:opts.filter((o,k) => radios[k].getAttribute('aria-checked') === 'true').join('; ')});
    radios.forEach((e,k) => {e.setAttribute('data-jf',String(n-1));e.setAttribute('data-jf-opt',String(k));});
  }
  const ctl = 'input, select, textarea, [contenteditable=true], [role=textbox], [role=combobox], button[aria-haspopup=listbox], [role=checkbox], [role=radio], [role=spinbutton]';
  for (const e of all(ctl)) {
    const t = (e.getAttribute('type') || '').toLowerCase();
    if (t === 'hidden' || t === 'submit' || t === 'image' || t === 'reset' || e.disabled) continue;
    if (e.getAttribute('aria-disabled') === 'true' || e.closest('.select2-container--disabled')) continue;
    if (e.matches('select.select2-hidden-accessible')) continue;
    const select2 = e.closest('.select2-container');
    if (select2?.querySelector('.select2-selection--single[role=combobox]') && !e.matches('.select2-selection--single[role=combobox]')) continue;
    if (t === 'button' && e.getAttribute('aria-haspopup') !== 'listbox') continue;
    const dw = e.closest('[data-automation-id=dateInputWrapper]');
    if (dw) {
      // Workday's date: month / day / year boxes (zero-size inputs, the visible text is drawn beside them)
      if (dw.hasAttribute('data-jf') || !vis(dw) || !shown(dw)) continue;
      const ff = dw.closest('[data-automation-id^=formField-]') || dw.parentElement;
      const lg = ff.querySelector('legend, label');
      const q = clean(lg ? lg.innerText : dw.getAttribute('aria-label'));
      const parts = [...dw.querySelectorAll('[data-automation-id$=-display]')].map(d => clean(d.innerText));
      const fmt = [...dw.querySelectorAll('[data-automation-id^=dateSection][data-automation-id$=-input]')]
          .map(i => ({Month: 'MM', Day: 'DD', Year: 'YYYY'})[i.getAttribute('aria-label')] || '??').join('/');
      const val = parts.join('/');
      add(dw, {kind: 'date_parts', label: q + ' (' + fmt + ')', required: [...dw.querySelectorAll('input')].some(i=>required(i,q)),
               value: /[A-Z]{2}/.test(val) ? '' : val, format: fmt});
      continue; }
    if (t === 'file') {
      // file inputs are often hidden behind an "Attach" button: count them when their container shows
      const box = e.closest('div, section, fieldset') || e.parentElement;
      if (!box || !vis(box) || !shown(box)) continue;
      // The caption is the upload's OWN explicit label first (`label[for]`, aria, a wrapping label), then the text of the nearest
      // container that holds THIS upload alone and is short (usually the wrapping label itself). A container that also holds another
      // upload, or is long, adds nothing: on a flat page it is the whole body, whose text names the other documents too, and used to
      // make a Resume input read "... Cover Letter ...", so that the cover-letter file went into both.
      const own = question(e);
      let cap = '';
      for (let a = e.parentElement, k = 0; a && k < 5 && !cap; a = a.parentElement, k++) {
        if ([...a.querySelectorAll('input[type=file]')].some(x => x !== e)) break;
        const x = clean(a.innerText).replace(/\b(attach|dropbox|google drive|enter manually|upload|or drag and drop( here)?|browse)\b/gi, '').replace(/[,|]/g, ' ').trim();
        if (x.length > 2 && x.length <= 120) cap = clean(x); }
      const q = e.getAttribute('data-jf-upload-label') || [own, cap, e.id || e.name].filter(t => t && !/^attach$/i.test(t)).join(' / ');
      add(e, {kind: 'file', label: q, required: required(e, q),   // the question's own marker counts, not only the caption's
              // only files really attached: the box's caption ("ATTACH RESUME/CV") is not a value
              value: e.files && e.files.length ? [...e.files].map(f => f.name).join(', ') : '',
              accept: e.getAttribute('accept') || '',
              // set by the click that opened this input from a lazy trigger: it is that trigger's own native control
              linked: !!e.getAttribute('data-jf-upload-label')});
      continue; }
    // a react-select input shrinks to nothing once a value is chosen: its control box is what shows
    const isCombo = (e.getAttribute('role') || '').toLowerCase() === 'combobox';
    const seenBox = isCombo ? (e.closest('[class*=control i], [class*=container i]') || e.parentElement || e) : e;
    if (!vis(seenBox) && !(t === 'radio' || t === 'checkbox')) continue;
    if ((t === 'radio' || t === 'checkbox') && !vis(e)) {
      // custom-styled boxes hide the input itself; its label must show
      const l = e.closest('label') || (e.id && e.getRootNode().querySelector(`label[for="${CSS.escape(e.id)}"]`));
      if (!l || !vis(l)) continue; }
    if (!shown(seenBox) && !(t === 'radio' || t === 'checkbox')) continue;
    if (mediaControl(e) || e.closest('[data-jf-skip], header, nav, [role=banner], [data-automation-id=navigationContainer]')) continue;
    if (e.closest('[data-automation-id=promptOption], [data-automation-id=activeListContainer], [role=option]')) continue;  // inside an open option list
    // a group is the boxes sharing a name inside one question (Workday repeats a name in every work-experience entry)
    const scope = e.closest('fieldset, [role=group], [role=radiogroup], [data-automation-id^=formField-], .application-question') || e.getRootNode();
    const sameName = t === 'radio' || t === 'checkbox' ? [...scope.querySelectorAll(`input[type=${t}][name="${CSS.escape(e.name || '')}"]`)] : [];
    if (e.hasAttribute('data-jf')) continue;  // already part of a group
    // one question's checkboxes, each with its own name: a Workday form field, an Ashby field block, a fieldset
    const qBlock = t === 'checkbox' ? e.closest('[data-automation-id^=formField-], [data-field-path], fieldset, .application-question') : null;
    const ffBoxes = qBlock ? [...qBlock.querySelectorAll('input[type=checkbox]')] : [];
    if (t === 'radio' || (t === 'checkbox' && ((e.name && sameName.length > 1) || ffBoxes.length > 1))) {
      // Workday lists one question's checkboxes under one form field, each with its own name
      const boxes = ffBoxes.length > 1 ? ffBoxes : (e.name ? sameName : [e]);
      const opts = boxes.map(b => { let l = ownText(b);
        if (!l && b.id) { const x = b.getRootNode().querySelector(`label[for="${CSS.escape(b.id)}"]`); if (x) l = caption(x); }
        return l || clean(b.getAttribute('aria-label')) || b.value; });
      const q = groupQuestion(e, e.name);
      boxes.forEach((b, k) => b.setAttribute('data-jf-opt', String(k)));
      add(e, {kind: t === 'radio' ? 'radio' : 'checkboxes', label: q, required: boxes.some(b => b.required) || required(e, q),
              options: opts, value: opts.filter((o, k) => boxes[k].checked).join('; ')});
      boxes.forEach(b => b.setAttribute('data-jf', String(n - 1)));
      continue; }
    const role = (e.getAttribute('role') || '').toLowerCase();
    const q = question(e);
    if (e.getAttribute('data-uxi-widget-type') === 'selectinput') {
      // Workday's search picker ("How Did You Hear About Us?", "Country Phone Code"): chosen items are listed apart
      const ms = e.closest('[data-automation-id=multiSelectContainer]') || e.parentElement;
      const sel = [...ms.querySelectorAll('[data-automation-id=selectedItemList] li')].map(x => clean(x.innerText)).filter(Boolean);
      add(e, {kind: 'combobox', label: q, required: required(e, q), value: [...new Set(sel)].join('; ')});
      continue; }
    if (t === 'checkbox' || role === 'checkbox') {
      const on = e.checked === true || e.getAttribute('aria-checked') === 'true';
      add(e, {kind: 'checkbox', label: q, required: required(e, q), value: on ? 'checked' : ''}); continue; }
    if (role === 'radio') continue;  // ARIA radio groups are rare on forms; left to the page model
    if (e.tagName === 'SELECT') {
      const opts = [...e.options].filter(o => !o.disabled && !o.hidden).map(o => clean(o.text)).filter(Boolean);   // a disabled prompt is not a choice
      add(e, {kind: 'select', label: q, required: required(e, q), options: opts, option_values: [...e.options].filter(o=>!o.disabled&&!o.hidden&&clean(o.text)).map(o=>o.value), options_state:'complete', multiple: e.multiple,
              value: e.selectedIndex >= 0 && e.value ? clean(e.options[e.selectedIndex].text) : '',
              // The selected option's own evidence that it is a PROMPT and not an answer: a disabled option is never submitted, a hidden
              // one is not offered, and a `data-placeholder` / placeholder-labelled one says so. A longer, question-specific prompt
              // ("Select your preferred contact method") cannot be recognised by its words, so the control says it here.
              prompt: e.selectedIndex >= 0 && (e.options[e.selectedIndex].disabled || e.options[e.selectedIndex].hidden
                  || e.options[e.selectedIndex].hasAttribute('data-placeholder') || e.options[e.selectedIndex].getAttribute('value') === 'placeholder'
                  || (e.options[e.selectedIndex].parentElement.tagName === 'OPTGROUP' && e.options[e.selectedIndex].parentElement.disabled))}); continue; }
    if (role === 'combobox' || e.getAttribute('aria-haspopup') === 'listbox' || e.getAttribute('aria-autocomplete')) {
      const box = e.closest('[class*=control i]') || (e.parentElement && e.parentElement.closest('[data-automation-id]')) || e.parentElement;
      const widgetValue = (__JOBAGENT_WIDGET_VALUE__)(e);
      const shownValue = widgetValue !== null ? widgetValue : e.tagName === 'BUTTON' ? clean(e.innerText).replace(/^(select all that apply|select( an?)?( answer| option| one)?)\b\s*/i, '') :
          clean((box && box.querySelector('[class*=singleValue i], [class*=single-value i], [class*=multiValue i], [data-automation-id=selectedItem]') || {}).innerText || e.value || '');
      add(e, {kind: 'combobox', label: q, required: required(e, q), value: /^select one$|^select\.\.\.$/i.test(shownValue) ? '' : shownValue}); continue; }
    if (role === 'spinbutton' || role === 'textbox' || e.isContentEditable || e.tagName === 'INPUT' || e.tagName === 'TEXTAREA') {
      const kind = e.tagName === 'TEXTAREA' || e.getAttribute('aria-multiline') === 'true' ? 'textarea' : (role === 'spinbutton' ? 'spin' : (t || 'text'));
      if (/search/i.test(t) || /^search/i.test(q)) continue;
      add(e, {kind, label: q, required: required(e, q), value: e.isContentEditable ? clean(e.innerText) : (e.value || ''), maxlength: e.maxLength > 0 ? e.maxLength : null});
    }
  }
  const buttons = all('button, [role=button], input[type=submit], a[href]')
      .filter(b => vis(b) && shown(b) && !b.disabled && !b.hasAttribute('data-jf'))
      .map(b => clean(b.innerText || b.value || b.getAttribute('aria-label'))).filter(t => t && t.length < 60);
  // "Add" / "Add Another" buttons (Workday work experience, education, languages): the planner may ask for entries
  const adds = [];
  for (const b of all('button, [role=button]')) {
    const tx = clean(b.innerText || b.getAttribute('aria-label')).replace(/[\uE000-\uF8FF]/g,'').trim();
    if (!/^add( another)?\b/i.test(tx) || !vis(b) || !shown(b) || tx.length > 40 || b.disabled ||
        b.getAttribute('aria-disabled')==='true' || (b.tagName==='BUTTON'&&b.form&&b.type!=='button') ||
        (b.tagName==='A'&&b.getAttribute('href')&&!/^#/.test(b.getAttribute('href')))) continue;
    b.setAttribute('data-jf-add', String(adds.length));
    let ownedSection=section(b) || clean(b.getAttribute('aria-label'));
    const group=b.closest('[role=group][aria-labelledby]');
    const title=group && byIds(group.getAttribute('aria-labelledby'),group);
    const entity=title && title.match(/^(Education|Work Experience|Employment History)(?:\s*\([^)]*\))?$/i);
    if(entity)ownedSection=entity[1];
    // A shared Add another toolbar may sit outside the entry header's owner.
    // Associate it with the nearest ancestor containing only one entity group.
    for(let owner=b.parentElement;owner && !owner.matches('form,body,html');owner=owner.parentElement){
      const ids=new Set([...owner.querySelectorAll('[data-jf]')].map(e=>Number(e.getAttribute('data-jf'))));
      const fields=out.filter(f=>ids.has(f.id));
      if(!fields.length)continue;
      const groups=fields.map(f=>(f.section||'').match(/^(Education|Work Experience|Employment History)(?: \d+)?$/i)?.[1]);
      if(groups.every(Boolean) && new Set(groups.map(x=>x.toLowerCase())).size===1)ownedSection=groups[0];
      break;
    }
    let help='';
    for(let owner=b.parentElement;owner&&!owner.matches('form,body,html');owner=owner.parentElement){
      const groups=[...owner.querySelectorAll('h2,h3,h4,[role=heading],[class*=header] > p')]
        .map(h=>clean(h.innerText).match(/^(Education|Work Experience|Employment History)(?: \d+)?$/i)?.[1]?.toLowerCase()).filter(Boolean);
      const unique=new Set(groups);
      if(unique.size>1)break;
      if(unique.size===1&&clean(ownedSection).replace(/ \d+$/,'').toLowerCase()===groups[0]){
        help=caption(owner).slice(0,4000);break;
      }
    }
    adds.push({id: adds.length, text: tx, section: ownedSection, help}); }
  const successfulUploadAlert=e=>{
    if(e.getAttribute('role')!=='alert' || e.getAttribute('aria-invalid')==='true' || e.getAttribute('aria-busy')==='true')return false;
    // Informational upload toasts are not validation errors. Attachment bytes
    // are still checked independently; this grants no upload proof.
    if(/^Your (?:resume|cover letter|document|file) (?:was|has been) uploaded successfully[.!]?$/i.test(clean(e.innerText)))return true;
    const accepted=box=>{
      if(!/^\d+:_attach$/.test(box.id))return false;
      const status=box.querySelector('[id="'+CSS.escape(box.id.replace(/_attach$/,'_attachSuccess'))+'"]');
      return status && vis(status) && shown(status) && status.getAttribute('aria-invalid')!=='true' &&
        status.getAttribute('aria-busy')!=='true' && clean(status.innerText)==='File is uploaded successfully';
    };
    const box=e.closest('.attachmentBtn');
    if(box && accepted(box) && e.id===box.id.replace(/_attach$/,'_attachSuccess'))return true;
    // The same accepted upload also emits a global toast with a filename.
    // Exclude it only when that exact filename has its own accepted widget.
    const toast=clean(e.innerText).match(/^(?:[\uE000-\uF8FF]\s*)?(.+?) File uploaded successfully\.$/);
    return !!toast && all('.attachmentBtn').some(b=>accepted(b) &&
      (b.innerText||'').split(/\r?\n/).map(clean).includes(toast[1]));
  };
  const errors = all('[role=alert], [aria-invalid=true], [class*=error i], [id*=error i], [data-automation-id*=error i]')
      .filter(vis).filter(e=>!successfulUploadAlert(e)).map(e => clean(e.innerText)).filter(t => t && t.length < 300);
  const headings = all('h1, h2, h3, [data-automation-id=pageHeader], [aria-current=step]')
      .filter(vis).map(h => clean(h.innerText)).filter(Boolean).slice(0, 8);
  // Upload controls may mount a native input only after a button is clicked.
  for (const button of all('button,[role=button],a')) {      // legacy portals use links: "Click here to attach your Resume"
    const names=[button.innerText,button.getAttribute('aria-label'),
      ...(button.getAttribute('aria-labelledby')||'').split(/\s+/).map(id=>byIds(id,button))];
    const text=names.map(t=>clean(t).replace(/\s+(?:opens? a dialog|required)\s*$/ig,'').trim()).find(t=>
      /^((click here to )?(choose (a )?file|browse( files)?|upload( (a |your )?(file|resume|cv|cover letter|document|attachment))?|attach( (a |your )?(file|resume|cv|cover letter|document|attachment))?))$/i.test(t));
    if (!text || !vis(button) || !shown(button) || button.hasAttribute('data-jf') ||
        (button.tagName==='BUTTON'&&button.form&&button.type!=='button')) continue;
    const box = button.closest('[data-field-path],[data-field-entry-id]') || button.closest('[class*=upload i],fieldset,.form-group,.form-field') || button.parentElement;
    if (!box || box.querySelector('input[type=file]')) continue;
    const label = box.querySelector('label,legend');
    const caption = (label && clean(label.innerText)) || question(button) || text;
    add(button, {kind:'file',label:caption,required:required(button,caption),value:'',accept:''});
  }
  // a visible custom element with no light children and no open shadow root: its content, if any, is in a closed shadow root
  // (or drawn some other way) and cannot be read here. Reported, never guessed.
  const opaque = [];
  for (const h of all('*')) {
    if (!h.tagName.includes('-') || h.shadowRoot || window.__jobagentClosedRoots?.get(h) || h.children.length || clean(h.textContent) || !vis(h) || !shown(h)) continue;
    const r = h.getBoundingClientRect();
    if (r.width >= 120 && r.height >= 24) opaque.push(h.tagName.toLowerCase());
  }
  return {shadowRoots: roots.length - 1, shadowFields, opaque: [...new Set(opaque)].slice(0, 10), fields: out, next: n, adds, buttons: [...new Set(buttons)].slice(0, 60), errors: [...new Set(errors)].slice(0, 20), headings};
}""".replace('__JOBAGENT_WIDGET_VALUE__', WIDGET_VALUE_JS).replace('__JOBAGENT_CAPTION__', CAPTION_JS)


@dataclass
class Field:
    id: int
    kind: str  # text email tel url number date textarea spin select radio checkbox checkboxes combobox file
    label: str
    required: bool = False
    value: str = ""
    options: list[str] = field(default_factory=list)
    section: str = ""
    frame: int = 0
    multiple: bool = False
    accept: str = ""
    maxlength: int | None = None
    format: str = ""
    help: str = ""
    slot: int = 0   # for uploads: which of several same-named uploads in this frame (DOM order); 0 for the first
    prompt: bool = False   # a select whose selected option is itself a prompt (disabled / hidden / marked placeholder), not an answer
    linked: bool = False   # an upload's native input that a lazy trigger's own click opened (carries that trigger's caption)
    identity: str = ''     # optional stable identity assigned by the experimental reader
    frame_identity: str = ''
    invalid: bool = False
    calendar: bool = False
    locator_ref: str = ''
    options_state: str = 'complete'
    option_values: list[str] = field(default_factory=list)
    source_followup: bool = False  # assigned only by adjacent, same-section sourcing-question discovery

    def brief(self) -> str:
        opts = ""
        if self.options:
            shown = self.options if len(self.options) <= 40 else self.options[:40] + [f"... {len(self.options)} total"]
            opts = f" options={shown}"
        return (f"[{self.id}] {self.kind}{' REQUIRED' if self.required else ''} \"{self.label[:200]}\""
                f"{f' section=\"{self.section}\"' if self.section else ''}{opts}"
                f"{f' current=\"{self.value[:80]}\"' if self.value else ''}"
                f"{f' maxlength={self.maxlength}' if self.maxlength else ''}"
                f"{f' format={self.format}' if self.format else ''}"
                f"{f' help={self.help[:600]!r}' if self.help else ''}")


@dataclass
class Coverage:
    """How much of the page the reader actually inspected (F17). A page whose every discovered field verified is NOT
    thereby fully read: a frame that could not be read, or a region the reader cannot see into, may hold more controls."""
    frames_read: int = 0
    unreadable: list = field(default_factory=list)        # [{"url": ..., "reason": ...}] frames whose controls could not be read
    challenge_frames: list = field(default_factory=list)  # human-check frames, left to the person on purpose
    shadow_roots: int = 0                                 # open and recovered closed author roots read
    shadow_fields: int = 0                                # fields found inside them
    opaque: list = field(default_factory=list)            # custom elements whose content cannot be read (closed shadow roots)
    collapsed: list = field(default_factory=list)         # application sections not yet opened

    @property
    def complete(self) -> bool:
        return not self.unreadable and not self.opaque and not self.collapsed

    def problems(self) -> list[str]:
        out = [f"a frame could not be read ({u['url']}: {u['reason']})" for u in self.unreadable]
        out += [f"a region the reader cannot see into (custom element <{tag}>): any controls inside need a person or an adapter"
                for tag in self.opaque]
        out += [f"a form section remains collapsed: {label}" for label in self.collapsed]
        return out

    def summary(self) -> dict:
        return {"complete": self.complete, "frames_read": self.frames_read, "problems": self.problems(),
                "challenge_frames": self.challenge_frames, "shadow_roots": self.shadow_roots, "shadow_fields": self.shadow_fields,
                "collapsed_sections": self.collapsed}


@dataclass
class PageView:
    fields: list[Field]
    buttons: list[str]
    errors: list[str]
    headings: list[str]
    frames: dict[int, Frame]
    adds: list[dict] = field(default_factory=list)
    coverage: Coverage = field(default_factory=Coverage)


READABLE_FRAME = ("http", "about:srcdoc", "about:blank", "data:", "blob:")


def classify_frames(page: Page) -> tuple[list[Frame], list[Frame]]:
    """(frames to read, human-check frames). Embedded documents without an address of their own (srcdoc, blank, data:) are
    read too; only challenge widgets are left out - the person solves those."""
    read, challenge = [page.main_frame], []
    for f in page.frames:
        if f == page.main_frame:
            continue
        if CAPTCHA_FRAME.search(f.url):
            challenge.append(f)
        elif f.url.startswith(READABLE_FRAME):
            read.append(f)
    return read, challenge


def frames_of(page: Page) -> list[Frame]:
    return classify_frames(page)[0]


def _where(url: str) -> str:
    """A frame's address without its query or fragment (they can carry tokens)."""
    p = urlsplit(url or "")
    return f"{p.scheme}://{p.netloc}{p.path}"[:120] if p.netloc else (url or "")[:40]


# a text box's character counter is not part of its question, and it changes as the box fills
COUNTER = re.compile(r"(?i)\s*(\b(remaining|characters? (left|remaining)|chars? left)\s*:?\s*\d+|\d+\s*/\s*\d{2,}\s*"
                     r"(characters?|chars?)?|\d+\s*(characters?|chars?) (left|remaining))\s*$")


def clean_label(label: str) -> str:
    """The question as the form asks it, without a live character counter ("... Remaining: 500")."""
    return COUNTER.sub("", label or "").strip()


PLACEHOLDER_VALUE = re.compile(r"(?i)^\s*[-\u2013\u2014 ]*(please\s+)?(select|choose|pick)\b[^a-z0-9]*(an?\s+)?(one|option|answer|value|item)?[^a-z0-9]*[-\u2013\u2014 ]*$")


def has_value(f: "Field") -> bool:
    """Does the field hold a real answer? A choice field showing its own prompt ("Choose", "-- Select --", "Please select one") holds
    none: that is the empty state of the control, not a prefilled value, and must never count as an answer or as verified (F-exp)."""
    if not f.value or f.prompt:
        return False          # a prompt the control itself marks (disabled / hidden / placeholder option) is never an answer
    return not (f.kind in ("select", "combobox", "radio", "buttons", "checkboxes") and PLACEHOLDER_VALUE.match(f.value))


def merge_linked_uploads(fields: list) -> list:
    """F28: a native input that a lazy trigger's own click opened and the page kept (outside the widget, hidden) is the SAME upload as
    its trigger, not a second one. Merged only on that explicit association (`linked`) and only when exactly one trigger carries the
    caption; with two same-caption triggers which one it belongs to is not known, so nothing is merged. Distinct widgets stay distinct."""
    triggers: dict[tuple, int] = {}
    for g in fields:
        if g.kind == "file" and not g.linked:
            triggers[(g.frame, g.label)] = triggers.get((g.frame, g.label), 0) + 1
    return [g for g in fields if not (g.kind == "file" and g.linked and triggers.get((g.frame, g.label)) == 1)]


def read_page(page: Page) -> PageView:
    """Every visible field on the page and in its iframes, numbered across frames, plus how much of the page was read."""
    fields, buttons, errors, headings, frames, n, adds = [], [], [], [], {}, 0, []
    cov = Coverage()
    read, challenge = classify_frames(page)
    cov.challenge_frames = [_where(f.url) for f in challenge]
    for k, f in enumerate(read):
        snap, last = None, None
        for attempt in range(2):                   # a frame in the middle of navigation answers on the second try
            try:
                if hasattr(getattr(page, 'context', None), 'new_cdp_session'):
                    from jobagent.tools.shadow import prepare
                    prepare(f)
                f.evaluate("() => { const w = r => { r.querySelectorAll('[data-jf], [data-jf-opt], [data-jf-add]').forEach(e => "
                           "{e.removeAttribute('data-jf'); e.removeAttribute('data-jf-opt'); e.removeAttribute('data-jf-add')}); "
                           "r.querySelectorAll('*').forEach(h => { const s=h.shadowRoot||window.__jobagentClosedRoots?.get(h); if(s)w(s); }); }; w(document); }")
                snap = f.evaluate(FIELDS_JS, n)
                break
            except Exception as exc:
                last = exc
                if f.is_detached():
                    break
                page.wait_for_timeout(300)
        if snap is None:
            if not f.is_detached():                # a frame that is gone is not unread content; one that fails is
                cov.unreadable.append({"url": _where(f.url), "reason": f"{type(last).__name__}: {str(last)[:100]}"})
            continue
        frames[k] = f
        cov.frames_read += 1
        cov.shadow_roots += snap.get("shadowRoots", 0)
        cov.shadow_fields += snap.get("shadowFields", 0)
        cov.opaque += [x for x in snap.get("opaque", []) if x not in cov.opaque]
        from jobagent.tools import disclosures
        try:
            cov.collapsed += [r['label'] for r in disclosures.inventory(f)]
        except Exception as exc:
            cov.unreadable.append({"url": _where(f.url), "reason": f"section discovery failed: {type(exc).__name__}"})
        n = snap["next"]
        for x in snap["fields"]:
            fields.append(Field(id=x["id"], kind=x["kind"], label=clean_label(x["label"]), required=bool(x.get("required")),
                                value=x.get("value") or "", options=x.get("options") or [], section=x.get("section", ""),
                                frame=k, multiple=bool(x.get("multiple")), accept=x.get("accept", ""),
                                maxlength=x.get("maxlength"), format=x.get("format", ""), help=x.get("help", ""),
                                linked=bool(x.get("linked")), prompt=bool(x.get("prompt")),
                                identity=x.get('identity',''),frame_identity=x.get('frame_identity',''),invalid=bool(x.get('invalid')),
                                calendar=bool(x.get('calendar')),locator_ref=x.get('locator_ref',''),
                                options_state=x.get('options_state','partial' if x['kind']=='combobox' else 'complete'),
                                option_values=x.get('option_values',[])))
        adds += [{**a, "frame": k} for a in snap.get("adds", [])]
        buttons += snap["buttons"]
        errors += snap["errors"]
        headings += snap["headings"]
    fields = merge_linked_uploads(fields)
    from jobagent.tools import referrals
    referrals.contextualize(fields)
    # Unique questions without author IDs can survive a React node replacement.
    # Radio groups can have fresh generated IDs on every conditional render;
    # their unique question, section and offered answers are the stable group.
    # Keep locator_ref tied to the actual node; identity is only for planning/audit.
    for g in fields:
        choice_group = g.kind in ('radio','checkboxes') and bool(g.label.strip()) and bool(g.options)
        if g.kind!='file' and (g.identity.startswith('node:') or choice_group):
            signature=(g.frame,g.kind,_norm(g.label),_norm(g.section))
            if sum((x.frame,x.kind,_norm(x.label),_norm(x.section))==signature for x in fields)==1:
                options = (tuple(g.options),) if g.kind in ('radio','checkboxes') else ()
                g.identity='question:'+repr((*signature[1:],*options))
    seen: dict[tuple, int] = {}
    for g in fields:                      # uploads often share a native name ("file"): number them so none is lost
        if g.kind == "file":
            k2 = (g.frame, g.label.rsplit(" / ", 1)[-1])
            g.slot, seen[k2] = seen.get(k2, 0), seen.get(k2, 0) + 1
    return PageView(fields, list(dict.fromkeys(buttons)), list(dict.fromkeys(errors)), list(dict.fromkeys(headings)),
                    frames, adds, cov)


# --- option matching ------------------------------------------------------------------------------------------------

_SYNONYMS = {
    "united states": ["united states of america", "usa", "us", "u.s.", "u.s.a.", "america"],
    "yes": ["y", "true", "i am", "i do", "i will"],
    "no": ["n", "false", "i am not", "i do not", "i don't", "i will not"],
    "male": ["man", "m"],
    # degree levels as portals list them ("Masters", "Master's Degree") and as people write them ("MS", "M.S.")
    "masters": ["master s degree", "master s", "masters degree", "master of science", "ms", "m.s.", "master",
                "master of science (m.s.)"],
    "bachelors": ["bachelor s degree", "bachelor s", "bachelors degree", "bachelor of technology", "b.tech",
                  "bachelor of science", "bs", "b.s.", "bachelor", "bachelor of engineering", "b.e."],
    "female": ["woman", "f"],
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s+.]", " ", s.lower())).strip()


# --- numeric ranges: matched by meaning, never by spelling (F30) -----------------------------------------------------------

_N = r"(\d+(?:\.\d+)?)"
_LOW = r"(?:or less|or fewer|or under|and under|and below|or lower|or below)"
_HIGH = r"(?:or more|or greater|or higher|and above|and over|or above|\+)"


def parse_interval(text: str) -> tuple[float, float] | None:
    """The interval of percentages a phrase means: "30% or less" -> (0, 30), "up to 25%" -> (0, 25), "50% or more" -> (50, 100),
    "10-25%" -> (10, 25), "30%" -> (30, 30). None when the phrase holds no percentage or range."""
    s = (text or "").lower().replace("\u2013", "-").replace("\u2014", "-")
    # comparison symbols first: they must keep their meaning, never be dropped into an exact point (F30). A strict bound excludes
    # its own value, an inclusive one keeps it.
    s = s.replace("\u2264", "<=").replace("\u2265", ">=").replace("=<", "<=").replace("=>", ">=")
    eps = 1e-6
    if m := re.search(rf"<=\s*{_N}", s):
        return 0.0, float(m.group(1))
    if m := re.search(rf"<\s*{_N}", s):
        return 0.0, float(m.group(1)) - eps
    if m := re.search(rf">=\s*{_N}", s):
        return float(m.group(1)), 100.0
    if m := re.search(rf">\s*{_N}", s):
        return float(m.group(1)) + eps, 100.0
    if m := re.search(rf"between\s*{_N}\s*%?\s*(?:and|to|-)\s*{_N}\s*%", s):
        return float(m.group(1)), float(m.group(2))
    if re.search(r"[~\u2248]|\b(approx\w*|about|around|roughly|circa|nearly|almost)\b", s) and re.search(r"\d\s*%", s):
        return None                                   # an approximation is not an interval this code can place: a person decides
    if m := re.search(rf"{_N}\s*%?\s*(?:-|to)\s*{_N}\s*%", s):
        return float(m.group(1)), float(m.group(2))
    if m := re.search(rf"{_N}\s*%?\s*{_LOW}", s) or re.search(rf"(?:up to|less than|no more than|under|at most|maximum of|max)\s*{_N}\s*%", s):
        return 0.0, float(m.group(1))
    if m := re.search(rf"{_N}\s*%?\s*{_HIGH}", s) or re.search(rf"(?:at least|more than|over|minimum of|min)\s*{_N}\s*%", s):
        return float(m.group(1)), 100.0
    if m := re.search(rf"{_N}\s*%", s):
        return float(m.group(1)), float(m.group(1))
    return None


def interval_option(answer: str, options: list[str]) -> tuple[str | None, bool]:
    """(the option whose bracket CONTAINS the answer's interval, the tightest one; whether this was a numeric-range question at
    all). A numeric question never falls back to spelling similarity: "30% or less" is not "50% or more"."""
    want = parse_interval(answer)
    ranges = [(o, parse_interval(o)) for o in options]
    ranges = [(o, r) for o, r in ranges if r is not None]
    if want is None:
        # a percentage the code cannot interpret (an approximation, an odd comparator) against options that ARE ranges: no guess,
        # no spelling similarity - the field is left for the candidate
        return None, bool(ranges) and bool(re.search(r"\d\s*%", answer or ""))
    if not ranges:
        return None, False
    fits = [(r[1] - r[0], o) for o, r in ranges if r[0] <= want[0] and want[1] <= r[1]]
    return (min(fits)[1] if fits else None), True


# --- factual identities: a school, an employer, a person (F29) --------------------------------------------------------------

IDENTITY_Q = re.compile(r"(?i)\b(schools?|universit(?:y|ies)|colleges?|institutions?|institutes?|alma mater|employers?|company name|organi[sz]ations?|"
                        r"referr\w* (name|employee)|employee name|supervisor|reference name)\b")
OTHER_OPTION = re.compile(r"(?i)^\s*(other|not listed|my (school|institution|employer) is not listed|none of the above)\b")


def is_identity(question: str) -> bool:
    """Is this question asking WHICH institution / employer / person? Those are facts: a similarly spelled neighbour is a
    different thing, so they are never matched by spelling similarity."""
    return bool(IDENTITY_Q.search(question or ""))


PERSON_Q = re.compile(r"(?i)\b(referr\w* (name|employee)|employee name|supervisor|reference name|manager name)\b")
_CORP_SUFFIX = re.compile(r"(?i)^[\s.,]*((inc|incorporated|llc|l\.l\.c|ltd|limited|corp|corporation|co|company|gmbh|plc|lp|llp)\b[\s.,]*)+$")
_STOP = {"of", "the", "and", "for", "at", "in"}
_TYPE_WORD = re.compile(r"(?i)^\s*(university|college|institute|school|inc\.?|llc|ltd\.?|corp\.?|company|co\.?)?\s*$")

# Aliases the CANDIDATE has approved: {normalised real name: [other forms the same institution / employer is listed under]}. Filled by
# the application step from the applicant file (an entry's `aliases:` list). Nothing else makes two different names one identity.
APPROVED_ALIASES: dict[str, list[str]] = {}


def set_approved_aliases(mapping: dict[str, list[str]] | None) -> None:
    APPROVED_ALIASES.clear()
    for name, forms_ in (mapping or {}).items():
        APPROVED_ALIASES[_norm(name)] = [x for x in forms_ if x]


def _initialism(name: str) -> str:
    return "".join(w[0] for w in re.findall(r"[A-Za-z]+", name) if w.lower() not in _STOP).upper()


def _justified_extension(short: str, long: str) -> bool:
    """Is `long` the same organisation as `short` by a JUSTIFIED, mechanical transformation? Only (1) a corporate suffix ("Acme
    Robotics" / "Acme Robotics, Inc."), or (2) its OWN initialism in parentheses, optionally followed by the type word ("Arizona
    State University" / "Arizona State University (ASU)", "Vellore Institute of Technology (VIT) University"). Punctuation does not
    make an extension the same organisation: "Acme Robotics - Competitor", "Acme Robotics (Competitor)" and "Acme Robotics,
    Competitor" are different identities. Campus or branch forms are accepted only as approved aliases."""
    s, lg = short.strip(), long.strip()
    if not lg.lower().startswith(s.lower()) or len(lg) == len(s):
        return False
    rest = lg[len(s):]
    if _CORP_SUFFIX.match(rest):
        return True
    m = re.match(r"^\s*\(([^()]+)\)(.*)$", rest)
    if not m:return False
    inside=m.group(1).replace('.', '').strip();initial=_initialism(s)
    return bool(initial and inside.upper().startswith(initial) and
                _TYPE_WORD.fullmatch(inside[len(initial):]) and _TYPE_WORD.fullmatch(m.group(2)))


def identity_option(answer: str, options: list[str], person: bool = False) -> str | None:
    """The option that IS this institution / employer / person: the same name (case and punctuation aside); for an organisation also
    a mechanical variant (`_justified_extension`, in either direction) or a form the candidate has approved as an alias
    (`APPROVED_ALIASES`). Never merely a similar name, a longer one, or one with something after a dash or comma: "Arizona State
    University" is not "Adams State University", "Jane Doe" is not "Jane Doe Jr", "Acme Robotics" is not "Acme Robotics -
    Competitor". A PERSON must match exactly."""
    a = _norm(answer)
    if not a:
        return None
    pool = [o for o in options if o and not OTHER_OPTION.match(o) and not re.match(r"(?i)^\s*(select|choose|--|please select)\b", o)]
    for o in pool:
        if _norm(o) == a:
            return o
    if person:
        return None
    approved = {_norm(x) for x in APPROVED_ALIASES.get(a, [])}
    for o in pool:
        if _norm(o) in approved or _justified_extension(answer, o) or _justified_extension(o, answer):
            return o
    return None



def literal_identity_option(answer,options,person=False):
    """An explicit Other choice is valid; it never aliases a named institution."""
    if OTHER_OPTION.match(answer or ''):
        hits=[o for o in options if _norm(o)==_norm(answer)]
        return hits[0] if len(hits)==1 else None
    return identity_option(answer,options,person=person)


def match_option(answer: str, options: list[str], cutoff: int = 80) -> tuple[str | None, int]:
    """The page option that means `answer` (e.g. "USA" -> "United States of America"); (None, best score) when no
    option is close enough for code to decide."""
    opts = [o for o in options if o and not re.match(r"(?i)^\s*(select|choose|--|please select)\b.*", o)]
    if not answer or not opts:
        return None, 0
    picked, numeric = interval_option(answer, opts)
    if numeric:
        return (picked, 97) if picked else (None, 0)         # a percentage question is answered by meaning or not at all
    a = _norm(answer)
    for o in opts:  # exact (normalized) first
        if _norm(o) == a:
            return o, 100
    lead = a.split(" (")[0].split(" +")[0].strip()  # "united states (+1)" -> "united states"
    for canon, alts in _SYNONYMS.items():  # same meaning written another way
        group = {canon, *alts}
        if a in group or lead in group:
            # an option that IS one of the wordings wins ("United States of America", "United States of America
            # (+1)"); "starts with" only as a last resort, never a territory ("United States Minor Outlying Islands")
            exact = [o for o in opts if _norm(o) in group or _norm(o).split(" (")[0].split(" +")[0].strip() in group]
            if exact:
                return exact[0], 95
            for o in opts:
                n = _norm(o)
                if (n.startswith(canon + " ") or n.startswith(a + ",")) and not re.search(
                        r"minor outlying|virgin islands|outlying|territor", n):
                    return o, 90
    # a yes/no answer against long options ("Yes, I am authorized ...", "No, I will not require ...")
    if a in ("yes", "no"):
        hits = [o for o in opts if re.match(rf"(?i)^\s*{a}\b", o)]
        if len(hits) == 1:
            return hits[0], 95
    best = process.extractOne(a, [_norm(o) for o in opts], scorer=fuzz.token_set_ratio)
    if best and best[1] >= cutoff:
        # token_set_ratio rates "No" against "No, I am not ..." 100 too; prefer the option that starts the same
        top = [o for o in opts if fuzz.token_set_ratio(a, _norm(o)) == best[1]]
        return (top[0] if len(top) == 1 else max(top, key=lambda o: fuzz.ratio(a, _norm(o)))), int(best[1])
    return None, int(best[1]) if best else 0


def already_shows(want: str, got: str, kind: str) -> bool:
    """The page already holds this answer, so the field is left alone: the same value, a near-identical spelling
    or an exact synonym ("United States of America" for "United States") - never just a value containing the
    answer ("United States Minor Outlying Islands")."""
    if not got:
        return False
    if " | " in want:
        return same_value(want, got, kind) and not lower_variant(want, got)
    if kind in ("tel", "date_parts", "checkbox", "checkboxes", "file"):
        return same_value(want, got, kind)
    w, g = _norm(want), _norm(got)
    if w == g or close_option(want, [got]) == got:
        return True
    lead = lambda x: x.split(" (")[0].split(" +")[0].strip()  # noqa: E731
    return any(lead(w) in {c, *alts} and lead(g) in {c, *alts} for c, alts in _SYNONYMS.items())


def lower_variant(want: str, got: str) -> bool:
    """The field holds one of the answer's later wordings ("Other") while an earlier one may be offered: try again."""
    variants = [v.strip() for v in want.split(" | ") if v.strip()]
    if len(variants) < 2:
        return False
    first = variants[0]
    return not (_norm(first) == _norm(got) or close_option(first, [got]) == got)


PHONE_CODE_Q = re.compile(r"(?i)country.*phone|phone.*country|(?:phone|calling|dial(?:l?ing)?)\s*(?:country\s*)?code")


def phone_code_option(answer: str, options: list[str]) -> str | None:
    """Match a dial prefix and/or its supplied country, preserving ambiguity.

    +1 alone cannot choose between multiple countries. A country supplied in
    the same phone record can distinguish United States (+1) from Canada (+1).
    """
    def parts(value):
        value = value.strip()
        if re.fullmatch(r"\+?\d{1,4}", value):
            return "", value.lstrip("+")
        match = re.search(r"\+(\d{1,4})\)?\s*$", value)
        return (value[:match.start()].rstrip(" ("), match[1]) if match else (value, "")
    country, code = parts(answer)
    if not country and not code:
        return None
    hits = []
    for option in options:
        offered_country, offered_code = parts(option)
        if code and code != offered_code:
            continue
        if country and not already_shows(country, offered_country, "select"):
            continue
        hits.append(option)
    return hits[0] if len(hits) == 1 else None


def in_range(option: str, x: float) -> bool:
    """A range option ("3.75+", "3.4 - 3.70", "Below 3.4", "2-5 years") holds x."""
    o = option.lower().replace("–", "-")
    if m := re.search(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)", o):
        return float(m.group(1)) <= x <= float(m.group(2))
    if m := re.search(r"(\d+(?:\.\d+)?)\s*\+|(?:above|over|at least|more than)\s*(\d+(?:\.\d+)?)", o):
        return x >= float(m.group(1) or m.group(2))
    if m := re.search(r"(?:below|under|less than)\s*(\d+(?:\.\d+)?)", o):
        return x < float(m.group(1))
    return False


def selection_parts(value: str, question: str = "") -> list[str]:
    """Render a supplied skills list as choices; commas in other answers stay literal."""
    pattern = r",(?![^()]*\))" if ';' not in value and re.search(r'(?i)\bskills\b',question) else ';'
    return [x.strip() for x in re.split(pattern,value) if x.strip()]


def same_value(want: str, got: str, kind: str, question: str = "", multiple: bool = False) -> bool:
    """The page shows the value that was meant (read-back check)."""
    if kind in ('combobox', 'select') and PHONE_CODE_Q.search(question):
        return phone_code_option(want, [got]) is not None
    if multiple and kind in ('combobox','select'):
        return {_norm(x) for x in selection_parts(want,question)} == {_norm(x) for x in got.split(';') if x.strip()}
    if kind in ('combobox','select','radio','buttons') and is_identity(question):
        return any(_norm(v)==_norm(got) or identity_option(v.strip(),[got],person=bool(PERSON_Q.search(question))) is not None
                   for v in want.split(' | ') if v.strip())
    if " | " in want:
        # one of the candidate's wordings exactly: "Arizona State University" is not "Arizona State University - West"
        g = _norm(got)
        return any(_norm(v) == g or close_option(v, [got]) == got for v in want.split(" | ") if v.strip())
    w, g = _norm(want), _norm(got)
    if kind == "tel" or re.fullmatch(r"[\d\s()+.-]{7,}", want.strip()):
        return re.sub(r"\D", "", want)[-10:] == re.sub(r"\D", "", got)[-10:]
    if kind == "checkbox":
        return (w in ("checked", "yes", "true")) == (g == "checked")
    if kind in ("checkboxes",):
        return {_norm(x) for x in want.split(";") if x.strip()} == {_norm(x) for x in got.split(";") if x.strip()}
    if kind == "file":  # the attached file's name
        return bool(got) and _norm(want.rsplit("/", 1)[-1]) in g
    if w == g:
        return True
    if kind == "date_parts":
        return bool(re.sub(r"\D", "", want)) and re.sub(r"\D", "", want) == re.sub(r"\D", "", got)
    if kind == "combobox" and ";" in want:
        return {_norm(x) for x in want.split(";") if x.strip()} <= {_norm(x) for x in got.split(";")} | {g}
    if " > " in want and _norm(want.rsplit(" > ", 1)[-1]) == g:
        return True
    return kind in ("combobox", "select", "radio", "buttons") and (w in g or fuzz.token_set_ratio(w, g) >= 90)


# --- comboboxes ------------------------------------------------------------------------------------------------------

OPTIONS_JS = r"""() => [...new Set([...(window.__jobagentAll ? window.__jobagentAll('[role=option], [role=menuitem], [data-automation-id=promptOption]') : document.querySelectorAll('[role=option], [role=menuitem], [data-automation-id=promptOption]'))]
  .filter(e => { const r = e.getBoundingClientRect(); return r.width > 1 && r.height > 1
                 && !e.closest('[data-automation-id=selectedItemList], [data-automation-id=selectedItem]'); })
  .map(e => (e.innerText || e.textContent || '').replace(/\s+/g, ' ').trim()).filter(Boolean))]"""

_NO_OPTIONS = re.compile(r"(?i)^(no (options|results|items|matches)( found)?\.?|loading\.*|type to search|start typing.*|"
                         r"select one)$")


SUGGEST_JS = r"""() => [...document.querySelectorAll('.dropdown-results > *, [class*=dropdown-location], .pac-item, ' +
    '[class*=suggestion i] li, [class*=autocomplete i] li, [role=option]')]
  .filter(e => { const r = e.getBoundingClientRect(); return r.width > 1 && r.height > 1; })
  .map(e => (e.innerText || e.textContent || '').replace(/\s+/g, ' ').trim()).filter(Boolean)"""

US_STATES = {"alabama": "al", "alaska": "ak", "arizona": "az", "arkansas": "ar", "california": "ca", "colorado": "co",
             "connecticut": "ct", "delaware": "de", "florida": "fl", "georgia": "ga", "hawaii": "hi", "idaho": "id",
             "illinois": "il", "indiana": "in", "iowa": "ia", "kansas": "ks", "kentucky": "ky", "louisiana": "la",
             "maine": "me", "maryland": "md", "massachusetts": "ma", "michigan": "mi", "minnesota": "mn",
             "mississippi": "ms", "missouri": "mo", "montana": "mt", "nebraska": "ne", "nevada": "nv",
             "new hampshire": "nh", "new jersey": "nj", "new mexico": "nm", "new york": "ny", "north carolina": "nc",
             "north dakota": "nd", "ohio": "oh", "oklahoma": "ok", "oregon": "or", "pennsylvania": "pa",
             "rhode island": "ri", "south carolina": "sc", "south dakota": "sd", "tennessee": "tn", "texas": "tx",
             "utah": "ut", "vermont": "vt", "virginia": "va", "washington": "wa", "west virginia": "wv",
             "wisconsin": "wi", "wyoming": "wy", "district of columbia": "dc"}


def best_place(answer: str, suggestions: list[str]) -> str | None:
    """The suggestion that is this place: same city first, then the same state (name or code), then the US
    ("Tempe, Arizona" -> "Tempe, AZ, USA", not "Tempe, Salerno, ITA")."""
    parts = [x.strip().lower() for x in answer.split(",") if x.strip()]
    if not parts:
        return None
    city = parts[0]
    state = next((x for x in parts[1:] if x in US_STATES or x in US_STATES.values()), "")
    codes = {state, US_STATES.get(state, "")} - {""}
    same_city = [x for x in suggestions if x.lower().split(",")[0].strip() == city]
    for x in same_city:
        bits = {b.strip().lower() for b in x.split(",")[1:]}
        if codes & bits or any(c in bits for c in [US_STATES.get(b, b) for b in bits] if c in codes):
            return x
    us = [x for x in same_city if re.search(r"(?i)\b(usa|us|united states)\b", x)]
    return (us or same_city or [None])[0]


def strict_place(answer: str, suggestions: list[str]) -> str | None:
    """Render one known city/state; never choose a nearest or different place."""
    bits=[_norm(x) for x in answer.split(',') if x.strip()]
    if len(bits)<2:return None
    codes=set(US_STATES.values())
    states={US_STATES.get(x,x) for x in bits[1:] if x in US_STATES or x in codes}
    if len(states)!=1:return None
    hits=[]
    for item in suggestions:
        parts=[_norm(x) for x in item.split(',') if x.strip()]
        if not parts or parts[0]!=bits[0]:continue
        given={US_STATES.get(x,x) for x in parts[1:] if x in US_STATES or x in codes}
        if given!=states:continue
        countries=[x for x in parts[1:] if x not in US_STATES and x not in codes]
        if any(x not in ('us','usa','united states','united states of america') for x in countries):continue
        hits.append(item)
    return hits[0] if len(hits)==1 else None


def fill_calendar(page,view,f,answer):
    """Choose an exact accessible date, then verify the displayed calendar value."""
    try:target=datetime.strptime(answer,'%Y-%m-%d')
    except ValueError:raise FillError('A calendar needs a source-backed YYYY-MM-DD date')
    loc=locate(view,f);frame=view.frames[f.frame]
    loc.click(timeout=8000)
    date_name=re.compile(rf'\b{target:%B}\s+{target.day}(?:st|nd|rd|th)?(?:,\s*|\s+){target.year}\b',re.I)
    for _ in range(36):
        cell=frame.get_by_role('option',name=date_name).filter(visible=True)
        if not cell.count():cell=frame.get_by_role('button',name=date_name).filter(visible=True)
        if not cell.count():cell=shadow_locator(frame,f'[data-date="{answer}"], [datetime="{answer}"]').filter(visible=True)
        if cell.count()==1:
            cell.click(timeout=8000);page.keyboard.press('Escape');loc.blur()
            shown=loc.input_value()
            for fmt in ('%m/%d/%Y','%Y-%m-%d','%B %d, %Y','%b %d, %Y'):
                try:
                    if datetime.strptime(shown,fmt).date()==target.date():return shown
                except ValueError:pass
            raise FillError('The calendar did not retain the selected date')
        popup=shadow_locator(frame,'.react-datepicker, [role=dialog]:has([role=grid])').filter(visible=True)
        if popup.count()!=1:break
        heading=popup.inner_text()
        month=re.search(r'\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})\b',heading)
        if not month:break
        current=datetime.strptime(month[0],'%B %Y')
        direction='Next' if (target.year,target.month)>(current.year,current.month) else 'Previous'
        nav=popup.get_by_role('button',name=re.compile(rf'{direction}.*month',re.I)).filter(visible=True)
        if nav.count()!=1:break
        nav.click(timeout=3000);page.wait_for_timeout(100)
    page.keyboard.press('Escape')
    raise FillError('No unique accessible day for the source-backed date')


def locate(view: PageView, f: Field):
    selector=f'[data-jf-ref={json.dumps(f.locator_ref)}]' if f.locator_ref else f'[data-jf="{f.id}"]'
    loc=field_element(view.frames[f.frame],selector)
    if f.locator_ref and hasattr(loc,'count') and loc.count()!=1:
        raise FillError('The captured control is not exactly one control now; refresh the form')
    return loc


def _scroll_to_control(loc):
    """Prepare the viewport without smooth-scroll stalls across embedded forms.

    This scrolls only; Playwright still enforces visibility, stability and normal
    click/fill actionability. Hidden or moving controls are never forced.
    """
    loc.evaluate("e=>e.scrollIntoView({behavior:'instant',block:'center',inline:'nearest'})")
    loc.scroll_into_view_if_needed(timeout=5000)


def _open_options(frame: Frame, loc, page: Page) -> list[str]:
    _scroll_to_control(loc)
    loc.click(timeout=8000)
    try:
        # Other closed pickers can retain hidden option nodes. Wait for actual
        # visible choices from this opening, not merely any option in the DOM.
        frame.wait_for_function("pattern => {const skip=new RegExp(pattern,'i');return ("+OPTIONS_JS+")().some(t=>!skip.test(t));}",
                                arg=_NO_OPTIONS.pattern.removeprefix('(?i)'),timeout=1500)
    except Exception:pass
    return [o for o in frame.evaluate(OPTIONS_JS) if not _NO_OPTIONS.match(o)]


def discover_options(page: Page, view: PageView, log=print, *, fields=None) -> None:
    """Open each combobox once to read its options (react-select and Workday lists render them only when open).
    A search box with no list until something is typed keeps no options (the answer is typed then)."""
    from jobagent.tools import referrals
    for f in (view.fields if fields is None else fields):
        if f.kind != "combobox" or f.options:
            continue
        try:
            loc = locate(view, f)
            search_picker = loc.get_attribute("data-uxi-widget-type") == "selectinput"
            if search_picker and f.value and not referrals.SOURCE.search(f.label):
                # A selected chip can cover the now-empty search input. Opening
                # it needlessly times out and can clear the choice. These are
                # partial options, not applicant facts: a different sourced
                # value can still search and replace them at fill time.
                # Recruitment-source policy needs the actual offered referral
                # choices before it can accept a company-specific fallback.
                f.options = [x.strip() for x in f.value.split(';') if x.strip()] if f.multiple else [f.value]
                f.options_state = 'partial'
                continue
            f.options = _open_options(view.frames[f.frame], loc, page)
            f.options_state = 'partial'
            if search_picker and not f.value and len(f.options) <= 15:
                f.options = _expand_categories(page, view.frames[f.frame], loc, f.options)
        except Exception as e:
            log(f"forms: options of \"{f.label[:50]}\" unreadable: {type(e).__name__}")
        finally:
            page.keyboard.press("Escape")
            page.wait_for_timeout(30)


def _expand_categories(page: Page, frame: Frame, loc, top: list[str]) -> list[str]:
    """Workday pickers list categories whose entries open on a click: list them as "Category > Entry"."""
    out = []
    for cat in top:
        try:
            page.keyboard.press("Escape")
            _open_options(frame, loc, page)
            shadow_locator(frame, "[role=option], [data-automation-id=promptOption]").filter(
                has_text=re.compile(rf"^\s*{re.escape(cat)}\s*$")).first.click(timeout=5000)
            page.wait_for_timeout(1000)
            chosen = loc.evaluate("e => [...(e.closest('[data-automation-id=multiSelectContainer]') || e.parentElement)"
                                  ".querySelectorAll('[data-automation-id=selectedItemList] li')].length")
            if chosen:  # a plain option: it got selected; undo by leaving the list (Escape) and keep it as is
                out.append(cat)
                loc.evaluate("e => (e.closest('[data-automation-id=multiSelectContainer]') || e.parentElement)"
                             ".querySelectorAll('[data-automation-id=selectedItemList] [data-automation-id=DELETE_charm], "
                             "[data-automation-id=selectedItemList] [aria-label*=delete i]').forEach(b => b.click())")
                continue
            sub = [o for o in frame.evaluate(OPTIONS_JS) if not _NO_OPTIONS.match(o) and o not in top]
            out += [f"{cat} > {o}" for o in sub[:40]] or [cat]
        except Exception:
            out.append(cat)
    page.keyboard.press("Escape")
    return out[:300]


# --- filling -------------------------------------------------------------------------------------------------------

class FillError(RuntimeError):
    pass


class UnavailableOption(FillError):
    """A strict picker exhausted its offered choices without an exact match."""


def _choose(question: str, answer: str, options: list[str], tie_break) -> str:
    if is_identity(question):
        # a fact about WHO: the named institution or nothing; the "Other" choice (whose name the form then asks for) when the list
        # lacks it; never a neighbour that merely looks alike, and never a model's idea of "closest" (F29)
        opt = identity_option(answer, options, person=bool(PERSON_Q.search(question or "")))
        if opt is None:
            opt = next((o for o in options if OTHER_OPTION.match(o or "")), None)
        if opt is None:
            raise FillError(f"{answer!r} is not offered, and the list has no Other choice; options: {options[:8]}: needs the candidate")
        return opt
    opt, score = match_option(answer, options)
    if opt is None and tie_break and options and parse_interval(answer) is None:
        opt = tie_break(question, answer, options)
    if opt is None:
        raise FillError(f"no option matches {answer!r} (best score {score}); options: {options[:12]}")
    return opt


def strict_option(answer: str, options: list[str]) -> str | None:
    """For lists of names (skills): the same name, or the same name with a qualifier ("Machine Learning (ML)",
    "C++ Programming Language"), never a different thing that contains it ("Python IDLE", "SAP R")."""
    a = _norm(answer)
    for o in options:
        n = _norm(o)
        if n == a or n.startswith(a + " (") or o.lower().startswith(answer.lower() + " (") \
                or n == a + " programming language":
            return o
    return None


def close_option(answer: str, options: list[str]) -> str | None:
    """The option that is this answer, written a little differently ("Computer and Informational Sciences" ->
    "Computer and Information Sciences"); not merely a related one ("Computer Engineering")."""
    a = _norm(answer)
    for o in options:  # the same name plus only an abbreviation: "Vellore Institute of Technology (VIT) University"
        if o.lower().startswith(answer.lower()) and re.fullmatch(
                r"\s*\([^)]{1,12}\)\s*(university|college|institute)?\s*", o[len(answer):], re.I):
            return o
    best = max(options, key=lambda o: fuzz.ratio(a, _norm(o)), default=None)
    return best if best is not None and (fuzz.ratio(a, _norm(best)) >= 94 or _norm(best) == a) else None


def _clear_search_picker(page, loc) -> None:
    box=loc.locator("xpath=ancestor::*[@data-automation-id='multiSelectContainer'][1]")
    pills=box.locator('[data-automation-id=selectedItem]')
    count=pills.count()
    if count>64:raise FillError('Too many selected items to clear safely in one operation')
    for _ in range(count):
        before=pills.count()
        if not before:break
        try:
            pills.first.focus();page.keyboard.press('Delete')
        except Exception:pass
        if pills.count()==before:
            box.locator('[data-automation-id=DELETE_charm]').first.click(timeout=3000)
        page.wait_for_timeout(100)
    if pills.count():raise FillError('The old search-picker selection could not be cleared')


def fill_field(page: Page, view: PageView, f: Field, answer: str, files: dict[str, str], tie_break=None,
               strict: bool = False, close: bool = False, before_upload=None, upload_confirmation=None, alternatives=()) -> str:
    """Put `answer` into the field. Returns what was chosen (the option text for choice fields)."""
    from jobagent.tools import challenge
    if challenge.guard(page):
        view = read_page(page)
        matches = [g for g in view.fields if g.label == f.label and g.kind == f.kind]
        if len(matches) != 1:
            raise challenge.Blocked("Form changed after verification; field requires review")
        f = matches[0]
    frame, loc = view.frames[f.frame], locate(view, f)
    if f.kind=='combobox' and f.multiple:answer='; '.join(selection_parts(answer,f.label))
    tagged=loc.get_attribute('data-jf')
    if tagged is not None and tagged.isdigit() and int(tagged)!=f.id:f=replace(f,id=int(tagged))
    if f.kind in TEXT_KINDS and loc.evaluate("e => (e.tagName === 'INPUT' || e.tagName === 'TEXTAREA') && e.readOnly"):
        current = loc.input_value()
        if not f.invalid and current.strip() == answer.strip() and loc.get_attribute('aria-invalid') != 'true':
            return current
        raise FillError('The read-only field does not hold the exact sourced answer or is invalid')
    if f.kind == "combobox" and f.value and not f.invalid:
        same = (phone_code_option(answer, [f.value]) is not None if PHONE_CODE_Q.search(f.label)
                else same_value(answer,f.value,f.kind,f.label,multiple=True) if f.multiple
                else _norm(answer) == _norm(f.value) if is_identity(f.label)
                else already_shows(answer, f.value, f.kind))
        if same:
            return f.value
    if f.kind == "file":
        path = files.get(answer) or answer
        name = path.rsplit("/", 1)[-1]
        # Resolve THIS widget again at action time (F28): a page that re-rendered since it was read has renumbered its controls, and
        # an index from the earlier read could now name another widget's. The widget is identified by its own caption (and slot);
        # if that is not exactly one control now, nothing is attached.
        live = read_page(page)
        same = [g for g in live.fields if g.kind == "file" and g.frame==f.frame and g.label == f.label and g.slot == f.slot]
        if len(same) != 1:
            raise FillError(f"the upload {f.label[:60]!r} is not exactly one control on the page now ({len(same)}); not attaching "
                            f"{name} to a control that might be another widget")
        f, view = same[0], live
        frame, loc = view.frames[f.frame], locate(view, f)
        if before_upload is None:
            from jobagent.filling.uploads import UploadProof
            proof=UploadProof(path,loc);proof.identity=f.identity;proof.frame=f.frame_identity
            proofs=getattr(page,'_jobagent_upload_proofs',None)
            if proofs is None:proofs=[];page._jobagent_upload_proofs=proofs
            proofs.append(proof);proof.prepare();before_upload=proof.bind;upload_confirmation=proof.verify
        if loc.evaluate("e => e.tagName !== 'INPUT'"):
            # Associate the clicked control with its chooser/new input, without assuming a caption or input ID.
            choosers = []
            listener = lambda chooser: choosers.append(chooser)
            shadow_locator(frame, 'input[type=file]').evaluate_all(
                "es=>es.forEach(e=>e.setAttribute('data-jf-upload-existing','1'))")
            shadow_locator(frame, '[role=dialog]').filter(visible=True).evaluate_all(
                "es=>es.forEach(e=>e.setAttribute('data-jf-upload-existing-dialog','1'))")
            page.on('filechooser', listener)
            try:
                loc.click(timeout=8000)
                fresh = shadow_locator(frame, 'input[type=file]:not([data-jf-upload-existing])')
                for _ in range(16):
                    if choosers or fresh.count():
                        break
                    page.wait_for_timeout(500)
                inside = [fresh.nth(i) for i in range(fresh.count())
                          if loc.evaluate("(btn, el) => { const box = btn.closest('fieldset,[data-field-path],[data-field-entry-id],"
                                          "[class*=upload i],.form-group,.form-field') || btn.parentElement; return !!box && box.contains(el); }",
                                          fresh.nth(i).element_handle())]
                if choosers:                      # the file chooser this very click opened: tied to this widget by the click itself
                    loc = choosers[0].element
                elif len(inside) == 1:            # a new input that appeared INSIDE this widget's own boundary
                    loc = inside[0]
                else:
                    # A source picker can be portaled outside the attachment
                    # widget. Only a newly visible, explicit upload-source
                    # dialog with one file input and no candidate fields is
                    # attributable to this click. Other page inputs do not qualify.
                    dialogs=shadow_locator(frame,'[role=dialog][aria-modal=true]:not([data-jf-upload-existing-dialog])').filter(visible=True)
                    if len(inside) or dialogs.count()!=1:
                        raise FillError('Upload button did not expose one identifiable file input inside its own widget')
                    dialog=dialogs.first
                    title=dialog.get_attribute('aria-label') or ''
                    if not re.fullmatch(r'(?i)(?:select|choose) (?:a )?source for (?:your )?file upload',title.strip()) or \
                            dialog.locator('input:not([type=file]):not([type=hidden]),select,textarea,[role=combobox]').count() or \
                            dialog.locator('input[type=file]').count()!=1:
                        raise FillError('Upload source dialog is ambiguous or contains unrelated fields')
                    loc=dialog.locator('input[type=file]')
                    if loc.is_disabled():raise FillError('Upload source input is disabled')
                    loc.evaluate("e=>e.setAttribute('data-jf-upload-portaled','1')")
                loc.evaluate("(e,label)=>e.setAttribute('data-jf-upload-label',label)", f.label)
            finally:
                page.remove_listener('filechooser', listener)
                shadow_locator(frame, '[data-jf-upload-existing]').evaluate_all(
                    "es=>es.forEach(e=>e.removeAttribute('data-jf-upload-existing'))")
                shadow_locator(frame, '[data-jf-upload-existing-dialog]').evaluate_all(
                    "es=>es.forEach(e=>e.removeAttribute('data-jf-upload-existing-dialog'))")
        # Retain the resolved node. A Locator can wait 30 seconds for a replaced
        # input on every poll; an ElementHandle can inspect its disconnected
        # node immediately. Choosers already provide an ElementHandle.
        native=loc.element_handle(timeout=1200) if hasattr(loc,'element_handle') else loc
        if native is None:raise FillError('The resolved file input disappeared before upload')
        if before_upload:before_upload(native)
        # Dispatch once. Poll owned evidence, not a filename elsewhere or a
        # second upload of the same bytes while the first is still processing.
        if getattr(getattr(page, 'context', None), 'jobagent_policy', None):
            import mimetypes
            native.set_input_files({'name':name,'mimeType':mimetypes.guess_type(path)[0] or 'application/octet-stream',
                                   'buffer':Path(path).read_bytes()},timeout=8000)
        else:native.set_input_files(path,timeout=8000)
        for _ in range(60):
            if upload_confirmation:
                if upload_confirmation():return path
            else:
                try:
                    if native.evaluate('e=>e.isConnected') and name in native.evaluate('e=>[...(e.files||[])].map(f=>f.name)'):return path
                except Exception:pass
            page.wait_for_timeout(500)
        raise FillError(f"{name} was attached but the page never showed it")
    _scroll_to_control(loc)
    if f.calendar:return fill_calendar(page,view,f,answer)
    if f.kind == "text" and loc.evaluate("e => /location|city|place|autocomplete/i.test([e.name, e.id, e.className, "
                                         "e.getAttribute('data-qa')].join(' ')) || !!e.getAttribute('aria-autocomplete')"):
        # a place box that keeps only a picked suggestion (Lever's "Current location"): type the city, pick it
        city = answer.split(",")[0].strip()
        loc.fill("")
        loc.type(city, delay=60)
        page.wait_for_timeout(2500)
        pick = best_place(answer, frame.evaluate(SUGGEST_JS))
        if pick:
            shadow_locator(frame, ".dropdown-results > *, [class*=dropdown-location], .pac-item, [class*=suggestion i] li, "
                          "[class*=autocomplete i] li, [role=option]").filter(
                has_text=re.compile(rf"^\s*{re.escape(pick)}\s*$")).first.click(timeout=8000)
            page.wait_for_timeout(800)
            return pick
        loc.fill(answer, timeout=8000)  # no suggestions: a plain text box after all
        return answer
    if f.kind in ("text", "email", "tel", "url", "number", "textarea", "date", "password"):
        loc.fill(answer, timeout=8000)
        loc.blur() if hasattr(loc, "blur") else None
        return answer
    if f.kind == "date_parts":  # Workday's date: click its first box and type the digits; it moves on by itself
        digits = re.sub(r"\D", "", answer)
        for _ in range(2):  # the box sometimes drops the first keystrokes: type again if nothing shows
            loc.locator("[data-automation-id$=-display]").first.click(timeout=8000)
            page.wait_for_timeout(300)
            page.keyboard.type(digits, delay=80)
            page.keyboard.press("Tab")
            page.wait_for_timeout(400)
            shown = re.sub(r"\D", "", "".join(loc.locator("[data-automation-id$=-display]").all_inner_texts()))
            if shown == digits:
                break
        return answer
    if " | " in answer and f.kind in ("combobox", "select", "radio"):
        # variants of one answer in preference order ("AI and Robotics | Robotics | Other"): the first the site has
        # first pass: a variant the form offers as such, in the candidate's order; then the closest option to any
        errors, variants = [], [x.strip() for x in answer.split(" | ") if x.strip()]
        for close_only in (True, False):
            for variant in variants:
                try:
                    return fill_field(page, view, f, variant, files, None if close_only else tie_break,
                                      close=close_only, strict=strict)
                except Exception as e:
                    errors.append(f"{variant}: {str(e)[:60]}")
                    page.keyboard.press("Escape")
        raise FillError("no variant offered: " + "; ".join(errors)[:300])
    if f.kind == "combobox" and f.multiple and ";" in answer and loc.get_attribute("data-uxi-widget-type") == "selectinput":
        # a multi-value search picker (Workday skills): add each value the site knows; skip the ones it doesn't
        added = []
        if f.value:_clear_search_picker(page,loc)
        for item in [x.strip() for x in answer.split(";") if x.strip()]:
            try:
                one = Field(**{**f.__dict__, "options": [], "value": "", "multiple": False})
                added.append(fill_field(page, view, one, item, files, None, strict=True))
            except Exception:
                page.keyboard.press("Escape")
        if not added:
            raise FillError(f"none of {answer[:80]!r} is offered")
        return "; ".join(added)
    if f.kind == "spin":  # Workday's month / year boxes take typed digits
        loc.click(timeout=8000)
        page.keyboard.press("Control+A")
        page.keyboard.type(answer, delay=60)
        return answer
    if f.kind == "select":
        opt = (literal_identity_option(answer,f.options,person=bool(PERSON_Q.search(f.label))) if is_identity(f.label) else strict_option(answer,f.options)) if strict else close_option(answer, f.options) if close and not is_identity(f.label) else _choose(f.label, answer, f.options, tie_break)
        if opt is None:
            raise UnavailableOption(f"{answer!r} isn't offered as such")
        values=[v for label,v in zip(f.options,f.option_values) if label==opt]
        if values and len(set(values))!=1:raise FillError('The offered label identifies several distinct options')
        loc.select_option(**({'value':values[0]} if values else {'label':opt}),timeout=8000)
        return opt
    if f.kind == "checkbox":
        want = _norm(answer) in ("checked", "yes", "true", "agree", "i agree", "accept")
        if loc.evaluate("e => e.tagName==='INPUT' && e.type==='checkbox' && getComputedStyle(e).opacity==='0'"):
            # Bootstrap-style checkboxes remain geometrically visible, while
            # their associated label receives pointer events. Never force a
            # click through an unrelated overlay or toggle a correct value.
            label=loc.evaluate_handle('''e=>{
                const labels=[...(e.labels||[])].filter(l=>{
                    const r=l.getBoundingClientRect(),s=getComputedStyle(l);
                    return r.width&&r.height&&s.display!=='none'&&s.visibility!=='hidden';});
                return labels.length===1?labels[0]:null;
            }''').as_element()
            if label is not None:
                if loc.is_checked()!=want:label.click(timeout=8000)
                return "checked" if want else ""
        if loc.is_visible():
            loc.set_checked(want, timeout=8000)
        else:  # styled box: its label takes the click
            if loc.is_checked() != want:
                shadow_locator(frame, f'label:has([data-jf="{f.id}"])').first.click(timeout=8000)
        return "checked" if want else ""
    if f.kind == "buttons":  # press the button that is the answer (Ashby Yes / No)
        opt = close_option(answer, f.options) if close else _choose(f.label, answer, f.options, tie_break)
        if opt is None:
            raise UnavailableOption(f"{answer!r} isn't offered as such")
        k = f.options.index(opt)
        field_element(frame, f'[data-jf="{f.id}"][data-jf-opt="{k}"]').click(timeout=8000)
        page.wait_for_timeout(50)
        return opt
    if f.kind in ("radio", "checkboxes"):
        wanted = [a.strip() for a in (answer.split(";") if f.kind == "checkboxes" else [answer]) if a.strip()]
        chosen = [close_option(a, f.options) if close else _choose(f.label, a, f.options, tie_break) for a in wanted]
        if None in chosen:
            raise UnavailableOption(f"{answer!r} isn't offered as such")
        for k, o in enumerate(f.options):
            box = field_element(frame, f'[data-jf="{f.id}"][data-jf-opt="{k}"]')
            on = o in chosen
            if box.is_checked() == on or (f.kind == "radio" and not on):
                continue
            try:
                box.set_checked(on, timeout=4000)
            except Exception:  # hidden custom input: click its label
                bid = box.get_attribute("id")
                lab = shadow_locator(frame, f'label[for="{bid}"]').first if bid else shadow_locator(frame, f'label:has([data-jf-opt="{k}"][data-jf="{f.id}"])').first
                lab.click(timeout=8000)
        return "; ".join(chosen)
    if f.kind == "combobox":
        single_select2 = loc.evaluate("e => e.matches('.select2-selection--single[role=combobox]')")
        workday_picker = loc.get_attribute("data-uxi-widget-type") == "selectinput"
        if single_select2 and loc.get_attribute("aria-expanded") == "true":
            page.keyboard.press("Escape")
        if f.value and loc.get_attribute("data-uxi-widget-type") == "selectinput":
            # a single-choice Workday picker that already holds something else (an earlier run's pick): its chip
            # sits over the input, so remove it before choosing
            _clear_search_picker(page,loc)
        opts = _open_options(frame, loc, page)
        if " > " in answer and loc.get_attribute("data-uxi-widget-type") == "selectinput":
            # "Category > meaning": open the category, then the entry that means the rest
            cat, answer = (x.strip() for x in answer.split(" > ", 1))
            first = _choose(f.label, cat, opts, tie_break)
            shadow_locator(frame, "[role=option], [data-automation-id=promptOption]").filter(
                has_text=re.compile(rf"^\s*{re.escape(first)}\s*$")).first.click(timeout=8000)
            page.wait_for_timeout(1200)
            opts = [o for o in frame.evaluate(OPTIONS_JS) if not _NO_OPTIONS.match(o)]
        geographic=bool(re.search(r'(?i)location|\bcity\b|where.*located',f.label)) and not is_identity(f.label)
        pick = (lambda ans, o: (phone_code_option(ans, o), 0)) if PHONE_CODE_Q.search(f.label) else \
            (lambda ans, o: (literal_identity_option(ans, o, person=bool(PERSON_Q.search(f.label))), 0)) if is_identity(f.label) else \
            (lambda ans, o: (strict_option(ans, o) or (strict_place(ans,o) if geographic else None), 0)) if strict else \
            (lambda ans, o: (close_option(ans, o), 0)) if close else match_option
        candidates=list(dict.fromkeys([answer,*[str(a) for a in alternatives if a and not OTHER_OPTION.match(str(a))]]))
        def offered(options):
            return next((hit for candidate in candidates if (hit:=pick(candidate,options)[0]) is not None
                         and (not PHONE_CODE_Q.search(f.label) or phone_code_option(answer, [hit]) is not None)),None)
        opt=offered(opts) if opts else None
        search = loc if loc.evaluate("e => e.tagName") == "INPUT" else None
        if opt is None and single_select2:
            # Select2's searchable single picker has a separate input in an
            # owned popup. Never type into an unrelated open search field.
            owned = (loc.get_attribute('aria-controls') or loc.get_attribute('aria-owns') or '').split()
            searches = frame.locator('input.select2-search__field:visible')
            candidates_search = [s for s in searches.all() if set(
                (s.get_attribute('aria-controls') or s.get_attribute('aria-owns') or '').split()
            ) & set(owned)]
            if len(candidates_search) == 1:
                search = candidates_search[0]
        if opt is None and search is not None:
            # a search box: type the answer (or its first words) and pick from what the site suggests
            for probe in dict.fromkeys([*[a[:60] for a in candidates], " ".join(answer.split()[:3]), answer.split()[0] if answer else ""]):
                if not probe:
                    continue
                previous=frame.evaluate(OPTIONS_JS)
                search.fill("")
                search.type(probe, delay=0)
                if loc.get_attribute("data-uxi-widget-type") == "selectinput":
                    loc.press("Enter")  # Workday searches on Enter
                try:
                    frame.wait_for_function("before => {const choices=("+OPTIONS_JS+")();return choices.length && !choices.every(t=>/^loading/i.test(t)) && JSON.stringify(choices)!==JSON.stringify(before);}",
                                            arg=previous,timeout=1800)
                except Exception:pass
                opts = [o for o in frame.evaluate(OPTIONS_JS) if not _NO_OPTIONS.match(o)]
                if not opts and loc.get_attribute("data-uxi-widget-type") == "selectinput":
                    # Workday picks a search's only result by itself: no list, just the new chip
                    chips = loc.evaluate("e => [...(e.closest('[data-automation-id=multiSelectContainer]') || "
                                         "e.parentElement).querySelectorAll('[data-automation-id=selectedItem]')]"
                                         ".map(x => x.innerText.trim())")
                    if chips and offered(chips):
                        return chips[-1]
                    for _ in chips if not strict else []:  # it picked something else: undo
                        box = loc.locator("xpath=ancestor::*[@data-automation-id='multiSelectContainer'][1]")
                        box.locator("[data-automation-id=selectedItem]").last.focus()
                        page.keyboard.press("Delete")
                        page.wait_for_timeout(500)
                if opts:
                    opt=offered(opts)
                    if opt:
                        break
        if opt is None and strict and is_identity(f.label):
            fallback=next((str(a) for a in alternatives if OTHER_OPTION.match(str(a))),None)
            if fallback:
                page.keyboard.press('Escape')
                return fill_field(page,view,f,fallback,files,strict=True)
        if opt is None and (strict or close):
            page.keyboard.press("Escape")
            raise UnavailableOption(f"{answer!r} isn't offered as such")
        if opt is None:
            try:
                opt = _choose(f.label, answer, opts, tie_break)
            except FillError:
                page.keyboard.press("Escape")
                page.keyboard.press("Escape")
                raise
        option = frame.get_by_role("option", name=opt, exact=True)
        if not option.count():
            option = shadow_locator(frame, "[role=option], [role=menuitem], [data-automation-id=promptOption]").filter(
                has_text=re.compile(rf"^\s*{re.escape(opt)}\s*$"))
        try:
            option.first.click(timeout=8000)
        finally:
            page.wait_for_timeout(1200 if workday_picker else 50)
            if single_select2:
                # Some Select2 integrations retain the menu, or replace the
                # trigger after selection. Close via the widget's keyboard
                # handler without touching a stale trigger or its ARIA state.
                page.keyboard.press("Escape")
        if single_select2:
            if loc.count() == 1:
                loc.blur(timeout=1200)
            return opt
        # Some portals commit validation on blur, after rendering the chosen
        # text. Readback must still require their invalid state to clear.
        if loc.count()==1:
            loc.blur(timeout=1200)
        # Carvana's GPA multi-select keeps its options open after selection and
        # intercepts the next field. Close its own trigger, without clicking elsewhere.
        if (loc.get_attribute("id") or "").endswith("_multiselect") and shadow_locator(frame, '[role="option"]:visible').count():
            loc.click(timeout=5000)
            page.wait_for_timeout(300)
        if loc.get_attribute("data-uxi-widget-type") == "selectinput":
            # a Workday category ("Job Board") opens its own list ("LinkedIn", "Indeed"): pick again from that
            for _ in range(2):
                chosen = loc.evaluate("e => [...(e.closest('[data-automation-id=multiSelectContainer]') || e.parentElement)"
                                      ".querySelectorAll('[data-automation-id=selectedItemList] li')].length")
                sub = [o for o in frame.evaluate(OPTIONS_JS) if not _NO_OPTIONS.match(o)]
                if chosen or not sub:
                    break
                pick = _choose(f.label, answer, sub, tie_break)
                shadow_locator(frame, "[role=option], [data-automation-id=promptOption]").filter(
                    has_text=re.compile(rf"^\s*{re.escape(pick)}\s*$")).first.click(timeout=8000)
                page.wait_for_timeout(1200)
                opt = f"{opt} > {pick}"
            page.keyboard.press("Escape")
        return opt
    raise FillError(f"unknown field kind {f.kind}")


# --- buttons -------------------------------------------------------------------------------------------------------

HUMAN_CHECK = re.compile(r"(?i)\bI'?\s?a?m a human\b|\bI am (a )?human\b|verify (that )?you('re| are) (a )?human|"
                         r"not a robot")


def human_check(page: Page) -> str:
    """A human check on the form ("I'm a human", "I'm not a robot"): the candidate does it in noVNC, never the agent."""
    for f in frames_of(page):
        try:
            text = f.locator("body").inner_text(timeout=5000)
        except Exception:
            continue
        if (m := HUMAN_CHECK.search(text)):
            return m.group(0)
    return ""


SUBMIT_INPUT = "form input[type=submit], form button[type=submit]"


def has_submit_input(page: Page) -> bool:
    """A visible form control can send the application; hidden future buttons and labelled non-final actions cannot."""
    for f in frames_of(page):
        try:
            for control in shadow_locator(f, SUBMIT_INPUT).all()[:64]:
                if not control.is_visible() or control.is_disabled():
                    continue
                label = control.evaluate("e => (e.innerText || e.value || e.getAttribute('aria-label') || '').trim()")
                if not label or SUBMIT.search(label):
                    return True
        except Exception:
            continue
    return False


class ClickOutcomeUnknown(RuntimeError):
    """A click on the control that sends the application was started and did not report success. It may have been
    delivered: the caller must treat the application as possibly sent."""


def click_submit_input(page: Page, approval: SubmitApproval) -> bool:
    """Click the form's visible, unlabelled submit control (step 13, after the candidate's approve)."""
    if not isinstance(approval, SubmitApproval):
        raise TypeError("sending an application needs a SubmitApproval")
    for f in frames_of(page):
        try:
            loc = shadow_locator(f, SUBMIT_INPUT).filter(visible=True)
            found = loc.count()
        except Exception:
            continue                                  # nothing was clicked: look in the next frame
        if found:
            approval.still_valid()                    # last check before the click is dispatched
            try:
                loc.first.click(timeout=10000, no_wait_after=True)
            except Exception as e:                    # the click may have been delivered before this failed: never "no button"
                raise ClickOutcomeUnknown(f"{type(e).__name__}: {str(e)[:200]}") from e
            return True                               # and no other control or frame is tried after a click
    return False


# --- is a file attached NOW? (checked at approval, from the page itself, never from an earlier fill record) -------------------
# Evidence belongs to ONE upload control. It is looked for only inside that control's own boundary: the smallest area
# around it (or, when its input is gone, around its one unambiguous caption) that holds no other upload control, no other
# field and no other upload's caption. A file name anywhere else on the page, in another widget, or in another frame is
# not evidence for it; when the association cannot be established, the blocker stays.

DOC_NAME = re.compile(r"[\w][\w .()\-]{0,80}\.(pdf|docx?|rtf|txt|odt|pages|png|jpe?g)\b", re.I)
BOUNDARY_JS = """({sel, own, others, docRe}) => {
  const norm = s => (s || '').replace(/\\s+/g, ' ').trim().toLowerCase();
  const doc = new RegExp(docRe, 'i');
  const docAll = new RegExp(docRe, 'gi');
  const stop = ['FORM', 'BODY', 'MAIN', 'HTML', 'SECTION', 'ARTICLE'];
  const el = sel ? document.querySelector(sel) : null;
  let start = el;
  if (!start) {   // the input is gone: its caption must be unique on the page (a file name is not a caption)
    const hits = [...document.querySelectorAll('label, legend, span, div, p, h2, h3, h4, dt, li')].filter(e => {
      const t = norm((e.innerText || '').replace(docAll, ' '));      // a file name beside a caption does not stop it being the caption
      return t.length > 2 && t.length < 300 && own.some(c => t.includes(c)) && !others.some(c => c && t.includes(c)); });
    const smallest = hits.filter(h => !hits.some(o => o !== h && h.contains(o)));
    if (smallest.length !== 1) return {n: smallest.length, text: '', files: 0};
    start = smallest[0];
  }
  const crowded = node => others.some(c => c && norm(node.innerText).includes(c))
      || [...node.querySelectorAll('input[type=file], [data-jf]')].some(x => x !== el && !(el && el.contains(x)));
  let a = start;
  for (let k = 0; k < 4 && a.parentElement && !stop.includes(a.parentElement.tagName) && !crowded(a.parentElement); k++) a = a.parentElement;
  return {n: 1, text: a.innerText || '', files: el && el.files ? el.files.length : 0};
}"""


def _captions(label: str) -> list[str]:
    """The words an upload's caption goes by: the parts of the combined label that read like text, lower-cased."""
    parts = [re.sub(r"\s+", " ", re.sub(r"[*✱∗＊⁎]|\(required\)|\b(attach|upload|browse|choose file)\b.*$", "", p,
                   flags=re.I)).strip().lower() for p in label.split(" / ")[:2]]
    return list(dict.fromkeys(p[:40] for p in parts if len(p) > 2))


REVIEW_ATTACHMENT_JS = r"""({captions, docRe}) => {
  const norm=s=>(s||'').toLowerCase().replace(/[^a-z]/g,'');
  const visible=e=>!e.closest('[hidden],[aria-hidden=true],[inert]')&&!!(e.getBoundingClientRect().width&&e.getBoundingClientRect().height)&&getComputedStyle(e).visibility!=='hidden';
  const caption=e=>e.getAttribute('aria-label')||
    (e.getAttribute('aria-labelledby')||'').split(/\s+/).map(id=>document.getElementById(id)?.innerText||'').join(' ');
  const selector='[role=group][aria-labelledby],[role=group][aria-label],fieldset';
  const groups=[...document.querySelectorAll(selector)].filter(e=>visible(e)&&captions.includes(norm(
    e.tagName==='FIELDSET'?e.querySelector(':scope > legend')?.innerText:caption(e))));
  if(groups.length!==1)return {n:groups.length,text:'',files:0};
  const g=groups[0];
  // A read-only section is a new rendering of the named document, not a
  // substitute for an empty live input or a neighbouring upload component.
  if(g.matches('[aria-invalid=true],[aria-busy=true]')||g.querySelector('input,select,textarea,[role=combobox]')||
     [...g.querySelectorAll(selector)].some(e=>visible(e)&&caption(e).trim())||
     [...g.querySelectorAll('[aria-invalid=true],[aria-busy=true],[role=progressbar],progress')].some(visible))
    return {n:1,text:'',files:0};
  const roles=['resume','cv','resumecv','curriculumvitae','coverletter'];
  if([...g.querySelectorAll('h2,h3,h4,h5,label,legend,dt')].some(e=>{
    const q=norm(e.innerText);return roles.includes(q)&&!captions.includes(q);
  }))return {n:1,text:'',files:0};
  const filename=new RegExp('^(?:'+docRe+')$','i');
  const names=[...g.querySelectorAll('[data-automation-id="file-upload-item-name"],[data-file-name],[data-filename],a[download]')]
    .filter(visible).map(e=>(e.innerText||e.getAttribute('data-file-name')||e.getAttribute('data-filename')||'').trim())
    .filter(s=>filename.test(s));
  return {n:1,text:names.join('\n'),files:0};
}"""


def _review_upload_scope(record):
    """Only a typed document section can name its later read-only summary."""
    roles={'resume':['resume','cv','resumecv','curriculumvitae'],'cover_letter':['coverletter']}
    captions=roles.get(str(record.get('answer') or ''),[])
    section=re.sub(r'[^a-z]','',str(record.get('section','')).lower())
    return {'captions':captions,'frame':record.get('frame')} if section in captions else None


def attachment_evidence(page: Page, view: PageView, field: "Field | None", others: list[str], known: list[str] = (),
                        label: str = "", review_scope: dict | None = None) -> str:
    """What shows that THIS upload has a file: its own non-empty `files`, or a file name inside its own boundary (see
    above). `field` is the live control (None when its input is gone: then `label` is its recorded label); `others` are
    the captions of every other upload. A name in `known` only confirms evidence already inside the boundary."""
    if field is None and review_scope:
        frame=review_scope['frame']
        frames=[view.frames[frame]] if frame in view.frames else [] if frame is not None else list(view.frames.values())
        matches=[]
        for current in frames:
            try:r=current.evaluate(REVIEW_ATTACHMENT_JS,{'captions':review_scope['captions'],'docRe':DOC_NAME.pattern})
            except Exception:continue
            if r['n']:matches.append(r)
        if len(matches)==1 and matches[0]['n']==1:
            if evidence:=_evidence_in(matches[0],[]):return evidence
    own = _captions(field.label if field is not None else label)
    if field is None and not own:
        return ""
    arg = {"own": own, "others": [c for c in others if c and c not in own], "docRe": DOC_NAME.pattern}
    if field is not None:
        frame = view.frames.get(field.frame)
        if frame is None:
            return ""
        try:
            r = frame.evaluate(BOUNDARY_JS, {**arg, "sel": f'[data-jf="{field.id}"]'})
        except Exception:
            return ""
        return _evidence_in(r, known)
    found = []
    for f in frames_of(page):
        try:
            r = f.evaluate(BOUNDARY_JS, {**arg, "sel": ""})
        except Exception:
            continue
        if r["n"] > 0:
            found.append(r)
    if len(found) != 1 or found[0]["n"] != 1:    # not on the page, or on it more than once: the association is not established
        return ""
    return _evidence_in(found[0], known)


def _evidence_in(r: dict, known) -> str:
    if r.get("files"):
        return "attached file"
    if m := DOC_NAME.search(r.get("text") or ""):
        return m.group(0)
    return next((k for k in known if k and k in (r.get("text") or "")), "")


def _ident(label: str) -> str:
    """A control's identity as the page names it: the whole label (every caption and the native name), lower-cased, with
    required marks and spacing removed. A position in the DOM is never part of it."""
    return re.sub(r"\s+", " ", re.sub(r"[*\u2731\u2217\uff0a\u204e]|\(required\)", "", label, flags=re.I)).strip().lower()


def upload_inventory_records(page: Page, records: list[dict]) -> list[dict]:
    """One current attachment per proven physical widget, retaining the full review history elsewhere."""
    proofs = getattr(page, '_jobagent_upload_proofs', [])

    def same_widget(a, b):
        try:
            return bool(a and b and a.evaluate('(a,b)=>a.isConnected && b.isConnected && a===b', b))
        except Exception:
            return False

    def widget(record):
        if record.get('kind') != 'file' or not record.get('verified'):
            return None
        frame = (record.get('field_key') or [None])[0]
        matched = [p for p in proofs if getattr(p, 'identity', None) == record.get('identity')
                   and getattr(p, 'frame', None) == frame
                   and getattr(p, 'expected', None) == record.get('held_file_sha256')]
        if not matched:
            return None
        first = getattr(matched[0], 'widget', None)
        return first if all(same_widget(first, getattr(p, 'widget', None)) for p in matched) else None

    def role(label):
        roles = [name for name, pattern in (
            ('resume', r'\b(?:resume|cv|curriculum\s+vitae)\b'),
            ('cover', r'\bcover[\s_-]*letter\b'), ('transcript', r'\btranscript\b'),
        ) if re.search(pattern, label, re.I)]
        return roles[0] if len(roles) == 1 else None

    out, owners = [], []
    for record in records:
        owner = widget(record)
        frame = (record.get('field_key') or [None])[0]
        previous = next((i for i, old_owner, old_frame in owners
                         if frame == old_frame and same_widget(owner, old_owner)
                         and ((_ident(record['label']) and _ident(record['label']) == _ident(out[i]['label']))
                              or (role(record['label']) and role(record['label']) == role(out[i]['label'])))), None)
        if previous is not None:
            out[previous] = {**record, 'required': bool(record.get('required') or out[previous].get('required'))}
        else:
            if owner is not None:
                owners.append((len(out), owner, frame))
            out.append(record)
    return out


def _uploads(view: PageView, records: list[dict]) -> list[dict]:
    """Every upload of the page: each live control, and each fill record that is provably none of them (see below)."""
    ups: list[dict] = []
    live: dict[tuple, list[dict]] = {}
    for g in view.fields:
        if g.kind == "file":
            u = {"field": g, "label": g.label, "required": g.required}
            ups.append(u)
            live.setdefault((g.frame, _ident(g.label)), []).append(u)
    recs: dict[tuple, list[dict]] = {}
    for e in records:
        if e.get("kind") == "file":
            recs.setdefault((e.get("frame"), _ident(e["label"])), []).append(e)
    for (frame, ident), rs in recs.items():
        if frame is None:                        # a record without a position: look in every frame, same rule
            cands = [u for (fr, i), us in live.items() if i == ident for u in us]
            rs_all = [e for (fr2, i2), es in recs.items() if i2 == ident and fr2 is None for e in es]
            joinable = len(cands) == 1 and len(rs_all) == 1
        else:
            cands = live.get((frame, ident), [])
            joinable = bool(cands) and len(cands) == len(rs)
        if joinable:
            order = sorted(rs, key=lambda e: e.get("slot", 0))
            for u, e in zip(cands, order):       # identical twins only: equal counts, so every record has a partner
                u["required"] = u["required"] or bool(e.get("required"))
                u.setdefault("filled", e.get("filled", ""))
            continue
        for e in rs:                             # not provably any live control: stays an upload of its own
            ups.append({"field": None, "label": e["label"], "required": bool(e.get("required")),
                        "filled": e.get("filled", ""), "review_scope": _review_upload_scope(e)})
    return ups


def upload_blockers(page: Page, view: PageView, records: list[dict], known: list[str] = ()) -> list[str]:
    """Required uploads with no file attached right now, each judged on its own evidence. An earlier record that says
    verified or failed is not trusted either way: a manual fix clears the blocker, a removed file raises it.

    Every live upload is its own entry. A fill record is the same control as a live one only when frame and full label
    match and that match is unambiguous (one record, one control; or equally many of each with identical labels).
    Position (`slot`) is never proof: it only orders identical twins whose counts agree. A record that does not join
    stays an upload of its own, with its own required flag, and is judged by the evidence at its one unambiguous
    caption; so a different control that now occupies its old position can neither absorb nor clear it. When a record
    joins, the stricter flag wins (required if either says required)."""
    records = upload_inventory_records(page, records)
    known = [k for k in known if k]
    ups = _uploads(view, records)
    caps = [_captions(u["label"]) for u in ups]
    out = []
    for i, u in enumerate(ups):
        if not u["required"]:
            continue
        others = [c for j, cs in enumerate(caps) if j != i for c in cs]
        names = known + ([Path(u["filled"]).name] if u.get("filled") else [])
        if attachment_evidence(page, view, u["field"], others, names, u["label"],u.get('review_scope')):
            continue
        what = (caps[i] or ["an upload"])[0]
        out.append(f"{what}: required upload with no file attached" if u["field"] is not None
                   else f"{what}: required upload, no attachment shown")
    return out


def _visible_buttons(view: PageView, text: str):
    """Visible buttons / links named `text`, in every frame (innerText follows CSS text-transform: "APPLY", the
    accessible name doesn't: "Apply")."""
    name = re.compile(rf"^\s*{re.escape(text)}\s*$", re.I)
    for frame in view.frames.values():
        try:
            if getattr(frame, 'is_detached', lambda: False)():
                continue
            btn = frame.get_by_role("button", name=name)
            if not btn.count():
                btn = frame.get_by_role("link", name=name)
            if not btn.count() and getattr(frame.page.context, 'jobagent_shadow_selectors', False):
                btn = shadow_locator(frame, 'button, [role=button], a, [role=link]').filter(has_text=name)
            btn = btn.filter(visible=True)
            if btn.count():
                yield btn.first
        except PlaywrightError:
            if not getattr(frame, 'is_detached', lambda: False)():
                raise
            # Navigation can remove an iframe after the snapshot. The next
            # startup iteration reads the new document; never use its old UI.


def _press(page: Page, btn) -> None:
    if getattr(page.context, 'jobagent_policy', None):
        # Keep published application links in the dedicated tab, including embedded ATS links.
        btn.evaluate("e=>{if(e.tagName==='A'&&e.target==='_blank')e.target='_self'}")
    # don't wait for the navigation here (Lever's apply page can take longer than the click timeout);
    # the next read of the page waits for it
    btn.click(timeout=10000, no_wait_after=True)


def click_button(page: Page, view: PageView, pattern: re.Pattern) -> str | None:
    """Click the first visible button whose text matches `pattern`; never one that sends the application (`SUBMIT`)."""
    from jobagent.tools import challenge
    if challenge.guard(page):
        view = read_page(page)
    for text in view.buttons:
        if not pattern.search(text) or SUBMIT.search(text):
            continue
        if pattern is NEXT and re.search(r'\bsubmit\b', text, re.I):
            continue
        for btn in _visible_buttons(view, text):
            if pattern is NEXT and (btn.get_attribute('role') == 'tab' or
                                    btn.get_attribute('aria-current') in ('step', 'page', 'true')):
                continue
            _press(page, btn)
            return text
    return None


# What a startup control can do. Its element type does not tell: a link, a type=button control or a button outside the
# form can all submit through JavaScript (`form.requestSubmit()`), and a field can belong to a form without being inside
# it (`form="id"`). So: (1) a control that belongs to a form with any visible fillable control (enumerated through
# `form.elements`, which includes fields attached from outside) is that form's own control, whatever its type; (2) a real
# link to another page is navigation, and is followed by loading its address, so no click handler runs.
SENDS_FORM_JS = """e => {
  const shown = x => { const r = x.getBoundingClientRect(); const s = getComputedStyle(x);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const form = e.form || e.closest('form');
  const fillable = form ? [...form.elements].filter(c => /^(INPUT|SELECT|TEXTAREA)$/.test(c.tagName)
      && !['hidden', 'submit', 'button', 'image', 'reset'].includes(c.type) && shown(c)).length : 0;
  let href = '';
  // A button's nonstandard href can be an inert fallback for a scripted menu,
  // rather than the actual application entry route. Only native links count.
  if (e.tagName === 'A' && e.getAttribute('href')) {
    try {
      const to = new URL(e.getAttribute('href'), location.href), here = new URL(location.href);
      if (/^https?:$/.test(to.protocol) && to.origin + to.pathname + to.search !== here.origin + here.pathname + here.search)
        href = to.href;
    } catch (err) { href = ''; }
  }
  return {sends: !!(form && fillable > 0), fillable, href};
}"""


def _successfactors_listing_entry(page: Page, view: PageView, button, expected_url: str | None = None) -> bool:
    """Recognise the CMS's listing-only entry button, without relaxing short-form protection.

    SuccessFactors places its language picker and listing buttons inside a carrier form.
    No candidate controls may exist, and the declared requisition must match the listing URL.
    The caller has already refused a button whose native form contains fillable controls.
    """
    coverage = getattr(view, "coverage", None)
    if not getattr(coverage, "complete", False) or not view.fields or any(
            f.label.strip().lower() not in {"language", "select language"} for f in view.fields):
        return False
    return bool(button.evaluate(r"""(e, expected) => {
      const u = new URL(location.href), seed = new URL(expected || location.href, location.href);
      const form = e.closest('form'), code = (e.getAttribute('onclick') || '').trim();
      if (form?.id !== 'careerform' || u.origin + u.pathname !== seed.origin + seed.pathname) return false;
      const hidden = name => form.querySelector(`input[type=hidden][name="${name}"]`)?.value;
      const ns = u.searchParams.get('career_ns') || hidden('career_ns');
      const current = u.searchParams.get('career_job_req_id') || hidden('career_job_req_id');
      const wanted = seed.searchParams.get('career_job_req_id');
      if (ns !== 'job_listing' || seed.searchParams.get('career_ns') !== 'job_listing' || !wanted || current !== wanted) return false;
      // One recognised call with literal arguments only; no arbitrary handler statements or expressions.
      if (!/^checkDpcs2AndProceed\(\{[\s\w,:'"-]+\}\);\s*return false;?$/.test(code)) return false;
      const req = code.match(/\bjobReqId\s*:\s*(\d+)\b/);
      return !!req && req[1] === wanted &&
        /\bevtSrc\s*:\s*['"]Apply['"]/.test(code);
    }""", expected_url))


def _workday_listing_entry(view: PageView, button) -> bool:
    """The published Workday Apply link opens a client-side chooser.

    Loading its parent /apply address skips that chooser and can leave an empty
    router outlet. Only the observed listing component on a complete, fieldless
    Workday job page may run this event; ordinary links still load by address.
    """
    if view.fields or not view.coverage.complete:
        return False
    return bool(button.evaluate(r"""e => {
      const here = new URL(location.href), to = new URL(e.href || location.href);
      if (window !== window.top || !here.hostname.endsWith('.myworkdayjobs.com') ||
          e.tagName !== 'A' || e.getAttribute('data-automation-id') !== 'adventureButton' ||
          e.form || e.closest('form') || !here.pathname.includes('/job/') ||
          /\/apply(?:\/|$)/.test(here.pathname) || to.origin !== here.origin ||
          to.pathname !== here.pathname.replace(/\/$/, '') + '/apply' || to.search || to.hash) return false;
      const header = document.querySelector('[data-automation-id="jobPostingHeader"]');
      const description = document.querySelector('[data-automation-id="jobPostingDescription"]');
      return !!header?.textContent.trim() && !!description?.textContent.trim() &&
        header.getBoundingClientRect().width > 0;
    }"""))


def click_open_application(page: Page, view: PageView, pattern: re.Pattern, *, expected_url: str | None = None,
                           navigation_only: bool = False) -> str | None:
    """Startup: open the application (Apply, Apply Manually, Continue application). Navigation only, and only what can be
    established as navigation:
    - a control that belongs to a form with fields to fill is that form's own control: never acted on here;
    - a real link to another page is followed by loading its address (no click handler runs), except the recognised
      fieldless Workday listing component that opens the client-side application chooser;
    - any other control is clicked only while the page has no application fields at all (nothing for it to send);
      with fields on the page it is left to the planner, the filler and the reviewed submission path (step 13).

    navigation_only follows published addresses without invoking any event handler.
    It can leave a listing whose unrelated search filters remain collapsed.
    """
    from jobagent.tools import challenge
    if challenge.guard(page):
        view = read_page(page)
    fields_on_page = bool(view.fields) or not view.coverage.complete
    for text in view.buttons:
        if not pattern.search(text):
            continue
        for btn in _visible_buttons(view, text):
            try:
                info = btn.evaluate(SENDS_FORM_JS)
            except Exception:
                continue                       # can't tell what it does: not an action startup may take
            if info["sends"]:
                continue
            if info["href"]:
                if _workday_listing_entry(view, btn):
                    if navigation_only:
                        continue
                    _press(page, btn)
                    return text
                try:
                    page.goto(info["href"], wait_until="domcontentloaded")
                except Exception:
                    continue
                return text
            if navigation_only:
                continue
            if fields_on_page and not _successfactors_listing_entry(page, view, btn, expected_url):
                continue
            _press(page, btn)
            return text
    return None


# The one object that lets code click the button that sends an application. Only the reviewed submission path
# (steps/submit.make_at_review, after the candidate answered 'approve' to that application's review prompt) makes one.
_ISSUER = object()


class SubmitApproval:
    """The candidate's 'approve' for one application. `click_final_submit` and `click_submit_input` need it."""

    def __init__(self, job_dir: str, request_id: str, _issuer=None, payload_sha: str = "", verify=None):
        if _issuer is not _ISSUER:
            raise TypeError("a SubmitApproval is issued by the reviewed submission path only")
        self.job_dir, self.request_id = str(job_dir), request_id
        self.payload_sha, self._verify = payload_sha, verify

    def still_valid(self) -> None:
        """Called by the sending helpers immediately before the click is dispatched, after any challenge wait or other
        slow step: raises ApprovalInvalidated if the page or an attachment is no longer what the candidate approved."""
        if self._verify is not None:
            self._verify()


class ApprovalInvalidated(RuntimeError):
    """The page changed between the candidate's approval and the click. Nothing was dispatched: a fresh review is needed."""


def click_final_submit(page: Page, view: PageView, pattern: re.Pattern, approval: SubmitApproval) -> str | None:
    """Click the button that sends the application. Needs the candidate's approval object."""
    if not isinstance(approval, SubmitApproval):
        raise TypeError("sending an application needs a SubmitApproval")
    from jobagent.tools import challenge
    if challenge.guard(page):
        view = read_page(page)
    for text in view.buttons:
        if not pattern.search(text):
            continue
        for btn in _visible_buttons(view, text):
            approval.still_valid()                    # last check, after the challenge guard and the search: before the click
            _press(page, btn)
            return text
    return None


def widget_uploads(page: Page, view: PageView, records: list[dict]) -> list[dict]:
    """The uploads that have no live file input any more (a widget that replaced its input with a file name, or a chooser
    with no native input): each with its label and the file name seen inside its own boundary ('' when none is shown).
    These can never be hashed, so the caller records them as unverified, but they must not vanish from the inventory."""
    records = upload_inventory_records(page, records)
    ups = _uploads(view, records)
    caps = [_captions(u["label"]) for u in ups]
    out = []
    for i, u in enumerate(ups):
        if u["field"] is not None:
            continue
        others = [c for j, cs in enumerate(caps) if j != i for c in cs]
        out.append({"label": u["label"], "observed": attachment_evidence(page, view, None, others, [], u["label"],u.get('review_scope'))})
    return out
