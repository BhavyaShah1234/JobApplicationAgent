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
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import Frame, Page
from rapidfuzz import fuzz, process
from jobagent.tools.shadow import locator as shadow_locator, field_element

CAPTCHA_FRAME = re.compile(r"(?i)hcaptcha|recaptcha|google\.com/recaptcha|challenges\.cloudflare|arkoselabs|funcaptcha")

# Buttons that send the application: never clicked by the agent (the user submits after review, step 13)
SUBMIT = re.compile(r"(?i)^\s*(submit|send)\b|submit (my |your )?application|^\s*finish\b|complete (my |your )?"
                    r"application|^\s*apply\s*$")
TEXT_KINDS = ("date_parts", "text", "email", "tel", "url", "number", "date", "textarea", "spin", "password")
NEXT = re.compile(r"(?i)^\s*(next|continue|save (and|&) continue|proceed|review( (my |your )?application)?)\b")

FIELDS_JS = r"""(start) => {
  const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 1 && r.height > 1 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const shown = e => { for (let a = e; a && a !== document.body; a = a.parentElement) {
      const s = getComputedStyle(a); if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false; }
    return true; };
  const clean = t => (t || '').replace(/\s+/g, ' ').trim();
  // F17: author roots, including closed roots recovered through the DOM protocol, share the same reader.
  const roots = [];
  const walk = r => { roots.push(r); for (const h of r.querySelectorAll('*')) {
    const s=h.shadowRoot || window.__jobagentClosedRoots?.get(h); if(s)walk(s); } };
  walk(document);
  const all = sel => roots.flatMap(r => [...r.querySelectorAll(sel)]);
  const anchor = e => { let x = e; while (x.getRootNode() instanceof ShadowRoot) x = x.getRootNode().host; return x; };
  const byIds = (ids, ctx) => clean((ids || '').split(/\s+/).map(i => (ctx ? ctx.getRootNode() : document).getElementById(i))
      .filter(Boolean).map(x => x.innerText || x.textContent).join(' '));
  const ownText = e => { const l = e.closest('label'); return l ? clean(l.innerText) : ''; };
  // a widget's own caption, never the question (Carvana's "SELECT ANSWER" / "SELECT ALL THAT APPLY" boxes)
  const GENERIC = /^(select( an?)?( answer| option| one| all that apply)?|choose( one| an option)?|please (select|choose)( one)?|-+ ?select ?-+|-+)$/i;
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
    if (fl && clean(fl.innerText)) return clean(fl.innerText).slice(0, 300);
    let t = byIds(e.getAttribute('aria-labelledby'), e);
    if (!t && e.id) { const l = e.getRootNode().querySelector(`label[for="${CSS.escape(e.id)}"]`); if (l) t = clean(l.innerText); }
    if (!t) t = clean(e.getAttribute('aria-label'));
    if (!t) { const l = e.closest('label'); if (l) t = clean(l.innerText); }
    if (!t) {
      // Ashby and similar: the question is the first label of the field's block, the input itself has none
      const fc = e.closest('[data-field-path], [data-field-entry-id]');
      const l = fc && [...fc.querySelectorAll('label, legend')].find(x => !x.contains(e) && clean(x.innerText));
      if (l) t = clean(l.innerText); }
    if (!t) { const fs = e.closest('fieldset'); const lg = fs && fs.querySelector('legend'); if (lg) t = clean(lg.innerText); }
    if (!t || GENERIC.test(t)) t = nearLabel(e) || (GENERIC.test(t) ? '' : t);
    if (!t) { const p = clean(e.placeholder || e.getAttribute('title') || ''); if (!GENERIC.test(p)) t = p; }
    if (!t) t = around(e, ownOf(e)).replace(/^(select all that apply|select( an?)?( answer| option| one)?)\b\s*/i, '');
    return t.slice(0, 300); };
  const ownOf = e => e.tagName === 'SELECT' ? [...e.options].map(o => clean(o.text)) : [clean(e.value)];
  const around = (e, own) => {
    // the nearest container whose text, without the control's own options / value, says something: the question
    // (Lever and many custom forms put it in a sibling div, not a label)
    let a = e.parentElement;
    for (let k = 0; a && k < 6; k++, a = a.parentElement) {
      let x = clean(a.innerText);
      for (const o of own) if (o) x = x.split(o).join(' ');
      x = clean(x);
      if (x.length > 1 && x.length < 400) return x;
      if (x.length >= 400) break; }
    return ''; };
  const groupQuestion = (e, name) => {
    // radio / checkbox groups: the group's legend or aria label, else the container text before the options
    if (e.closest('[data-automation-id^=formField-]')) return question(e);  // Workday: the form field's label
    const blk = e.closest('[data-field-path], fieldset');
    if (blk) {  // the block's own question: a label that isn't one of the boxes' labels
      const ids = new Set([...blk.querySelectorAll('input')].map(b => b.id).filter(Boolean));
      const q = [...blk.querySelectorAll('label, legend')].find(l => !ids.has(l.getAttribute('for')) && !l.querySelector('input') && clean(l.innerText));
      if (q) return clean(q.innerText).slice(0, 300); }
    const g = e.closest('fieldset, [role=radiogroup], [role=group]');
    if (g) { const lg = g.querySelector('legend');
      const t = (lg && clean(lg.innerText)) || byIds(g.getAttribute('aria-labelledby'), g) || clean(g.getAttribute('aria-label'));
      if (t) return t.slice(0, 300); }
    const labels = [...e.getRootNode().querySelectorAll(`input[name="${CSS.escape(name)}"]`)].map(b => {
      const l = b.closest('label') || (b.id && b.getRootNode().querySelector(`label[for="${CSS.escape(b.id)}"]`));
      return l ? clean(l.innerText) : clean(b.value); });
    return (around(e, labels) || question(e)).slice(0, 300); };
  const section = e => {
    // the nearest heading above the field (e.g. "Work Experience 2", "Voluntary Self-Identification")
    // headings only: a legend is a field's own label (Workday's "To*" date), not a section
    const hs = all('h1, h2, h3, h4, h5, [role=heading]').filter(vis);
    let best = '';
    const ae = anchor(e);
    for (const h of hs) { if (anchor(h).compareDocumentPosition(ae) & Node.DOCUMENT_POSITION_FOLLOWING) best = clean(h.innerText); }
    return best.slice(0, 80); };
  // a required marker is "*" or a lookalike (Lever uses U+2731), at the end of the caption or followed by a space
  const required = (e, q) => e.required || e.getAttribute('aria-required') === 'true'
      || /[*✱∗＊⁎]\s*$|[*✱∗＊⁎]\s|\(required\)/i.test(q);
  const out = []; let n = start; const seen = new Set(); let shadowFields = 0;
  const add = (e, f) => {
    // Legacy SuccessFactors uses a region/div masthead rather than a header tag.
    // Its Language picker is navigation, not a candidate question.
    if (e.closest('#headerNavBar, #rcmCareerLangDropDown')) return;
    e.setAttribute('data-jf', String(n));
    if (e.getRootNode() instanceof ShadowRoot) shadowFields++;
    f.id = n++;
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
    const q = (l ? clean(l.innerText) : around(btns[0], btns.map(b => clean(b.innerText)))).slice(0, 300);
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
    const opts = radios.map(e => clean(e.getAttribute('aria-label') || e.innerText));
    add(g, {kind:'radio',label:q,required:required(g,q),options:opts,
      value:opts.filter((o,k) => radios[k].getAttribute('aria-checked') === 'true').join('; ')});
    radios.forEach((e,k) => {e.setAttribute('data-jf',String(n-1));e.setAttribute('data-jf-opt',String(k));});
  }
  const ctl = 'input, select, textarea, [contenteditable=true], [role=textbox], [role=combobox], button[aria-haspopup=listbox], [role=checkbox], [role=radio], [role=spinbutton]';
  for (const e of all(ctl)) {
    const t = (e.getAttribute('type') || '').toLowerCase();
    if (t === 'hidden' || t === 'submit' || t === 'image' || t === 'reset' || e.disabled) continue;
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
      add(dw, {kind: 'date_parts', label: q + ' (' + fmt + ')', required: required(dw.querySelector('input') || dw, q),
               value: /[A-Z]{2}/.test(val) ? '' : val, format: fmt});
      continue; }
    if (t === 'file') {
      // file inputs are often hidden behind an "Attach" button: count them when their container shows
      const box = e.closest('div, section, fieldset') || e.parentElement;
      if (!box || !vis(box) || !shown(box)) continue;
      // the upload's own caption ("Resume/CV*", "Cover Letter"): the nearest container text that isn't just buttons
      let cap = '';
      for (let a = e.parentElement, k = 0; a && k < 5 && !cap; a = a.parentElement, k++) {
        const x = clean(a.innerText).replace(/\b(attach|dropbox|google drive|enter manually|upload|or drag and drop( here)?|browse)\b/gi, '').replace(/[,|]/g, ' ').trim();
        if (x.length > 2) cap = clean(x).slice(0, 120); }
      const q = e.getAttribute('data-jf-upload-label') || [question(e), cap, e.id || e.name].filter(t => t && !/^attach$/i.test(t)).join(' / ');
      add(e, {kind: 'file', label: q, required: required(e, q),   // the question's own marker counts, not only the caption's
              // only files really attached: the box's caption ("ATTACH RESUME/CV") is not a value
              value: e.files && e.files.length ? [...e.files].map(f => f.name).join(', ') : '',
              accept: e.getAttribute('accept') || ''});
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
    if (e.closest('[data-jf-skip], header, nav, [role=banner], [data-automation-id=navigationContainer]')) continue;
    if (e.closest('[data-automation-id=promptOption], [data-automation-id=activeListContainer], [role=option]')) continue;  // inside an open option list
    // a group is the boxes sharing a name inside one question (Workday repeats a name in every work-experience entry)
    const scope = e.closest('fieldset, [role=group], [role=radiogroup], [data-automation-id^=formField-]') || e.getRootNode();
    const sameName = t === 'radio' || t === 'checkbox' ? [...scope.querySelectorAll(`input[type=${t}][name="${CSS.escape(e.name || '')}"]`)] : [];
    if (e.hasAttribute('data-jf')) continue;  // already part of a group
    // one question's checkboxes, each with its own name: a Workday form field, an Ashby field block, a fieldset
    const qBlock = t === 'checkbox' ? e.closest('[data-automation-id^=formField-], [data-field-path], fieldset') : null;
    const ffBoxes = qBlock ? [...qBlock.querySelectorAll('input[type=checkbox]')] : [];
    if (t === 'radio' || (t === 'checkbox' && ((e.name && sameName.length > 1) || ffBoxes.length > 1))) {
      // Workday lists one question's checkboxes under one form field, each with its own name
      const boxes = ffBoxes.length > 1 ? ffBoxes : (e.name ? sameName : [e]);
      const opts = boxes.map(b => { let l = ownText(b);
        if (!l && b.id) { const x = b.getRootNode().querySelector(`label[for="${CSS.escape(b.id)}"]`); if (x) l = clean(x.innerText); }
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
      const opts = [...e.options].map(o => clean(o.text)).filter(Boolean);
      add(e, {kind: 'select', label: q, required: required(e, q), options: opts.slice(0, 300), multiple: e.multiple,
              value: e.selectedIndex >= 0 && e.value ? clean(e.options[e.selectedIndex].text) : ''}); continue; }
    if (role === 'combobox' || e.getAttribute('aria-haspopup') === 'listbox' || e.getAttribute('aria-autocomplete')) {
      const box = e.closest('[class*=control i]') || (e.parentElement && e.parentElement.closest('[data-automation-id]')) || e.parentElement;
      const shownValue = e.tagName === 'BUTTON' ? clean(e.innerText).replace(/^(select all that apply|select( an?)?( answer| option| one)?)\b\s*/i, '') :
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
    const tx = clean(b.innerText || b.getAttribute('aria-label'));
    if (!/^add( another)?\b/i.test(tx) || !vis(b) || !shown(b) || tx.length > 40) continue;
    b.setAttribute('data-jf-add', String(adds.length));
    adds.push({id: adds.length, text: tx, section: section(b) || clean(b.getAttribute('aria-label'))}); }
  const errors = all('[role=alert], [aria-invalid=true], [class*=error i], [id*=error i], [data-automation-id*=error i]')
      .filter(vis).map(e => clean(e.innerText)).filter(t => t && t.length < 300);
  const headings = all('h1, h2, h3, [data-automation-id=pageHeader], [aria-current=step]')
      .filter(vis).map(h => clean(h.innerText)).filter(Boolean).slice(0, 8);
  // Upload controls may mount a native input only after a button is clicked.
  for (const button of all('button,[role=button]')) {
    const text = clean(button.innerText || button.getAttribute('aria-label'));
    if (!/^(choose (a )?file|browse( files)?|upload( (a |your )?(file|resume|cv|cover letter|document|attachment))?|attach( (a |your )?(file|resume|cv|cover letter|document|attachment))?)$/i.test(text)
        || !vis(button) || !shown(button) || button.hasAttribute('data-jf')) continue;
    const box = button.closest('fieldset,[data-field-path],[data-field-entry-id],[class*=upload i],.form-group,.form-field') || button.parentElement;
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
}"""


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

    @property
    def complete(self) -> bool:
        return not self.unreadable and not self.opaque

    def problems(self) -> list[str]:
        out = [f"a frame could not be read ({u['url']}: {u['reason']})" for u in self.unreadable]
        out += [f"a region the reader cannot see into (custom element <{tag}>): any controls inside need a person or an adapter"
                for tag in self.opaque]
        return out

    def summary(self) -> dict:
        return {"complete": self.complete, "frames_read": self.frames_read, "problems": self.problems(),
                "challenge_frames": self.challenge_frames, "shadow_roots": self.shadow_roots, "shadow_fields": self.shadow_fields}


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
        n = snap["next"]
        for x in snap["fields"]:
            fields.append(Field(id=x["id"], kind=x["kind"], label=clean_label(x["label"]), required=bool(x.get("required")),
                                value=x.get("value") or "", options=x.get("options") or [], section=x.get("section", ""),
                                frame=k, multiple=bool(x.get("multiple")), accept=x.get("accept", ""),
                                maxlength=x.get("maxlength"), format=x.get("format", ""), help=x.get("help", ""),))
        adds += [{**a, "frame": k} for a in snap.get("adds", [])]
        buttons += snap["buttons"]
        errors += snap["errors"]
        headings += snap["headings"]
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


