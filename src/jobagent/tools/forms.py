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
    let t = byIds(e.getAttribute('aria-labelledby'));
    if (!t && e.id) { const l = document.querySelector(`label[for="${CSS.escape(e.id)}"]`); if (l) t = clean(l.innerText); }
    if (!t) t = clean(e.getAttribute('aria-label'));
    if (!t) { const l = e.closest('label'); if (l) t = clean(l.innerText); }
    if (!t) { const fs = e.closest('fieldset'); const lg = fs && fs.querySelector('legend'); if (lg) t = clean(lg.innerText); }
    if (!t) t = clean(e.placeholder || e.getAttribute('title') || '');
    if (!t) { let a = e.parentElement;
      for (let k = 0; a && k < 5; k++, a = a.parentElement) { const x = clean(a.innerText); if (x && x.length < 300) { t = x; break; } } }
    return t.slice(0, 300); };
  const groupQuestion = (e, name) => {
    // radio / checkbox groups: the group's legend or aria label, else the container text before the options
    const g = e.closest('fieldset, [role=radiogroup], [role=group]');
    if (g) { const lg = g.querySelector('legend');
      const t = (lg && clean(lg.innerText)) || byIds(g.getAttribute('aria-labelledby')) || clean(g.getAttribute('aria-label'));
      if (t) return t.slice(0, 300); }
    let a = e.parentElement;
    for (let k = 0; a && k < 6; k++, a = a.parentElement) {
      const n = a.querySelectorAll(`input[name="${CSS.escape(name)}"]`).length;
      const x = clean(a.innerText);
      if (n > 1 && x) { return x.slice(0, 300); } }
    return question(e); };
  const section = e => {
    // the nearest heading above the field (e.g. "Work Experience 2", "Voluntary Self-Identification")
    const hs = [...document.querySelectorAll('h1, h2, h3, h4, legend, [role=heading]')].filter(vis);
    let best = '';
    for (const h of hs) { if (h.compareDocumentPosition(e) & Node.DOCUMENT_POSITION_FOLLOWING) best = clean(h.innerText); }
    return best.slice(0, 80); };
  const required = (e, q) => e.required || e.getAttribute('aria-required') === 'true' || /\*\s*$|\*\s|\(required\)/i.test(q);
  const out = []; let n = start; const seen = new Set();
  const add = (e, f) => { e.setAttribute('data-jf', String(n)); f.id = n++; f.section = section(e); out.push(f); };
  const ctl = 'input, select, textarea, [role=combobox], button[aria-haspopup=listbox], [role=checkbox], [role=radio], [role=spinbutton]';
  for (const e of document.querySelectorAll(ctl)) {
    const t = (e.getAttribute('type') || '').toLowerCase();
    if (t === 'hidden' || t === 'submit' || t === 'button' || t === 'image' || t === 'reset' || e.disabled) continue;
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
              value: e.files && e.files.length ? [...e.files].map(f => f.name).join(', ') : clean(box.innerText).slice(0, 120),
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
    if (e.closest('[data-jf-skip]')) continue;
    if (t === 'radio' || (t === 'checkbox' && e.name && document.querySelectorAll(`input[type=checkbox][name="${CSS.escape(e.name)}"]`).length > 1)) {
      const key = t + ':' + e.name; if (seen.has(key)) continue; seen.add(key);
      const boxes = [...document.querySelectorAll(`input[type=${t}][name="${CSS.escape(e.name)}"]`)];
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
  const errors = [...document.querySelectorAll('[role=alert], [aria-invalid=true], [class*=error i], [id*=error i], [data-automation-id*=error i]')]
      .filter(vis).map(e => clean(e.innerText)).filter(t => t && t.length < 300);
  const headings = [...document.querySelectorAll('h1, h2, h3, [data-automation-id=pageHeader], [aria-current=step]')]
      .filter(vis).map(h => clean(h.innerText)).filter(Boolean).slice(0, 8);
  return {fields: out, next: n, buttons: [...new Set(buttons)].slice(0, 60), errors: [...new Set(errors)].slice(0, 20), headings};
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

    def brief(self) -> str:
        opts = ""
        if self.options:
            shown = self.options if len(self.options) <= 40 else self.options[:40] + [f"... {len(self.options)} total"]
            opts = f" options={shown}"
        return (f"[{self.id}] {self.kind}{' REQUIRED' if self.required else ''} \"{self.label[:200]}\""
                f"{f' section=\"{self.section}\"' if self.section else ''}{opts}"
                f"{f' current=\"{self.value[:80]}\"' if self.value else ''}"
                f"{f' maxlength={self.maxlength}' if self.maxlength else ''}")


@dataclass
class PageView:
    fields: list[Field]
    buttons: list[str]
    errors: list[str]
    headings: list[str]
    frames: dict[int, Frame]


def frames_of(page: Page) -> list[Frame]:
    return [page.main_frame] + [f for f in page.frames if f != page.main_frame and f.url.startswith("http")
                                and not CAPTCHA_FRAME.search(f.url)]


def read_page(page: Page) -> PageView:
    """Every visible field on the page and in its iframes, numbered across frames."""
    fields, buttons, errors, headings, frames, n = [], [], [], [], {}, 0
    for k, f in enumerate(frames_of(page)):
        try:
            f.evaluate("() => document.querySelectorAll('[data-jf], [data-jf-opt]').forEach(e => "
                       "{e.removeAttribute('data-jf'); e.removeAttribute('data-jf-opt')})")
            snap = f.evaluate(FIELDS_JS, n)
        except Exception:
            continue
        frames[k] = f
        n = snap["next"]
        for x in snap["fields"]:
            fields.append(Field(id=x["id"], kind=x["kind"], label=x["label"], required=bool(x.get("required")),
                                value=x.get("value") or "", options=x.get("options") or [], section=x.get("section", ""),
                                frame=k, multiple=bool(x.get("multiple")), accept=x.get("accept", ""),
                                maxlength=x.get("maxlength")))
        buttons += snap["buttons"]
        errors += snap["errors"]
        headings += snap["headings"]
    return PageView(fields, list(dict.fromkeys(buttons)), list(dict.fromkeys(errors)), list(dict.fromkeys(headings)),
                    frames)


# --- option matching ------------------------------------------------------------------------------------------------

_SYNONYMS = {
    "united states": ["united states of america", "usa", "us", "u.s.", "u.s.a.", "america"],
    "yes": ["y", "true", "i am", "i do", "i will"],
    "no": ["n", "false", "i am not", "i do not", "i don't", "i will not"],
    "male": ["man", "m"],
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


def same_value(want: str, got: str, kind: str) -> bool:
    """The page shows the value that was meant (read-back check)."""
    w, g = _norm(want), _norm(got)
    if kind in ("tel",):
        return re.sub(r"\D", "", want)[-10:] == re.sub(r"\D", "", got)[-10:]
    if kind == "checkbox":
        return (w in ("checked", "yes", "true")) == (g == "checked")
    if kind in ("checkboxes",):
        return {_norm(x) for x in want.split(";") if x.strip()} == {_norm(x) for x in got.split(";") if x.strip()}
    if kind == "file":
        return bool(got) and (w in g or _norm(want.rsplit("/", 1)[-1]) in g)
    if w == g:
        return True
    return kind in ("combobox", "select", "radio") and (w in g or fuzz.token_set_ratio(w, g) >= 90)


# --- comboboxes ------------------------------------------------------------------------------------------------------

OPTIONS_JS = r"""() => [...document.querySelectorAll('[role=option], [role=menuitem], [data-automation-id=promptOption]')]
  .filter(e => { const r = e.getBoundingClientRect(); return r.width > 1 && r.height > 1; })
  .map(e => (e.innerText || e.textContent || '').replace(/\s+/g, ' ').trim()).filter(Boolean)"""

_NO_OPTIONS = re.compile(r"(?i)^(no (options|results|items)( found)?|loading\.*|type to search|start typing.*)$")


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
            f.options = _open_options(view.frames[f.frame], locate(view, f), page)[:300]
        except Exception as e:
            log(f"forms: options of \"{f.label[:50]}\" unreadable: {type(e).__name__}")
        finally:
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)


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


def fill_field(page: Page, view: PageView, f: Field, answer: str, files: dict[str, str], tie_break=None) -> str:
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
    if f.kind == "spin":  # Workday's month / year boxes take typed digits
        loc.click(timeout=8000)
        page.keyboard.press("Control+A")
        page.keyboard.type(answer, delay=60)
        return answer
    if f.kind == "select":
        opt = _choose(f.label, answer, f.options, tie_break)
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
        chosen = [_choose(f.label, a, f.options, tie_break) for a in wanted]
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
        opt, _ = match_option(answer, opts) if opts else (None, 0)
        if opt is None and (loc.evaluate("e => e.tagName") == "INPUT"):
            # a search box: type the answer (or its first words) and pick from what the site suggests
            for probe in dict.fromkeys([answer[:60], " ".join(answer.split()[:3]), answer.split()[0] if answer else ""]):
                if not probe:
                    continue
                loc.fill("")
                loc.type(probe, delay=40)
                page.wait_for_timeout(1800)
                opts = [o for o in frame.evaluate(OPTIONS_JS) if not _NO_OPTIONS.match(o)]
                if opts:
                    opt, _ = match_option(answer, opts)
                    if opt:
                        break
        if opt is None:
            try:
                opt = _choose(f.label, answer, opts, tie_break)
            except FillError:
                page.keyboard.press("Escape")
                raise
        option = frame.get_by_role("option", name=opt, exact=True)
        if not option.count():
            option = frame.locator("[role=option], [role=menuitem], [data-automation-id=promptOption]").filter(
                has_text=re.compile(rf"^\s*{re.escape(opt)}\s*$"))
        option.first.click(timeout=8000)
        page.wait_for_timeout(500)
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
        for frame in view.frames.values():
            btn = frame.get_by_role("button", name=text, exact=True)
            if not btn.count():
                btn = frame.get_by_role("link", name=text, exact=True)
            btn = btn.filter(visible=True)
            if btn.count():
                btn.first.click(timeout=10000)
                return text
    return None
