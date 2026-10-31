"""Layout validator: checks the rendered resume the way a reader sees it, which the LaTeX source can't show.

Code checks (from the PDF's word boxes and the free space LaTeX logs, see tailor.compile_pdf):
- more than one page; a blank band at the bottom (the page should be filled);
- a bullet whose last line holds only a word or three (a wasted line);
- larger gaps between bullets than within them;
- a Technical Skills category whose line is mostly empty;
- a project description of a single line.
Visual check (`review`): a Voyager vision model looks at the page image with an explicit checklist for what only
a reader notices (overlaps, cut-off or odd characters, misaligned dates, uneven spacing, empty areas).
"""

import base64
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

HEADINGS = re.compile(r"(?i)^(summary|experience|projects|education|technical skills|skills|publications)$")
BLANK_PT = 36          # more free height than this at the bottom (about 3 lines of small text) = page not filled
ORPHAN_FRAC = 0.2      # a bullet's last line narrower than this share of the text width = a wasted line
SKILL_FILL = 0.6       # a skills category whose (last) line ends before this share of the width = mostly empty


@dataclass
class Row:
    y0: float
    y1: float
    x0: float
    x1: float
    text: str


@dataclass
class Layout:
    pages: int
    free_pt: float | None          # free height LaTeX left at the bottom of the last page
    issues: list[str] = field(default_factory=list)
    short_skills: list[str] = field(default_factory=list)
    one_line_projects: list[str] = field(default_factory=list)


