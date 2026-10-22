"""The posting's job description exactly as written (jd.txt), and its sections.

`blocks()` turns the posting's HTML (JSON-LD description or the page's description element) into an ordered list
of heading / bullet / paragraph blocks — no LLM, no rewriting. `render()` writes them as plain text; `sections()`
groups them under their headings; `classify_sections()` asks the fast model what each section is (basic
requirements, preferred, duties, …), falling back to heading rules if the model is unavailable.
"""

import io
import re
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field
from playwright.sync_api import Page

_BLOCKS_JS = r"""
(arg) => {
  const root = arg.html !== null
    ? new DOMParser().parseFromString(arg.html, 'text/html').body
    : (arg.sel && document.querySelector(arg.sel)) || document.body;
  const live = arg.html === null;
  const clean = s => (s || '').replace(/ /g, ' ').replace(/\s+/g, ' ').trim();
  const textOf = n => clean(live ? n.innerText : n.textContent);
  const SKIP = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'SVG', 'BUTTON', 'NAV', 'FORM', 'IFRAME', 'TEMPLATE', 'SELECT']);
  const BLOCK = /^(P|DIV|SECTION|ARTICLE|UL|OL|TABLE|TBODY|TR|TD|MAIN|HEADER|FOOTER|BLOCKQUOTE|DL|DD|DT|SPAN|FONT|CENTER)$/;
  const out = [];
  const isLeadHeading = n => {  // <p><strong>Minimum requirements</strong></p>
    if (!/^(P|DIV)$/.test(n.tagName) || n.children.length !== 1) return false;
    const c = n.children[0];
    return /^(STRONG|B|U)$/.test(c.tagName) && textOf(n) === textOf(c) && textOf(n).length > 0 && textOf(n).length < 120;
  };
  const walk = el => {
    for (const n of el.childNodes) {
      if (n.nodeType === 3) { const t = clean(n.textContent); if (t) out.push({k: 'p', t}); continue; }
      if (n.nodeType !== 1 || SKIP.has(n.tagName)) continue;
      if (n.hasAttribute('hidden') || /display\s*:\s*none|visibility\s*:\s*hidden/i.test(n.getAttribute('style') || '')) continue;
      if (live) { const cs = getComputedStyle(n); if (cs.display === 'none' || cs.visibility === 'hidden') continue; }
      const tag = n.tagName, t = textOf(n);
      if (!t) continue;
      if (/^H[1-6]$/.test(tag) || isLeadHeading(n)) { out.push({k: 'h', t: t.replace(/:$/, '')}); continue; }
      if (tag === 'LI') {
        if (n.querySelector('li')) { const own = clean([...n.childNodes].filter(c => c.nodeType === 3).map(c => c.textContent).join(' ')); if (own) out.push({k: 'li', t: own}); walk(n); }
        else out.push({k: 'li', t});
        continue;
      }
      if (tag === 'BR') continue;
      if (tag === 'P' && !n.querySelector('p,div,ul,ol,li,h1,h2,h3,h4,h5,h6,table')) {
        // <p><b>Requirements:</b><br>line<br>line</p> → heading + its lines (line structure kept)
        const raw = live ? n.innerText : [...n.childNodes].map(c => c.nodeName === 'BR' ? '\n' : c.textContent).join('');
        const lines = raw.split(/\n+/).map(clean).filter(Boolean);
        const lead = n.firstElementChild, leadText = lead ? clean(lead.textContent) : '';
        const startsBold = lead && /^(STRONG|B|U)$/.test(lead.tagName) && lines.length > 1 && lines[0] === leadText &&
                           clean(n.textContent).startsWith(leadText) && leadText.length < 120;
        lines.forEach((line, i) => out.push({k: i === 0 && startsBold ? 'h' : 'p', t: i === 0 && startsBold ? line.replace(/:$/, '') : line}));
        continue;
      }
      if (BLOCK.test(tag) || n.children.length) walk(n); else out.push({k: 'p', t});
    }
  };
  walk(root);
  // merge consecutive identical blocks (some pages repeat hidden mobile/desktop copies)
  return out.filter((b, i) => i === 0 || b.t !== out[i - 1].t || b.k !== out[i - 1].k);
}
"""


@dataclass
class Block:
    kind: Literal["h", "li", "p"]
    text: str


@dataclass
class Section:
    heading: str
    bullets: list[str] = field(default_factory=list)
    paragraphs: list[str] = field(default_factory=list)

    @property
    def items(self) -> list[str]:
        return self.bullets or self.paragraphs


def blocks(page: Page, *, html: str | None = None, selector: str | None = None) -> list[Block]:
    raw = page.evaluate(_BLOCKS_JS, {"html": html, "sel": selector})
    return [Block(b["k"], b["t"]) for b in raw]