def match_option(answer: str, options: list[str], cutoff: int = 80) -> tuple[str | None, int]:
    """The page option that means `answer` (e.g. "USA" -> "United States of America"); (None, best score) when no
    option is close enough for code to decide."""
    opts = [o for o in options if o and not re.match(r"(?i)^\s*(select|choose|--|please select)\b.*", o)]
    if not answer or not opts:
        return None, 0
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


def same_value(want: str, got: str, kind: str) -> bool:
    """The page shows the value that was meant (read-back check)."""
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


def locate(view: PageView, f: Field):
    return field_element(view.frames[f.frame], f'[data-jf="{f.id}"]')


def _open_options(frame: Frame, loc, page: Page) -> list[str]:
    loc.scroll_into_view_if_needed(timeout=5000)
    loc.click(timeout=8000)
    page.wait_for_timeout(700)
    return [o for o in frame.evaluate(OPTIONS_JS) if not _NO_OPTIONS.match(o)]


def discover_options(page: Page, view: PageView, log=print) -> None:
    """Open each combobox once to read its options (react-select and Workday lists render them only when open).
    A search box with no list until something is typed keeps no options (the answer is typed then)."""
    for f in view.fields:
        if f.kind != "combobox" or f.options:
            continue
        try:
            loc = locate(view, f)
            f.options = _open_options(view.frames[f.frame], loc, page)[:300]
            if loc.get_attribute("data-uxi-widget-type") == "selectinput" and len(f.options) <= 15:
                f.options = _expand_categories(page, view.frames[f.frame], loc, f.options)
        except Exception as e:
            log(f"forms: options of \"{f.label[:50]}\" unreadable: {type(e).__name__}")
        finally:
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)


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


