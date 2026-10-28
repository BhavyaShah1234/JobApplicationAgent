"""Step 10: the local ATS scorer, run on every resume version (free and instant; external sites are slower and
rate-limited). It reads the resume the way an ATS does, from the PDF's extracted text, and scores:

- keywords (55%): the JD's technical skills and the technical terms of its REQUIRED / PREFERRED items, weighted
  (required 2, tech stack 1.5, preferred 1), matched after normalisation ("Node.js" = "nodejs", "CI/CD" = "ci cd");
- parseability (15%): text extracts, email and phone found, no junk glyphs in the header, one page;
- sections (10%): Summary / Experience / Education / Skills headers found;
- title (5%): the posting's title words in the resume;
- semantic (15%): embedding similarity of the resume text and the posting text.

The missing keywords, most important first, are what the loop feeds back into tailoring.
"""

import math
import re
import subprocess
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path

from jobagent.state import JobDescription
from jobagent.tools.tailor import TOOLISH, jd_items

SECTIONS = {"summary": r"summary|profile|objective", "experience": r"experience|employment|work history",
            "education": r"education", "skills": r"skills|technical skills|technologies"}
_GENERIC = {"experience", "ability", "knowledge", "understanding", "strong", "skills", "team", "work", "years",
            "familiarity", "proficiency", "engineering", "development", "systems", "software", "design"}


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower().replace("c++", "cpp").replace("c#", "csharp")
    text = re.sub(r"(?<=\w)\.(?=js\b)", "", text)                 # node.js -> nodejs
    return " " + re.sub(r"[^a-z0-9+#]+", " ", text).strip() + " "


def atomize(term: str) -> list[str]:
    """A tech-stack entry that is really a sentence ("Different backend languages, including Java, Rust, Python
    and Go") -> the names it mentions (["Java", "Rust", "Python", "Go"]): capitalised names of up to three words,
    except an ordinary word that merely starts a sentence ("Different", "Our", "While")."""
    term = term.strip()
    if len(term.split()) <= 4 and "," not in term:
        return [term]
    out = []
    for sentence in re.split(r"(?<=[.;:])\s+", term):
        for m in re.finditer(r"\b[A-Z][\w+#./-]*(?:\s+[A-Z][\w+#./-]*){0,2}", sentence):
            name = m.group(0).rstrip(".")
            starts = not sentence[:m.start()].strip(" ,(")
            in_list = re.match(r"\s*(,|and\b|or\b|/)\s*[A-Z]", sentence[m.end():])  # "Docker and Kubernetes"
            if starts and " " not in name and not TOOLISH.fullmatch(name) and not in_list:
                continue
            if starts and " " in name and not TOOLISH.search(name):
                continue
            out.append(name)
    return list(dict.fromkeys(out))


def keywords(jd: JobDescription) -> dict[str, float]:
    """keyword -> weight. Technical terms only: the JD's tech stack and tool-like terms in its required/preferred
    items (CamelCase / acronyms / tech-stack phrases they contain)."""
    out: dict[str, float] = {}

    def add(term: str, w: float):
        term = term.strip(" .,;:()")
        if len(term) < 2 or term.lower() in _GENERIC:
            return
        out[term] = max(out.get(term, 0.0), w)

    stack = [a for k in jd.technical_skills if k for a in atomize(k)]
    for k in stack:
        add(k, 1.5)
    for _, text, need in jd_items(jd):
        if need == "soft":
            continue
        w = 2.0 if need == "REQUIRED" else 1.0
        for k in stack:
            if norm(k).strip() and norm(k) in norm(text):
                add(k, w)
        for tok in TOOLISH.findall(text):
            add(tok, w)
    return out


@dataclass
class LocalScore:
    score: float
    keyword_pct: float
    missing: list[str] = field(default_factory=list)     # most important first
    matched: list[str] = field(default_factory=list)
    parse_issues: list[str] = field(default_factory=list)
    sections_missing: list[str] = field(default_factory=list)
    title_pct: float = 0.0
    semantic: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def pdf_text(pdf: Path) -> str:
    return subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True).stdout


def header_junk(text: str) -> list[str]:
    """Characters an ATS can't read in the first lines (icon fonts extract as junk like 'ï' or '§')."""
    head = "\n".join(text.strip().splitlines()[:3])
    return sorted({c for c in head if not (c.isascii() and (c.isprintable() or c.isspace())) and c not in "–—’‘“”•·"})


def score(pdf: Path, jd: JobDescription, jd_text: str = "", embed=None) -> LocalScore:
    text = pdf_text(pdf)
    ntext = norm(text)
    kw = keywords(jd)
    hit = {k: w for k, w in kw.items() if norm(k) in ntext}
    keyword_pct = 100.0 * sum(hit.values()) / sum(kw.values()) if kw else 100.0
    missing = [k for k, _ in sorted(((k, w) for k, w in kw.items() if k not in hit), key=lambda x: -x[1])]

    issues = []
    if len(text.strip()) < 200:
        issues.append("almost no text extracts from the PDF")
    if not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text):
        issues.append("no email address found in the text")
    if not re.search(r"\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}", text):
        issues.append("no phone number found in the text")
    if junk := header_junk(text):
        issues.append(f"header extracts with unreadable characters {''.join(junk)!r} (icon font glyphs)")
    pages = int(re.search(r"Pages:\s+(\d+)", subprocess.run(["pdfinfo", str(pdf)], capture_output=True,
                                                             text=True).stdout).group(1))
    if pages != 1:
        issues.append(f"{pages} pages")
    parse_pct = max(0.0, 100.0 - 25.0 * len(issues))

    lines = [l.strip().lower() for l in text.splitlines() if l.strip()]
    missing_sections = [s for s, pat in SECTIONS.items() if not any(re.fullmatch(pat, l) for l in lines)]
    section_pct = 100.0 * (len(SECTIONS) - len(missing_sections)) / len(SECTIONS)

    words = [w for w in norm(jd.title or "").split() if len(w) > 2 and w not in {"and", "the", "for", "new"}]
    title_pct = 100.0 * sum(f" {w} " in ntext for w in words) / len(words) if words else 100.0

    semantic = 0.0
    if jd_text:
        from jobagent import llm
        embed = embed or llm.embeddings()
        a, b = embed.embed_documents([text[:8000], jd_text[:8000]])
        cos = sum(x * y for x, y in zip(a, b)) / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))
        semantic = max(0.0, min(100.0, (cos - 0.3) / 0.5 * 100.0))   # ~0.3 unrelated .. ~0.8 near-identical

    total = (0.55 * keyword_pct + 0.15 * parse_pct + 0.10 * section_pct + 0.05 * title_pct +
             0.15 * (semantic if jd_text else keyword_pct))
    return LocalScore(round(total, 1), round(keyword_pct, 1), missing, sorted(hit), issues, missing_sections,
                      round(title_pct, 1), round(semantic, 1))


def record(job_dir: Path, site: str, rnd: int, score_: float | None, missing: list[str], raw: dict) -> None:
    """One row in ats_runs for this resume version."""
    import hashlib
    import json

    from jobagent import db
    sha = hashlib.sha256((job_dir / "resume.pdf").read_bytes()).hexdigest()
    with (conn := db.connect()):
        conn.execute("INSERT INTO ats_runs (job_dir, site, round, score, missing, raw, resume_sha) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (str(job_dir), site, rnd, score_, json.dumps(missing), json.dumps(raw), sha))