def rows(pdf: Path) -> list[Row]:
    """Page 1's words grouped into visual rows (pdftotext splits a row at wide gaps, e.g. title and date)."""
    xml = subprocess.run(["pdftotext", "-bbox", "-f", "1", "-l", "1", str(pdf), "-"], capture_output=True,
                         text=True).stdout
    words = [(float(a), float(b), float(c), float(d), html_unescape(w)) for a, b, c, d, w in re.findall(
        r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>', xml)]
    out: list[Row] = []
    for x0, y0, x1, y1, w in sorted(words, key=lambda t: (round(t[3]), t[0])):
        if out and abs(out[-1].y1 - y1) <= 2.5:
            r = out[-1]
            r.x0, r.x1, r.y0, r.text = min(r.x0, x0), max(r.x1, x1), min(r.y0, y0), r.text + " " + w
        else:
            out.append(Row(y0, y1, x0, x1, w))
    return out


def html_unescape(s: str) -> str:
    import html
    return html.unescape(s)


def sections(rs: list[Row]) -> dict[str, list[Row]]:
    out, cur = {}, None
    for r in rs:
        if HEADINGS.match(r.text.strip()):
            cur = r.text.strip().lower()
            out[cur] = []
        elif cur:
            out[cur].append(r)
    return out


def page_count(pdf: Path) -> int:
    info = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True).stdout
    m = re.search(r"Pages:\s+(\d+)", info)
    return int(m.group(1)) if m else 0


def check(pdf: Path) -> Layout:
    """Code checks on the compiled resume (reads <name>.layout.json written by compile_pdf for the free space)."""
    pdf = Path(pdf)
    meta = pdf.with_suffix(".layout.json")
    free = json.loads(meta.read_text()).get("free_pt") if meta.exists() else None
    lay = Layout(page_count(pdf), free)
    if lay.pages != 1:
        lay.issues.append(f"the resume is {lay.pages} pages; it must be exactly one")
        return lay
    rs = rows(pdf)
    body = [r for r in rs[2:]]
    left, right = min(r.x0 for r in body), max(r.x1 for r in body)
    width = right - left
    if free is not None and free > BLANK_PT:
        lines = round(free / 11)
        lay.issues.append(f"about {lines} lines of the page are blank at the bottom: fill them (full 3-sentence "
                          "project descriptions, one more bullet where a role has room, or more skills)")
    sec = sections(rs)
    exp = sec.get("experience", [])
    # bullets: a row starting with the bullet glyph opens a bullet; following rows until the next one continue it
    bullets, gaps_in, gaps_between = [], [], []
    for prev, r in zip(exp, exp[1:]):
        gap = r.y0 - prev.y1
        if r.text.startswith("•"):
            if prev.text.startswith("•") or bullets:
                gaps_between.append(gap)
        elif bullets is not None and not re.search(r"\d{4}|Present", r.text):
            gaps_in.append(gap)
    cur = None
    for r in exp:
        if r.text.startswith("•"):
            if cur:
                bullets.append(cur)
            cur = [r]
        elif cur and not re.search(r"(19|20)\d{2}\s*$|Present\s*$", r.text) and r.x0 > left + 2:
            cur.append(r)
        elif cur:
            bullets.append(cur)
            cur = None
    if cur:
        bullets.append(cur)
    for b in bullets:
        last = b[-1]
        if len(b) > 1 and (last.x1 - left) / width < ORPHAN_FRAC:
            lay.issues.append(f'bullet "{b[0].text[2:60]}..." wraps onto a line holding only "{last.text}": shorten it '
                              "to one line or extend it to fill the second line")
    if gaps_in and gaps_between and max(gaps_between) > sorted(gaps_in)[len(gaps_in) // 2] + 4:
        lay.issues.append("the gaps between some bullets are much larger than the line spacing (uneven spacing)")
    # skills: a row with "Category:" starts a category; its last row should run most of the width
    skills = sec.get("technical skills") or sec.get("skills") or []
    cats: list[list[Row]] = []
    for r in skills:
        if re.match(r"^[^:]{2,45}:", r.text):
            cats.append([r])
        elif cats:
            cats[-1].append(r)
    for c in cats:
        fill = (c[-1].x1 - left) / width
        if fill < SKILL_FILL:
            name = c[0].text.split(":")[0]
            lay.short_skills.append(name)
            lay.issues.append(f'Technical Skills "{name}" fills only {fill:.0%} of its last line: add relevant skills')
    # projects: a heading row has "Title | stack ... Month Year"; the rows after it are the description
    projs: list[list[Row]] = []
    for r in sec.get("projects", []):
        if "|" in r.text and re.search(r"(19|20)\d{2}\s*$", r.text):
            projs.append([r])
        elif projs:
            projs[-1].append(r)
    for p in projs:
        if len(p) - 1 < 2:
            title = p[0].text.split("|")[0].strip()
            lay.one_line_projects.append(title)
            lay.issues.append(f'the project "{title}" has a {len(p) - 1}-line description: write the full 3-sentence '
                              "paragraph (problem, system, result)")
    return lay


# --- visual review by a vision-language model ----------------------------------------------------------------

class VisualIssue(BaseModel):
    kind: Literal["overflow", "blank_space", "spacing", "skills_space", "short_project", "orphan_words",
                  "odd_characters", "alignment", "overlap", "other"]
    where: str = Field(max_length=120, description="Section and the first words of the affected line")
    detail: str = Field(max_length=300)


class VisualReview(BaseModel):
    issues: list[VisualIssue] = Field(default_factory=list, max_length=12)


REVIEW_PROMPT = """You are checking the LAYOUT of a one-page resume image, as a recruiter would see it. Report only what you
can SEE in the image; do not judge the content or wording. Check each item and report every real problem:
1. overflow: text cut off at an edge, or content clearly missing at the bottom (it should be one full page).
2. blank_space: an empty band at the bottom of the page taller than about 3 lines, or large empty areas elsewhere.
3. spacing: gaps between bullets, entries or sections that are clearly uneven or much larger than elsewhere.
4. skills_space: Technical Skills lines that are mostly empty (a category whose line ends before the middle).
5. short_project: a project with only a one-line description under its heading.
6. orphan_words: a bullet or paragraph whose last line holds only one to three words.
7. odd_characters: boxes, question marks or wrong symbols (e.g. "¡" or "¿" where "<" or ">" belongs, "Ö" for "×").
8. alignment: dates or locations not lined up at the right edge, or headings misaligned.
9. overlap: text overlapping other text or lines.
Return an empty list if the layout is clean. Be specific in `where` (section + first words of the line)."""


def page_png(pdf: Path, dpi: int = 110) -> bytes:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["pdftoppm", "-r", str(dpi), "-png", "-singlefile", "-f", "1", "-l", "1", str(pdf),
                        f"{tmp}/page"], check=True)
        return Path(tmp, "page.png").read_bytes()


def review(pdf: Path, model: str | None = None, log=None) -> list[str]:
    """Vision-model layout review of page 1 (settings.model_vision by default); [] if the model is unavailable."""
    from jobagent import llm
    from jobagent.config import get_settings
    name = model or get_settings().model_vision
    img = base64.b64encode(page_png(pdf)).decode()
    msgs = [SystemMessage(REVIEW_PROMPT),
            HumanMessage(content=[{"type": "text", "text": "The resume page:"},
                                  {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img}"}}])]
    try:
        out = llm.structured_invoke(llm.writer(3000, timeout=300, model=name), VisualReview, msgs, log=log)
    except Exception as e:
        if log:
            log(f"layout review ({name}) unavailable: {type(e).__name__}")
        return []
    return [f"{i.kind} at {i.where}: {i.detail}" for i in out.issues]