def render(bs: list[Block]) -> str:
    lines: list[str] = []
    for b in bs:
        if b.kind == "h":
            if lines and lines[-1] != "":
                lines.append("")
            lines += [b.text, ""]
        elif b.kind == "li":
            lines.append(f"- {b.text}")
        else:
            lines.append(b.text)
    return "\n".join(lines).strip() + "\n"


def sections(bs: list[Block]) -> list[Section]:
    out = [Section("")]
    for b in bs:
        if b.kind == "h":
            out.append(Section(b.text))
        elif b.kind == "li":
            out[-1].bullets.append(b.text)
        else:
            out[-1].paragraphs.append(b.text)
    return [s for s in out if s.heading or s.items]


# --- attachments --------------------------------------------------------------------------------------------

_ATTACH_HREF = re.compile(r"\.(pdf|docx?)(\?|#|$)", re.I)
_ATTACH_JD = re.compile(r"(?i)(job|position|role)[\s_-]*(description|posting|profile|spec)|\bjd\b|description")
_ATTACH_NOT = re.compile(r"(?i)terms|privacy|policy|eeo|equal[\s_-]*opportunity|benefit|handbook|cookie|accessib")


def attachment_links(page: Page, selector: str | None) -> list[tuple[str, str]]:
    """(absolute URL, link text) of job-description files linked from the posting."""
    links = page.evaluate(
        """(sel) => [...((sel && document.querySelector(sel)) || document).querySelectorAll('a[href]')]
                     .map(a => [a.href, (a.innerText || a.getAttribute('aria-label') || '').trim()])""",
        selector if selector not in (None, "body", "vision") else None,
    )
    out = []
    for href, text in links:
        name = f"{text} {href.rsplit('/', 1)[-1]}"
        if _ATTACH_HREF.search(href) and _ATTACH_JD.search(name) and not _ATTACH_NOT.search(name):
            out.append((href, text or href.rsplit("/", 1)[-1]))
    return list(dict.fromkeys(out))


def attachment_text(data: bytes, url: str) -> str:
    """Text of a PDF/DOCX/DOC as-is (layout kept for PDFs)."""
    if data[:4] == b"%PDF":
        with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
            f.write(data)
            f.flush()
            return subprocess.run(["pdftotext", "-layout", f.name, "-"], capture_output=True, text=True,
                                  timeout=60).stdout.strip()
    if data[:2] == b"PK":  # docx
        xml = zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml").decode("utf-8", "replace")
        xml = re.sub(r"</w:p>", "\n", xml)
        return re.sub(r"<[^>]+>", "", xml).strip()
    return ""  # legacy .doc and others: not converted (reported by the caller)


# --- section classification -----------------------------------------------------------------------------------

Category = Literal["basic_qualifications", "preferred_qualifications", "responsibilities", "values", "benefits",
                   "technical_skills", "role_summary", "company", "compensation", "other"]


class SectionLabel(BaseModel):
    index: int
    category: Category = Field(description="What this section of the job description is")


class SectionLabels(BaseModel):
    labels: list[SectionLabel]


CLASSIFY_PROMPT = """Label each section of a job description by what it contains (judge by the content, not only the
heading — companies use creative headings):
- basic_qualifications: required / minimum qualifications, "what you'll need", "you must have"
- preferred_qualifications: nice-to-have, preferred, bonus, "you might thrive if", "ideal candidate" extras
- responsibilities: duties, what you'll do, the role's day-to-day work
- values: traits, culture, "who you are", what they value (not hard requirements)
- benefits: perks, benefits, what they offer
- technical_skills: a list of technologies / tools / stack
- role_summary: overview of the role in prose; company: about the company; compensation: pay / salary text
- other: anything else (legal/EEO text, how to apply, location notes)"""


def classify_sections(secs: list[Section], log=print) -> dict[int, str]:
    """index → category for each section, via the fast model; heading rules if the model fails."""
    from langchain_core.messages import HumanMessage, SystemMessage

    from jobagent import llm

    lines = []
    for i, s in enumerate(secs):
        sample = " | ".join(s.items[:3])[:300]
        lines.append(f"{i}. heading: {s.heading or '(none)'} — {len(s.bullets)} bullets — sample: {sample}")
    try:
        out = llm.structured(SectionLabels, "fast").invoke(
            [SystemMessage(CLASSIFY_PROMPT), HumanMessage("Sections:\n" + "\n".join(lines))])
        labels = {l.index: l.category for l in out.labels if 0 <= l.index < len(secs)}
        if labels:
            return labels
    except Exception as e:
        log(f"jd sections: model labelling failed ({type(e).__name__}); using heading rules")
    from jobagent.tools.scrape import classify_heading

    return {i: classify_heading(s.heading) or "other" for i, s in enumerate(secs)}