def _choose(question: str, answer: str, options: list[str], tie_break) -> str:
    opt, score = match_option(answer, options)
    if opt is None and tie_break and options:
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


def fill_field(page: Page, view: PageView, f: Field, answer: str, files: dict[str, str], tie_break=None,
               strict: bool = False, close: bool = False) -> str:
    """Put `answer` into the field. Returns what was chosen (the option text for choice fields)."""
    from jobagent.tools import challenge
    if challenge.guard(page):
        view = read_page(page)
        matches = [g for g in view.fields if g.label == f.label and g.kind == f.kind]
        if len(matches) != 1:
            raise challenge.Blocked("Form changed after verification; field requires review")
        f = matches[0]
    frame, loc = view.frames[f.frame], locate(view, f)
    if f.kind == "file":
        path = files.get(answer) or answer
        name = path.rsplit("/", 1)[-1]
        if loc.evaluate("e => e.tagName !== 'INPUT'"):
            # Associate the clicked control with its chooser/new input, without assuming a caption or input ID.
            choosers = []
            listener = lambda chooser: choosers.append(chooser)
            shadow_locator(frame, 'input[type=file]').evaluate_all(
                "es=>es.forEach(e=>e.setAttribute('data-jf-upload-existing','1'))")
            page.on('filechooser', listener)
            try:
                loc.click(timeout=8000)
                fresh = shadow_locator(frame, 'input[type=file]:not([data-jf-upload-existing])')
                for _ in range(16):
                    if choosers or fresh.count():
                        break
                    page.wait_for_timeout(500)
                if choosers:
                    loc = choosers[0].element
                elif fresh.count() == 1:
                    loc = fresh.first
                else:
                    raise FillError('Upload button did not expose one identifiable file input')
                loc.evaluate("(e,label)=>e.setAttribute('data-jf-upload-label',label)", f.label)
            finally:
                page.remove_listener('filechooser', listener)
                shadow_locator(frame, '[data-jf-upload-existing]').evaluate_all(
                    "es=>es.forEach(e=>e.removeAttribute('data-jf-upload-existing'))")
        for _ in range(2):  # the upload counts once the page shows the file's name
            if getattr(getattr(page, 'context', None), 'jobagent_policy', None):
                # The agent runs in Docker but Chrome runs on the host. Transfer bytes;
                # a Docker /app path is not a file path on the desktop browser's host.
                import mimetypes
                from pathlib import Path
                loc.set_input_files({'name': name, 'mimeType': mimetypes.guess_type(path)[0] or 'application/octet-stream',
                                     'buffer': Path(path).read_bytes()})
            else:
                loc.set_input_files(path)
            for _ in range(10):
                page.wait_for_timeout(2000)
                try:
                    if name in loc.evaluate('e=>[...(e.files||[])].map(f=>f.name)'):
                        return path
                except Exception:
                    pass  # some uploaders replace their native input after accepting the file
                if any(name in fr.locator("body").inner_text(timeout=5000) for fr in view.frames.values()):
                    return path
        raise FillError(f"{name} was attached but the page never showed it")
    loc.scroll_into_view_if_needed(timeout=5000)
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
                                      close=close_only)
                except Exception as e:
                    errors.append(f"{variant}: {str(e)[:60]}")
                    page.keyboard.press("Escape")
        raise FillError("no variant offered: " + "; ".join(errors)[:300])
    if f.kind == "combobox" and ";" in answer and loc.get_attribute("data-uxi-widget-type") == "selectinput":
        # a multi-value search picker (Workday skills): add each value the site knows; skip the ones it doesn't
        added = []
        for item in [x.strip() for x in answer.split(";") if x.strip()]:
            try:
                one = Field(**{**f.__dict__, "options": []})
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
        opt = close_option(answer, f.options) if close else _choose(f.label, answer, f.options, tie_break)
        if opt is None:
            raise FillError(f"{answer!r} isn't offered as such")
        loc.select_option(label=opt, timeout=8000)
        return opt
    if f.kind == "checkbox":
        want = _norm(answer) in ("checked", "yes", "true", "agree", "i agree", "accept")
        if loc.is_visible():
            loc.set_checked(want, timeout=8000)
        else:  # styled box: its label takes the click
            if loc.is_checked() != want:
                shadow_locator(frame, f'label:has([data-jf="{f.id}"])').first.click(timeout=8000)
        return "checked" if want else ""
    if f.kind == "buttons":  # press the button that is the answer (Ashby Yes / No)
        opt = close_option(answer, f.options) if close else _choose(f.label, answer, f.options, tie_break)
        if opt is None:
            raise FillError(f"{answer!r} isn't offered as such")
        k = f.options.index(opt)
        field_element(frame, f'[data-jf="{f.id}"][data-jf-opt="{k}"]').click(timeout=8000)
        page.wait_for_timeout(500)
        return opt
    if f.kind in ("radio", "checkboxes"):
        wanted = [a.strip() for a in (answer.split(";") if f.kind == "checkboxes" else [answer]) if a.strip()]
        chosen = [close_option(a, f.options) if close else _choose(f.label, a, f.options, tie_break) for a in wanted]
        if None in chosen:
            raise FillError(f"{answer!r} isn't offered as such")
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
        if f.value and not strict and loc.get_attribute("data-uxi-widget-type") == "selectinput":
            # a single-choice Workday picker that already holds something else (an earlier run's pick): its chip
            # sits over the input, so remove it before choosing
            box = loc.locator("xpath=ancestor::*[@data-automation-id='multiSelectContainer'][1]")
            pill = box.locator("[data-automation-id=selectedItem]")  # "Other, press delete to clear value."
            for _ in range(3):
                if not pill.count():
                    break
                try:
                    pill.first.focus()
                    page.keyboard.press("Delete")
                except Exception:
                    box.locator("[data-automation-id=DELETE_charm]").first.click(timeout=5000, force=True)
                page.wait_for_timeout(700)
        opts = _open_options(frame, loc, page)
        if " > " in answer and loc.get_attribute("data-uxi-widget-type") == "selectinput":
            # "Category > meaning": open the category, then the entry that means the rest
            cat, answer = (x.strip() for x in answer.split(" > ", 1))
            first = _choose(f.label, cat, opts, tie_break)
            shadow_locator(frame, "[role=option], [data-automation-id=promptOption]").filter(
                has_text=re.compile(rf"^\s*{re.escape(first)}\s*$")).first.click(timeout=8000)
            page.wait_for_timeout(1200)
            opts = [o for o in frame.evaluate(OPTIONS_JS) if not _NO_OPTIONS.match(o)]
        pick = (lambda ans, o: (strict_option(ans, o), 0)) if strict else \
            (lambda ans, o: (close_option(ans, o), 0)) if close else match_option
        opt, _ = pick(answer, opts) if opts else (None, 0)
        if opt is None and (loc.evaluate("e => e.tagName") == "INPUT"):
            # a search box: type the answer (or its first words) and pick from what the site suggests
            for probe in dict.fromkeys([answer[:60], " ".join(answer.split()[:3]), answer.split()[0] if answer else ""]):
                if not probe:
                    continue
                loc.fill("")
                loc.type(probe, delay=40)
                if loc.get_attribute("data-uxi-widget-type") == "selectinput":
                    loc.press("Enter")  # Workday searches on Enter
                page.wait_for_timeout(1800)
                opts = [o for o in frame.evaluate(OPTIONS_JS) if not _NO_OPTIONS.match(o)]
                if not opts and loc.get_attribute("data-uxi-widget-type") == "selectinput":
                    # Workday picks a search's only result by itself: no list, just the new chip
                    chips = loc.evaluate("e => [...(e.closest('[data-automation-id=multiSelectContainer]') || "
                                         "e.parentElement).querySelectorAll('[data-automation-id=selectedItem]')]"
                                         ".map(x => x.innerText.trim())")
                    if chips and pick(answer, chips)[0]:
                        return chips[-1]
                    for _ in chips if not strict else []:  # it picked something else: undo
                        box = loc.locator("xpath=ancestor::*[@data-automation-id='multiSelectContainer'][1]")
                        box.locator("[data-automation-id=selectedItem]").last.focus()
                        page.keyboard.press("Delete")
                        page.wait_for_timeout(500)
                if opts:
                    opt, _ = pick(answer, opts)
                    if opt:
                        break
        if opt is None and (strict or close):
            page.keyboard.press("Escape")
            raise FillError(f"{answer!r} isn't offered as such")
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
            page.wait_for_timeout(1200)
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
    """The form has a submit control without a label (shown only once the form is complete, e.g. after a human
    check)."""
    for f in frames_of(page):
        try:
            if shadow_locator(f, SUBMIT_INPUT).count():
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


