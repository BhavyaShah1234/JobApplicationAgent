"""Step 12: application forms. Read every visible field of the page (main page and iframes), match each one to an
answer, fill it, and read it back.

A field is one question: a text box, a `<select>`, a radio group, a checkbox (or a group of them), a file input, or
a combobox (ARIA comboboxes such as react-select on Greenhouse, Workday's listbox buttons and search prompts). Every
field gets a `data-jf` number on the page, so the code finds it again without asking a model. Option fields are
matched to the page's own options (rapidfuzz, then a model tie-break); every value is read back from the page after
filling. Submit buttons are never clicked here.
"""

import re
from dataclasses import dataclass, field

from playwright.sync_api import Frame, Page
from rapidfuzz import fuzz, process

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
  const byIds = ids => clean((ids || '').split(/\s+/).map(i => document.getElementById(i))
      .filter(Boolean).map(x => x.innerText || x.textContent).join(' '));
  const ownText = e => { const l = e.closest('label'); return l ? clean(l.innerText) : ''; };
  const question = e => {
    // the question a control belongs to: aria, label[for], wrapping label, legend, or the nearest container text
    // Workday: the form field's own label (a list button's aria-label holds its value: "Select One Required")
    const ff = e.closest('[data-automation-id^=formField-]');
    const fl = ff && ff.querySelector(':scope > label, :scope > legend, :scope > fieldset > legend, label');
    if (fl && clean(fl.innerText)) return clean(fl.innerText).slice(0, 300);
    let t = byIds(e.getAttribute('aria-labelledby'));
    if (!t && e.id) { const l = document.querySelector(`label[for="${CSS.escape(e.id)}"]`); if (l) t = clean(l.innerText); }
    if (!t) t = clean(e.getAttribute('aria-label'));
    if (!t) { const l = e.closest('label'); if (l) t = clean(l.innerText); }
    if (!t) { const fs = e.closest('fieldset'); const lg = fs && fs.querySelector('legend'); if (lg) t = clean(lg.innerText); }
    if (!t) t = clean(e.placeholder || e.getAttribute('title') || '');
    if (!t) t = around(e, ownOf(e));
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
    const g = e.closest('fieldset, [role=radiogroup], [role=group]');
    if (g) { const lg = g.querySelector('legend');
      const t = (lg && clean(lg.innerText)) || byIds(g.getAttribute('aria-labelledby')) || clean(g.getAttribute('aria-label'));
      if (t) return t.slice(0, 300); }
    const labels = [...document.querySelectorAll(`input[name="${CSS.escape(name)}"]`)].map(b => {
      const l = b.closest('label') || (b.id && document.querySelector(`label[for="${CSS.escape(b.id)}"]`));
      return l ? clean(l.innerText) : clean(b.value); });
    return (around(e, labels) || question(e)).slice(0, 300); };
  const section = e => {
    // the nearest heading above the field (e.g. "Work Experience 2", "Voluntary Self-Identification")
    // headings only: a legend is a field's own label (Workday's "To*" date), not a section
    const hs = [...document.querySelectorAll('h1, h2, h3, h4, h5, [role=heading]')].filter(vis);
    let best = '';
    for (const h of hs) { if (h.compareDocumentPosition(e) & Node.DOCUMENT_POSITION_FOLLOWING) best = clean(h.innerText); }
    return best.slice(0, 80); };
  const required = (e, q) => e.required || e.getAttribute('aria-required') === 'true' || /\*\s*$|\*\s|\(required\)/i.test(q);
  const out = []; let n = start; const seen = new Set();
  const add = (e, f) => { e.setAttribute('data-jf', String(n)); f.id = n++; f.section = section(e); out.push(f); };
  const ctl = 'input, select, textarea, [role=combobox], button[aria-haspopup=listbox], [role=checkbox], [role=radio], [role=spinbutton]';
  for (const e of document.querySelectorAll(ctl)) {
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
      const q = [question(e), cap, e.id || e.name].filter(t => t && !/^attach$/i.test(t)).join(' / ');
      add(e, {kind: 'file', label: q, required: required(e, cap),
              // only files really attached: the box's caption ("ATTACH RESUME/CV") is not a value
              value: e.files && e.files.length ? [...e.files].map(f => f.name).join(', ') : '',
              accept: e.getAttribute('accept') || ''});
      continue; }
    // a react-select input shrinks to nothing once a value is chosen: its control box is what shows
    const isCombo = (e.getAttribute('role') || '').toLowerCase() === 'combobox';
    const seenBox = isCombo ? (e.closest('[class*=control i], [class*=container i]') || e.parentElement) : e;
    if (!vis(seenBox) && !(t === 'radio' || t === 'checkbox')) continue;
    if ((t === 'radio' || t === 'checkbox') && !vis(e)) {
      // custom-styled boxes hide the input itself; its label must show
      const l = e.closest('label') || (e.id && document.querySelector(`label[for="${CSS.escape(e.id)}"]`));
      if (!l || !vis(l)) continue; }
    if (!shown(seenBox) && !(t === 'radio' || t === 'checkbox')) continue;
    if (e.closest('[data-jf-skip], header, nav, [role=banner], [data-automation-id=navigationContainer]')) continue;
    if (e.closest('[data-automation-id=promptOption], [data-automation-id=activeListContainer], [role=option]')) continue;  // inside an open option list
    // a group is the boxes sharing a name inside one question (Workday repeats a name in every work-experience entry)
    const scope = e.closest('fieldset, [role=group], [role=radiogroup], [data-automation-id^=formField-]') || document;
    const sameName = t === 'radio' || t === 'checkbox' ? [...scope.querySelectorAll(`input[type=${t}][name="${CSS.escape(e.name || '')}"]`)] : [];
    if (e.hasAttribute('data-jf')) continue;  // already part of a group
    const ffBoxes = t === 'checkbox' && e.closest('[data-automation-id^=formField-]')
        ? [...e.closest('[data-automation-id^=formField-]').querySelectorAll('input[type=checkbox]')] : [];
    if (t === 'radio' || (t === 'checkbox' && ((e.name && sameName.length > 1) || ffBoxes.length > 1))) {
      // Workday lists one question's checkboxes under one form field, each with its own name
      const boxes = ffBoxes.length > 1 ? ffBoxes : (e.name ? sameName : [e]);
      const opts = boxes.map(b => { let l = ownText(b);
        if (!l && b.id) { const x = document.querySelector(`label[for="${CSS.escape(b.id)}"]`); if (x) l = clean(x.innerText); }
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
      const shownValue = e.tagName === 'BUTTON' ? clean(e.innerText) :
          clean((box && box.querySelector('[class*=singleValue i], [class*=single-value i], [class*=multiValue i], [data-automation-id=selectedItem]') || {}).innerText || e.value || '');
      add(e, {kind: 'combobox', label: q, required: required(e, q), value: /^select one$|^select\.\.\.$/i.test(shownValue) ? '' : shownValue}); continue; }
    if (role === 'spinbutton' || e.tagName === 'INPUT' || e.tagName === 'TEXTAREA') {
      const kind = e.tagName === 'TEXTAREA' ? 'textarea' : (role === 'spinbutton' ? 'spin' : (t || 'text'));
      if (/search/i.test(t) || /^search/i.test(q)) continue;
      add(e, {kind, label: q, required: required(e, q), value: e.value || '', maxlength: e.maxLength > 0 ? e.maxLength : null});
    }
  }
  const buttons = [...document.querySelectorAll('button, [role=button], input[type=submit], a[href]')]
      .filter(b => vis(b) && shown(b) && !b.disabled && !b.hasAttribute('data-jf'))
      .map(b => clean(b.innerText || b.value || b.getAttribute('aria-label'))).filter(t => t && t.length < 60);
  // "Add" / "Add Another" buttons (Workday work experience, education, languages): the planner may ask for entries
  const adds = [];
  for (const b of document.querySelectorAll('button, [role=button]')) {
    const tx = clean(b.innerText || b.getAttribute('aria-label'));
    if (!/^add( another)?\b/i.test(tx) || !vis(b) || !shown(b) || tx.length > 40) continue;
    b.setAttribute('data-jf-add', String(adds.length));
    adds.push({id: adds.length, text: tx, section: section(b) || clean(b.getAttribute('aria-label'))}); }
  const errors = [...document.querySelectorAll('[role=alert], [aria-invalid=true], [class*=error i], [id*=error i], [data-automation-id*=error i]')]
      .filter(vis).map(e => clean(e.innerText)).filter(t => t && t.length < 300);
  const headings = [...document.querySelectorAll('h1, h2, h3, [data-automation-id=pageHeader], [aria-current=step]')]
      .filter(vis).map(h => clean(h.innerText)).filter(Boolean).slice(0, 8);
  return {fields: out, next: n, adds, buttons: [...new Set(buttons)].slice(0, 60), errors: [...new Set(errors)].slice(0, 20), headings};
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

    def brief(self) -> str:
        opts = ""
        if self.options:
            shown = self.options if len(self.options) <= 40 else self.options[:40] + [f"... {len(self.options)} total"]
            opts = f" options={shown}"
        return (f"[{self.id}] {self.kind}{' REQUIRED' if self.required else ''} \"{self.label[:200]}\""
                f"{f' section=\"{self.section}\"' if self.section else ''}{opts}"
                f"{f' current=\"{self.value[:80]}\"' if self.value else ''}"
                f"{f' maxlength={self.maxlength}' if self.maxlength else ''}"
                f"{f' format={self.format}' if self.format else ''}")


@dataclass
class PageView:
    fields: list[Field]
    buttons: list[str]
    errors: list[str]
    headings: list[str]
    frames: dict[int, Frame]
    adds: list[dict] = field(default_factory=list)


def frames_of(page: Page) -> list[Frame]:
    return [page.main_frame] + [f for f in page.frames if f != page.main_frame and f.url.startswith("http")
                                and not CAPTCHA_FRAME.search(f.url)]


def read_page(page: Page) -> PageView:
    """Every visible field on the page and in its iframes, numbered across frames."""
    fields, buttons, errors, headings, frames, n, adds = [], [], [], [], {}, 0, []
    for k, f in enumerate(frames_of(page)):
        try:
            f.evaluate("() => document.querySelectorAll('[data-jf], [data-jf-opt], [data-jf-add]').forEach(e => "
                       "{e.removeAttribute('data-jf'); e.removeAttribute('data-jf-opt'); e.removeAttribute('data-jf-add')})")
            snap = f.evaluate(FIELDS_JS, n)
        except Exception:
            continue
        frames[k] = f
        n = snap["next"]
        for x in snap["fields"]:
            fields.append(Field(id=x["id"], kind=x["kind"], label=x["label"], required=bool(x.get("required")),
                                value=x.get("value") or "", options=x.get("options") or [], section=x.get("section", ""),
                                frame=k, multiple=bool(x.get("multiple")), accept=x.get("accept", ""),
                                maxlength=x.get("maxlength"), format=x.get("format", "")))
        adds += [{**a, "frame": k} for a in snap.get("adds", [])]
        buttons += snap["buttons"]
        errors += snap["errors"]
        headings += snap["headings"]
    return PageView(fields, list(dict.fromkeys(buttons)), list(dict.fromkeys(errors)), list(dict.fromkeys(headings)),
                    frames, adds)


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
    for canon, alts in _SYNONYMS.items():  # same meaning written another way
        group = {canon, *alts}
        if a in group:
            for o in opts:
                if _norm(o) in group or _norm(o).startswith(canon + " ") or _norm(o).startswith(a + ","):
                    return o, 95
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
        return any(same_value(v.strip(), got, kind) for v in want.split(" | "))
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
    return kind in ("combobox", "select", "radio") and (w in g or fuzz.token_set_ratio(w, g) >= 90)


# --- comboboxes ------------------------------------------------------------------------------------------------------

OPTIONS_JS = r"""() => [...new Set([...document.querySelectorAll('[role=option], [role=menuitem], [data-automation-id=promptOption]')]
  .filter(e => { const r = e.getBoundingClientRect(); return r.width > 1 && r.height > 1
                 && !e.closest('[data-automation-id=selectedItemList], [data-automation-id=selectedItem]'); })
  .map(e => (e.innerText || e.textContent || '').replace(/\s+/g, ' ').trim()).filter(Boolean))]"""

_NO_OPTIONS = re.compile(r"(?i)^(no (options|results|items|matches)( found)?\.?|loading\.*|type to search|start typing.*|"
                         r"select one)$")


def locate(view: PageView, f: Field):
    return view.frames[f.frame].locator(f'[data-jf="{f.id}"]').first


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
            frame.locator("[role=option], [data-automation-id=promptOption]").filter(
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
    best = max(options, key=lambda o: fuzz.ratio(a, _norm(o)), default=None)
    return best if best is not None and (fuzz.ratio(a, _norm(best)) >= 90 or _norm(best) == a) else None


def fill_field(page: Page, view: PageView, f: Field, answer: str, files: dict[str, str], tie_break=None,
               strict: bool = False, close: bool = False) -> str:
    """Put `answer` into the field. Returns what was chosen (the option text for choice fields)."""
    frame, loc = view.frames[f.frame], locate(view, f)
    if f.kind == "file":
        path = files.get(answer) or answer
        loc.set_input_files(path)
        page.wait_for_timeout(2500)
        return path
    loc.scroll_into_view_if_needed(timeout=5000)
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
                frame.locator(f'label:has([data-jf="{f.id}"])').first.click(timeout=8000)
        return "checked" if want else ""
    if f.kind in ("radio", "checkboxes"):
        wanted = [a.strip() for a in (answer.split(";") if f.kind == "checkboxes" else [answer]) if a.strip()]
        chosen = [close_option(a, f.options) if close else _choose(f.label, a, f.options, tie_break) for a in wanted]
        if None in chosen:
            raise FillError(f"{answer!r} isn't offered as such")
        for k, o in enumerate(f.options):
            box = frame.locator(f'[data-jf="{f.id}"][data-jf-opt="{k}"]').first
            on = o in chosen
            if box.is_checked() == on or (f.kind == "radio" and not on):
                continue
            try:
                box.set_checked(on, timeout=4000)
            except Exception:  # hidden custom input: click its label
                bid = box.get_attribute("id")
                lab = frame.locator(f'label[for="{bid}"]').first if bid else frame.locator(f'label:has([data-jf-opt="{k}"][data-jf="{f.id}"])').first
                lab.click(timeout=8000)
        return "; ".join(chosen)
    if f.kind == "combobox":
        opts = _open_options(frame, loc, page)
        if " > " in answer and loc.get_attribute("data-uxi-widget-type") == "selectinput":
            # "Category > meaning": open the category, then the entry that means the rest
            cat, answer = (x.strip() for x in answer.split(" > ", 1))
            first = _choose(f.label, cat, opts, tie_break)
            frame.locator("[role=option], [data-automation-id=promptOption]").filter(
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
            option = frame.locator("[role=option], [role=menuitem], [data-automation-id=promptOption]").filter(
                has_text=re.compile(rf"^\s*{re.escape(opt)}\s*$"))
        try:
            option.first.click(timeout=8000)
        finally:
            page.wait_for_timeout(1200)
        if loc.get_attribute("data-uxi-widget-type") == "selectinput":
            # a Workday category ("Job Board") opens its own list ("LinkedIn", "Indeed"): pick again from that
            for _ in range(2):
                chosen = loc.evaluate("e => [...(e.closest('[data-automation-id=multiSelectContainer]') || e.parentElement)"
                                      ".querySelectorAll('[data-automation-id=selectedItemList] li')].length")
                sub = [o for o in frame.evaluate(OPTIONS_JS) if not _NO_OPTIONS.match(o)]
                if chosen or not sub:
                    break
                pick = _choose(f.label, answer, sub, tie_break)
                frame.locator("[role=option], [data-automation-id=promptOption]").filter(
                    has_text=re.compile(rf"^\s*{re.escape(pick)}\s*$")).first.click(timeout=8000)
                page.wait_for_timeout(1200)
                opt = f"{opt} > {pick}"
            page.keyboard.press("Escape")
        return opt
    raise FillError(f"unknown field kind {f.kind}")


# --- buttons -------------------------------------------------------------------------------------------------------

def click_button(page: Page, view: PageView, pattern: re.Pattern, allow_submit: bool = False) -> str | None:
    """Click the first visible button whose text matches `pattern`; never one that sends the application."""
    for text in view.buttons:
        if not pattern.search(text):
            continue
        if SUBMIT.search(text) and not allow_submit:
            continue
        # innerText follows CSS text-transform ("APPLY") while the accessible name doesn't ("Apply")
        name = re.compile(rf"^\s*{re.escape(text)}\s*$", re.I)
        for frame in view.frames.values():
            btn = frame.get_by_role("button", name=name)
            if not btn.count():
                btn = frame.get_by_role("link", name=name)
            btn = btn.filter(visible=True)
            if btn.count():
                # don't wait for the navigation here (Lever's apply page can take longer than the click timeout);
                # the next read of the page waits for it
                btn.first.click(timeout=10000, no_wait_after=True)
                return text
    return None