def attachment_evidence(page: Page, view: PageView, field: "Field | None", others: list[str], known: list[str] = (),
                        label: str = "") -> str:
    """What shows that THIS upload has a file: its own non-empty `files`, or a file name inside its own boundary (see
    above). `field` is the live control (None when its input is gone: then `label` is its recorded label); `others` are
    the captions of every other upload. A name in `known` only confirms evidence already inside the boundary."""
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
            ups.append({"field": None, "label": e["label"], "required": bool(e.get("required")), "filled": e.get("filled", "")})
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
    known = [k for k in known if k]
    ups = _uploads(view, records)
    caps = [_captions(u["label"]) for u in ups]
    out = []
    for i, u in enumerate(ups):
        if not u["required"]:
            continue
        others = [c for j, cs in enumerate(caps) if j != i for c in cs]
        names = known + ([Path(u["filled"]).name] if u.get("filled") else [])
        if attachment_evidence(page, view, u["field"], others, names, u["label"]):
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
        btn = frame.get_by_role("button", name=name)
        if not btn.count():
            btn = frame.get_by_role("link", name=name)
        if not btn.count() and getattr(frame.page.context, 'jobagent_shadow_selectors', False):
            btn = shadow_locator(frame, 'button, [role=button], a, [role=link]').filter(has_text=name)
        btn = btn.filter(visible=True)
        if btn.count():
            yield btn.first


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
        for btn in _visible_buttons(view, text):
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


def click_open_application(page: Page, view: PageView, pattern: re.Pattern, *, expected_url: str | None = None) -> str | None:
    """Startup: open the application (Apply, Apply Manually, Continue application). Navigation only, and only what can be
    established as navigation:
    - a control that belongs to a form with fields to fill is that form's own control: never acted on here;
    - a real link to another page is followed by loading its address (no click handler runs);
    - any other control is clicked only while the page has no application fields at all (nothing for it to send);
      with fields on the page it is left to the planner, the filler and the reviewed submission path (step 13)."""
    from jobagent.tools import challenge
    if challenge.guard(page):
        view = read_page(page)
    fields_on_page = bool(view.fields)
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
                try:
                    page.goto(info["href"], wait_until="domcontentloaded")
                except Exception:
                    continue
                return text
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
    ups = _uploads(view, records)
    caps = [_captions(u["label"]) for u in ups]
    out = []
    for i, u in enumerate(ups):
        if u["field"] is not None:
            continue
        others = [c for j, cs in enumerate(caps) if j != i for c in cs]
        out.append({"label": u["label"], "observed": attachment_evidence(page, view, None, others, [], u["label"])})
    return out
