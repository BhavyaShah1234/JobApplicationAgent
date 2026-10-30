"""Step 9: tailor the resume template to one job (Experience bullets, Summary, Technical Skills).

Kept byte-for-byte: preamble, header, section names, every role subheading, Education. Experience bullets are written
from the JD's duties and technical qualifications (every required one covered by a NEW bullet, 2-4 bullets per
role), then the template's bullets marked
"% backfill" are added where relevant; Summary is written fresh per job and company; Technical Skills are regenerated
from the JD's tech stack (abstract terms with concrete tools). Code checks reject repeats, academic wording, bullets
without a technology or (beyond one) a result, and tools outside a role's dates are steered by the prompt. Estimated
metrics are listed for the user's review. The resume must compile to exactly one page.
"""

import json
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from jobagent import llm
from jobagent.clock import with_today
from jobagent.state import JobDescription


# --- parsing the template ------------------------------------------------------------------------------------

@dataclass
class Role:
    header: str                  # the \resumeSubheading block (locked)
    title: str
    company: str
    bullets: list[str]           # LaTeX source of each \resumeItem{...}
    span: tuple[int, int]        # where the bullet list body sits in the .tex
    dates: str = ""
    pool: list[bool] = field(default_factory=list)  # bullet marked "% backfill" in the template


def eligible(role: "Role", j: int, marked: bool) -> bool:
    """May original bullet j be reused / topped up? Only "% backfill" ones when the template marks any."""
    return (role.pool[j] if marked else True) and not has_command(role.bullets[j])


@dataclass
class Resume:
    tex: str
    summary_span: tuple[int, int]
    summary: str
    roles: list[Role]
    skills_span: tuple[int, int]
    skills: list[tuple[str, str]]  # (category, items text)
    locked: list[str] = field(default_factory=list)


def _braced(tex: str, start: int) -> tuple[str, int]:
    """Content of the {...} group opening at tex[start] == '{', and the index after it."""
    depth, i = 0, start
    while i < len(tex):
        c = tex[i]
        if c == "\\":
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return tex[start + 1:i], i + 1
        i += 1
    raise ValueError("unbalanced braces")


def parse(tex: str) -> Resume:
    s_sum = tex.index("\\section{Summary}") + len("\\section{Summary}")
    e_sum = tex.index("\\section{Experience}")
    summary = tex[s_sum:e_sum].strip()
    exp_end = tex.index("\\section{Education}")
    roles = []
    for m in re.finditer(r"\\resumeSubheading", tex[:exp_end]):
        if m.start() < e_sum:
            continue
        pos = m.end()
        args = []
        for _ in range(4):
            pos = tex.index("{", pos)
            arg, pos = _braced(tex, pos)
            args.append(arg)
        list_start = tex.index("\\resumeItemListStart", pos) + len("\\resumeItemListStart")
        list_end = tex.index("\\resumeItemListEnd", list_start)
        body = tex[list_start:list_end]
        bullets, p = [], 0
        pool = []
        while (k := body.find("\\resumeItem{", p)) != -1:
            content, p = _braced(body, k + len("\\resumeItem"))
            bullets.append(content.strip())
            eol = body.find("\n", p)
            pool.append(bool(re.match(r"\s*%\s*backfill\b", body[p:eol if eol != -1 else len(body)])))
        title = re.sub(r"\\underline\{(.*)\}", r"\1", args[0]).strip()
        roles.append(Role(tex[m.start():pos], title, args[2].strip(), bullets, (list_start, list_end),
                          to_plain(args[1]).strip(), pool))
    s_sk = tex.index("\\section{Technical Skills}")
    skills = [(c.strip(), v.strip()) for c, v in re.findall(r"\\textbf\{([^}]*)\}\{:\s*((?:[^{}]|\{[^{}]*\})*)\}", tex[s_sk:])]
    return Resume(tex, (s_sum, e_sum), summary, roles, (s_sk, len(tex)), skills)


# --- LaTeX text <-> plain text ------------------------------------------------------------------------------------

_UNESC = [("\\%", "%"), ("\\&", "&"), ("\\$", "$"), ("\\#", "#"), ("\\_", "_"), ("--", "–")]


def to_plain(latex: str) -> str:
    out = latex
    for a, b in _UNESC:
        out = out.replace(a, b)
    return out


_KEEP = set("–’‘“”®™°±×→")
# Dashes used as punctuation (em dash, or a spaced en dash / double hyphen) read as AI-written text. Ranges like
# "2025–2026" are not touched.
DASH = re.compile(r"\s*—\s*|\s+(–|--)\s+")


_CHANGE_VERB = (r"(?:cut|cutting|reduc\w*|lift\w*|rais\w*|improv\w*|increas\w*|boost\w*|lower\w*|decreas\w*|"
                r"grow\w*|grew|shrink\w*|slash\w*|sped up|speed\w* up|accelerat\w*|drop\w*|trim\w*|shorten\w*|"
                r"halv\w*|doubl\w*|tripl\w*|uplift\w*|expand\w*|lengthen\w*)")
_NOT_OBJECT = r"(?:by|to|from|than|at|under|below|within|of|with|and|while|for|per|across|in|on|into|over)"
_MISSING_BY = re.compile(rf"\b({_CHANGE_VERB})((?:\s+(?!{_NOT_OBJECT}\b)[\w/&().+-]+){{1,6}}?)\s+"
                         r"((?:about|nearly|roughly|approximately|almost|over|more than)\s+)?(\d+(?:\.\d+)?\s?%)",
                         re.I)


def by_percent(text: str) -> str:
    """'cutting failed refreshes 45%' -> 'cutting failed refreshes by 45%' (a change by a percentage needs 'by')."""
    return _MISSING_BY.sub(lambda m: f"{m.group(1)}{m.group(2)} by {m.group(3) or ''}{m.group(4)}", text)


def no_dashes(text: str) -> str:
    """Last-resort fix for a punctuation dash the model still wrote: a comma (the checks ask for a rewrite first)."""
    return DASH.sub(", ", text)


def to_latex(plain: str) -> str:
    plain = by_percent(no_dashes(plain))
    plain = "".join(c for c in re.sub(r"~\s*(?=\d)", "about ", re.sub(r"\s*(→|->)\s*", " to ", plain)).replace("‑", "-") if ord(c) < 128 or c in _KEEP)
    out = plain.replace("\\", "\\textbackslash{}")
    for ch in "%&$#_":
        out = out.replace(ch, "\\" + ch)
    return out.replace("~", "\\textasciitilde{}").replace("^", "\\textasciicircum{}").replace("–", "--")


def has_command(latex: str) -> bool:
    return bool(re.search(r"\\(href|url|textbf|textit|underline)\b", latex))


# --- helpers -------------------------------------------------------------------------------------------------

def _split_items(text: str) -> list[str]:
    return [t.strip() for t in re.split(r",(?![^{]*\})", text) if t.strip()]


def compile_pdf(tex: str, out_dir: Path, name: str = "resume") -> tuple[int, str]:
    """Write <name>.tex to out_dir, compile it in a temp dir, copy <name>.pdf back. Returns (pages, error); a page
    whose vertical spacing TeX had to squeeze counts as one page more."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{name}.tex").write_text(tex)
    with tempfile.TemporaryDirectory() as tmp:
        # \vbadness=0 makes TeX report a page whose spacing it had to squeeze to fit ("Tight \vbox")
        Path(tmp, f"{name}.tex").write_text(tex.replace("\\begin{document}", "\\vbadness=0\n\\begin{document}", 1))
        run = subprocess.run(["latexmk", "-pdf", "-interaction=nonstopmode", "-halt-on-error", f"{name}.tex"],
                             cwd=tmp, capture_output=True, text=True, timeout=180)
        if run.returncode:
            errs = [l for l in Path(tmp, f"{name}.log").read_text(errors="replace").splitlines() if l.startswith("!")]
            return 0, " | ".join(errs[:3]) or run.stdout[-300:]
        Path(out_dir / f"{name}.pdf").write_bytes(Path(tmp, f"{name}.pdf").read_bytes())
        info = subprocess.run(["pdfinfo", f"{name}.pdf"], cwd=tmp, capture_output=True, text=True).stdout
        squeezed = "Tight \\vbox" in Path(tmp, f"{name}.log").read_text(errors="replace")
    # A squeezed page doesn't really fit: it counts as one page too many, so the caller shortens the content.
    return int(re.search(r"Pages:\s+(\d+)", info).group(1)) + squeezed, ""


# --- Experience bullets from the JD's duties, skills from its tech stack ----------------------------------------

PLACEHOLDER = re.compile(r"\b[XYZN]\s*%|\bby [XYZN]\b|\b[XYZN]x\b")


class FillBullet(BaseModel):
    text: str = Field(max_length=300, description="Plain text, no LaTeX, past tense, aim for <= 200 chars")
    duties: list[int] = Field(min_length=1, max_length=3, description="Indices of the JD ITEMS this bullet covers")
    original: int | None = Field(None, description="Index of this role's original bullet if reused, else null")
    estimated: list[str] = Field(default_factory=list, max_length=3,
                                 description="Each number/metric in this bullet that you estimated (e.g. '22%', '40 s')")


class FillRole(BaseModel):
    role: int = Field(description="Role index as given")
    bullets: list[FillBullet] = Field(min_length=2, max_length=4)


class ToolsFor(BaseModel):
    term: str = Field(max_length=40, description="One abstract term from `terms`, exactly as written there")
    tools: list[str] = Field(min_length=1, max_length=4, description="Concrete current tools implementing it")


class SkillLine(BaseModel):
    category: str = Field(max_length=32, description="Short category name, no parentheses or colons")
    terms: list[str] = Field(min_length=1, max_length=16,
                             description="One skill per array element (never a comma list in one element)")
    tools_for: list[ToolsFor] = Field(default_factory=list, max_length=6,
                                      description="Concrete tools for the abstract terms of this line")

    def render(self) -> list[str]:
        tools = {t.term.strip().lower(): t.tools for t in self.tools_for}
        terms = []
        for raw in self.terms:  # top-level commas only; "Term (a, b)" written inside a term is read as tools_for
            for part in re.split(r",(?![^()]*\))", raw):
                part = part.strip()
                if m := re.fullmatch(r"(.+?)\s*\((.+)\)", part):
                    part = m.group(1).strip()
                    tools.setdefault(part.lower(), [x.strip() for x in m.group(2).split(",") if x.strip()])
                terms.append(part)
        shown = {x.strip().lower() for v in tools.values() for x in v}
        out, seen = [], set()
        for term in terms:
            key = term.lower()
            if key == self.category.strip().lower():  # "AI Tools: AI tools (Copilot)" -> "AI Tools: Copilot"
                out += [t for t in tools.pop(key, []) if t.lower() not in seen]
                seen.add(key)
                continue
            if not term or key in seen or (key in shown and key not in tools):
                continue  # a tool already shown in parentheses is not repeated as a term
            seen.add(key)
            tl = tools.pop(key, None)
            out.append(f"{term} ({', '.join(tl)})" if tl else term)
        return out


class Filled(BaseModel):
    summary: str = Field(max_length=800, description="Plain text, 2-3 complete sentences, <= 450 characters")
    roles: list[FillRole] = Field(min_length=1, max_length=6)
    skills: list[SkillLine] = Field(min_length=3, max_length=8)
    notes: str = Field(max_length=600, description="How duties were distributed across roles and why")


def filled_schema(r: "Resume") -> type[BaseModel]:
    """One fixed slot per role (role_0 = first role ...) so bullets cannot drift to a neighbouring role."""
    from pydantic import create_model
    fields = {"summary": (str, Field(max_length=800, description="Plain text, 2-3 complete sentences, <= 450 characters"))}
    for i, role in enumerate(r.roles):
        fields[f"role_{i}"] = (list[FillBullet], Field(min_length=MIN_BULLETS, max_length=MAX_BULLETS,
                               description=f"Bullets for: {role.title} at {role.company}"))
    fields["skills"] = (list[SkillLine], Field(min_length=3, max_length=8))
    fields["notes"] = (str, Field(max_length=600, description="How duties were distributed across roles and why"))
    return create_model("FilledSlots", **fields)


def to_filled(r: "Resume", out: BaseModel) -> Filled:
    roles = [FillRole(role=i, bullets=getattr(out, f"role_{i}")) for i in range(len(r.roles))]
    return Filled(summary=out.summary, roles=roles, skills=out.skills, notes=out.notes)


FILL_PROMPT = """You write the Experience bullets and the Technical Skills of a resume for one job posting.
The candidate has told us they have done all of the posting's duties and know all of its skills.

Experience bullets:
- Every bullet is a professional, technical accomplishment: action verb + the concrete technical work (named tools,
  techniques, systems) + the result achieved (a metric or a concrete outcome). No bullet about soft skills alone
  (partnering, communicating, presenting, mentoring): items marked "soft skill" need no bullet and no mention at
  all (never tack "with stakeholders" onto a technical bullet just to cover one).
- Write every role as professional industry work. Never use wording that makes it look academic, unpaid, student
  or volunteer work: no "faculty", "professor", "lab", "coursework", "class", "student", "thesis", "volunteer",
  "unpaid", "academic", "research codebase", "research goals". Use "teams", "stakeholders", "production" instead.
- Work in two passes; field role_N is the N-th role listed.
  Pass 1, NEW bullets (`original` null): turn the numbered JD ITEMS (duties AND technical qualifications) into
  bullets describing work the candidate DID (past tense, strong verb, the posting's own keywords kept verbatim for
  ATS). Every item marked REQUIRED must appear in the `duties` of at least one NEW bullet; PREFERRED items should be
  covered where they fit. A bullet may combine up to 3 closely related items; a long item may be shortened to its
  most important keywords. Place each bullet under the role where it reads most plausibly for that employer and
  title, and spread the work across ALL roles; do not dump everything into one role.
  Pass 2, ORIGINAL bullets: only then, where a role still has room (4 bullets at most), add the candidate's
  ORIGINAL bullets that are relevant to this posting (set `original`; keep their facts and numbers, light
  rewording allowed). Originals never count as covering a JD item and never replace a NEW bullet. Skip ones that
  are not relevant; never pad a role to reach a count. Each role ends with 2-4 bullets.
- Weave in tools from the qualifications and technical skills where they fit naturally.
- Metrics: quantify results with believable, conservative numbers in units that fit the duty (e.g. downtime
  -18%, OEE +7 pts, cycle time -12%, changeover 45 -> 20 min, first-pass yield +4%, MTTR -25%, defect escapes -30%,
  15 technicians trained, 6 stations commissioned). Keep them modest and internally consistent; round numbers are
  fine. List every number you estimated in the bullet's `estimated` field (the candidate confirms them). Numbers
  copied from an original bullet are not estimates. Roughly half the bullets carry a metric, never more than two.
- Never repeat yourself across roles: no two bullets may share an opening phrase or say the same thing. If a
  duty appears under two roles, the second bullet must use different wording, a different angle and a different
  tool/result (e.g. one role builds the pipelines, another optimizes or monitors them).
- Respect each role's dates: only name a tool, product or practice under a role if it existed during that role
  (e.g. Microsoft Copilot agents / Copilot Studio agents are 2024+, Microsoft Fabric is late 2023+, LangGraph is
  2024+); otherwise use a contemporary equivalent or move the duty to a later role.
- {max_bullets} bullets in total at most (the resume must fit one page). Keep every bullet to ONE line on the
  page: ~110 characters, never more than 130.

Technical Skills:
- 5-8 short categories that suit THIS job (no parentheses in category names). Cover every technology, method and standard from the posting's
  technical_skills and qualifications (deduplicated; join near-duplicates like "ultrasonic/laser welding").
- `terms` lists the posting's terms, one per array element. An abstract capability never stands alone: add a
  `tools_for` entry naming the concrete, current industry tools that implement it (rendered "Term (tools)"), e.g. "Machine Vision (OpenCV, YOLO, Cognex In-Sight, Keyence CV-X)",
  "Machine Learning (PyTorch, TensorFlow, scikit-learn)", "Real-Time Analytics (Ignition Perspective, InfluxDB,
  Grafana, Kafka)", "PLC (Rockwell Studio 5000, Siemens TIA Portal)", "HMI (FactoryTalk View, WinCC)",
  "Robotics/Cobots (FANUC, ABB RobotStudio, Universal Robots)", "CAD (SolidWorks, AutoCAD)". At most 4 tools each. Standards, methods and named products (PFMEA, IATF 16949,
  Ignition, OPC UA, 5S) need no tools_for entry.
- Prefer the candidate's CURRENT skills as those tools when they fit (e.g. OpenCV, YOLOv8, MediaPipe, PyTorch,
  ONNX/TensorRT for vision/ML; Python, SQL, Docker). Drop current skills that serve nothing in this posting.
- Keep it compact: the whole section should be about 8-9 lines on the page.

Summary: write it FRESH for this job title at this company, 2-3 sentences: open with the posting's own title
wording, then the 2-3 requirements the candidate most clearly meets (posting keywords), then one clause tying the
candidate to the company's business, product or mission (from the COMPANY PROFILE). Do not reuse sentences from the
current summary. Years of experience: follow CANDIDATE FACTS (state the years the posting asks for, overall or in its
domain, up to the declared total; never more).
No pronouns ("he"/"his"). The degree is also in the Education section.

Style everywhere (bullets, summary, skills): never use em dashes (—) or a spaced dash as punctuation; use commas,
semicolons, colons or separate sentences. A change by a percentage takes "by": "cutting failed runs by 45%",
"lifting throughput by 35%" (not "cutting failed runs 45%")."""


SOFT = re.compile(r"(?i)^\W*(partner|communicat|collaborat|mentor|present|coach|liaise|work closely|engage)")
TECHLESS = re.compile(r"(?i)\b(python|sql|spark|azure|aws|git|ci/cd|api|pipeline|model|data|plc|robot|vision|cloud)")


def is_soft(duty: str) -> bool:
    """A people/collaboration duty with no technology in it: never a bullet of its own."""
    return bool(SOFT.search(duty)) and len(TECHLESS.findall(duty)) <= 1


# physical / logistics requirements that are not resume material
NON_SKILL = re.compile(r"(?i)\b(lift|lbs|stationary|sitting|bend|climb|driver.?s licen[cs]e|auto insurance|transportation|"
                      r"high.stress|travel locally|inquir|cabinets)\b")


# Qualifications that are not bullet material: education, authorisation, pay, EEO boilerplate
NOT_BULLET = re.compile(r"(?i)\b(degree|bachelor|master'?s|ph\.?d|b\.?s\.?|m\.?s\.?|pursuing|education level|"
                        r"graduat\w*|gpa|authori[sz]\w*|sponsor\w*|citizen\w*|clearance|relocat\w*|willing\w*|travel|"
                        r"background in|foundations? in|"
                        r"salary|base pay|compensation|usd|annually|equal opportunity|discriminat\w*|accommodation)\b")
# "3+ years of professional ... experience in X" -> "X": the skill stays, the years go
_YEARS = re.compile(r"(?i)^\W*(at least\s+)?\d+\+?\s*(or more\s+)?years?\s+of\s+((non-internship|professional|"
                    r"relevant|industry|hands-on|full)\s+)*")
TECH_WORDS = re.compile(r"(?i)\b(code|coding|software|systems?|backend|back-end|frontend|front-end|full.stack|"
                        r"infrastructure|database|architecture|algorithms?|distributed|programming|debug\w*|kernel|"
                        r"firmware|embedded|hardware|cloud|machine learning|deep learning|ml|ai|test\w*|deploy\w*|"
                        r"production|scal\w+|performance|security|devops|automation|object.oriented|design patterns|"
                        r"data|api|apis|sdk|compiler|network\w*|linux|processor|gpu|cpu)\b")


def is_technical(text: str, jd: JobDescription) -> bool:
    low = text.lower()
    return bool(TOOLISH.search(text) or TECHLESS.search(text) or TECH_WORDS.search(text) or
                any(len(k) > 2 and k.lower() in low for k in jd.technical_skills))


def jd_items(jd: JobDescription) -> list[tuple[str, str, str]]:
    """The JD items Experience bullets are written from: (kind, text, need) with need in REQUIRED / PREFERRED /
    soft. Technical duties and basic qualifications are required, technical preferred ones preferred; soft or
    generic duties ("Adapt quickly") may be folded in or skipped; education, authorisation, pay and physical
    requirements are left out, and "N+ years of" is stripped so the skill behind it stays."""
    items = [("duty", d, "REQUIRED" if is_technical(d, jd) and not is_soft(d) else "soft")
             for d in jd.responsibilities]
    for kind, quals, need in (("basic", jd.basic_qualifications, "REQUIRED"),
                              ("preferred", jd.preferred_qualifications, "PREFERRED")):
        for q in quals:
            q = _YEARS.sub("", q.lstrip("-• ").strip())
            if q and not NON_SKILL.search(q) and not NOT_BULLET.search(q) and is_technical(q, jd):
                items.append((kind, q[0].upper() + q[1:], need))
    return items


def build_fill_prompt(r: Resume, jd: JobDescription, company_md: str, max_bullets: int, feedback: str = "") -> list:
    duties = "\n".join(f"  [{i}] {need} {kind}: {text}" for i, (kind, text, need) in enumerate(jd_items(jd)))
    marked = any(any(x.pool) for x in r.roles)
    roles = []
    for i, role in enumerate(r.roles):
        lines = [f"[{i}] {role.title} — {role.company} ({role.dates})"]
        lines += [f"   ({j}) {to_plain(b)}" for j, b in enumerate(role.bullets) if eligible(role, j, marked)]
        roles.append("\n".join(lines))
    job = {"title": jd.title, "company": jd.company, "role_summary": jd.role_summary,
           "basic_qualifications": [q for q in jd.basic_qualifications if not NON_SKILL.search(q)],
           "preferred_qualifications": jd.preferred_qualifications, "technical_skills": jd.technical_skills}
    text = (f"JD ITEMS (cover every REQUIRED index with NEW bullets):\n{duties}\n\nREST OF THE POSTING:\n"
            f"{json.dumps(job, indent=1, ensure_ascii=False)}\n\nCOMPANY PROFILE:\n{company_md[:2500]}\n\n"
            f"CURRENT SUMMARY:\n{to_plain(r.summary)}\n\nROLES (fixed) AND ORIGINAL BULLETS:\n" + "\n\n".join(roles) +
            "\n\nCURRENT SKILLS:\n" + "\n".join(f"- {c}: {to_plain(v)}" for c, v in r.skills))
    if feedback:
        text += f"\n\nREVISION REQUEST:\n{feedback}"
    return [SystemMessage(with_today(FILL_PROMPT.format(max_bullets=max_bullets))),
            HumanMessage(text + "\n\n" + facts_block())]


def declared_claims() -> dict:
    """The `claims` block of data/master_profile.yaml ({} without one)."""
    import yaml

    from jobagent.config import get_settings
    try:
        return yaml.safe_load((get_settings().data_dir / "master_profile.yaml").read_text()).get("claims") or {}
    except Exception:
        return {}


def facts_block() -> str:
    """What the writer may claim about the candidate, from the user's declared facts."""
    years = declared_claims().get("years_experience")
    lines = ["CANDIDATE FACTS (declared by the candidate):",
             f"- Up to {years}+ years of professional experience may be stated, overall or in any domain the posting "
             "asks for (e.g. 'fraud and abuse analysis')." if years else
             "- Years of experience: only what the current resume states.",
             "- The candidate has every skill and tool the posting names.",
             "- Degrees, certifications, clearances, citizenship or work authorization, employers, job titles and "
             "dates: ONLY as already on the resume; never invent them.",
             "- Never add a city, state, country or other location anywhere."]
    return "\n".join(lines)


def fill_locked_parts(r: Resume) -> list[str]:
    tex = r.tex
    pub = next((i for i in (tex.find("\\section{Projects}"), tex.find("\\section{Publications}")) if i != -1), -1)
    start = pub if pub != -1 else tex.index("\\section{Education}")
    return [tex[:r.summary_span[0]], tex[start:r.skills_span[0]], "\\section{Experience}",
            "\\section{Technical Skills}"] + [role.header for role in r.roles]


def apply_fill(r: Resume, f: Filled) -> tuple[str, list[str]]:
    problems = []
    tex = r.tex
    # skills: the whole category list is regenerated
    s0 = r.skills_span[0]
    i0 = tex.index("\\small{\\item{", s0) + len("\\small{\\item{")
    i1 = tex.index("}}", i0)
    lines = [f"     \\textbf{{{to_latex(s.category)}}}{{: {', '.join(to_latex(x) for x in s.render())}}}" for s in f.skills]
    tex = tex[:i0] + "\n" + " \\\\\n".join(lines) + "\n    " + tex[i1:]
    by_role = {e.role: e for e in f.roles}
    for i in range(len(r.roles) - 1, -1, -1):
        role = r.roles[i]
        edit = by_role.get(i)
        if edit is None:
            problems.append(f"role {i} ({role.title}) got no bullets")
            items = []
        else:
            items = [role.bullets[b.original] if b.original is not None and 0 <= b.original < len(role.bullets)
                     and to_plain(role.bullets[b.original]) == b.text and not has_command(role.bullets[b.original])
                     else to_latex(b.text) for b in edit.bullets]
        body = "\n" + "".join(f"        \\resumeItem{{{x}}}\n" for x in items) + "      "
        tex = tex[:role.span[0]] + body + tex[role.span[1]:]
    # keep the template's wrapper, e.g. \small{\justifying ...}
    m = re.match(r"\\(small|footnotesize|normalsize)\{(\\justifying\s*)?", r.summary)
    body = f"\\{m.group(1)}{{{m.group(2) or ''}{to_latex(f.summary)}}}" if m else to_latex(f.summary)
    tex = tex[:r.summary_span[0]] + "\n" + body + "\n\n" + tex[r.summary_span[1]:]
    for part in fill_locked_parts(r):
        if part not in tex:
            problems.append(f"LOCKED PART CHANGED: {part[:80]!r}")
    return tex, problems


def fill_checks(r: Resume, jd: JobDescription, f: Filled) -> tuple[list[int], list[str], list[str]]:
    """(REQUIRED JD item indices no NEW bullet covers, JD skills found nowhere, invented-number warnings)."""
    covered = {d for e in f.roles for b in e.bullets if b.original is None for d in b.duties}
    uncovered = [i for i, (_, _, need) in enumerate(jd_items(jd)) if need == "REQUIRED" and i not in covered]
    text = " ".join([f.summary] + [b.text for e in f.roles for b in e.bullets] +
                    [x for s in f.skills for x in s.render()]).lower()
    norm = lambda s: re.sub(r"[\s\-‑/]+", " ", s.lower().replace("®", "")).strip()
    missing = [k for k in jd.technical_skills if norm(k) not in norm(text)]
    known = set(re.findall(r"\d+(?:\.\d+)?", to_plain(r.tex) + " " + " ".join(jd.responsibilities + jd.technical_skills +
                                                                              jd.preferred_qualifications)))
    warn = []
    for e in f.roles:
        for b in e.bullets:
            est = " ".join(b.estimated)
            for n in re.findall(r"\d+(?:\.\d+)?", PLACEHOLDER.sub("", b.text)):
                if n not in known and n not in est:  # an estimate the model didn't list: list it for review
                    b.estimated = b.estimated + [n]
                    est += " " + n
    return uncovered, missing, warn


MIN_BULLETS, MAX_BULLETS = 2, 4
def backfill(r: Resume, jd: JobDescription, f: Filled, embed=None) -> list[tuple[int, str, float]]:
    """Top up every role with < MIN_BULLETS bullets with that role's ORIGINAL bullets (verbatim), most JD-relevant
    first by embedding similarity; skips originals already used and near-duplicates of the new bullets.
    Mutates `f`; returns (role, bullet, score) for each one added."""
    import math

    def cos(a, b):
        return sum(x * y for x, y in zip(a, b)) / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))

    marked = any(any(x.pool) for x in r.roles)
    short = [e for e in f.roles if e.role < len(r.roles) and len(e.bullets) < MIN_BULLETS]
    if not short:
        return []
    embed = embed or llm.embeddings()
    job_text = " ".join(jd.responsibilities + jd.basic_qualifications + jd.preferred_qualifications +
                        jd.technical_skills)
    added = []
    for e in short:
        role = r.roles[e.role]
        used = {b.original for b in e.bullets if b.original is not None}
        cands = [(j, to_plain(b)) for j, b in enumerate(role.bullets) if j not in used and eligible(role, j, marked)]
        if not cands:
            continue
        vecs = embed.embed_documents([job_text] + [t for _, t in cands] + [b.text for b in e.bullets])
        jv, cv, nv = vecs[0], vecs[1:1 + len(cands)], vecs[1 + len(cands):]
        ranked = sorted(((cos(jv, v), j, t, v) for (j, t), v in zip(cands, cv)), reverse=True)
        for score, j, t, v in ranked:
            if len(e.bullets) >= MIN_BULLETS:
                break
            if any(cos(v, n) > 0.85 for n in nv):
                continue  # says the same as a bullet already there
            e.bullets.append(FillBullet.model_construct(text=t, duties=[], original=j, estimated=[]))
            added.append((e.role, t, score))
    return added


def _words(t: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", t.lower())


def duplicates(r: Resume, f: Filled) -> list[str]:
    """Pairs of bullets (anywhere in Experience) that repeat each other: a shared 4-word phrase (ignoring very
    common joiners) or > 50% word overlap. Returned as human-readable lines for the model and the report."""
    stop = {"and", "the", "to", "for", "of", "a", "an", "in", "on", "with", "by", "into", "from"}
    items = [(e.role, k, b.text) for e in f.roles for k, b in enumerate(e.bullets)]
    out = []
    for x in range(len(items)):
        for y in range(x + 1, len(items)):
            (ra, ka, ta), (rb, kb, tb) = items[x], items[y]
            wa, wb = _words(ta), _words(tb)
            grams = lambda w: {tuple(w[i:i + 4]) for i in range(len(w) - 3) if not set(w[i:i + 4]) <= stop}
            shared = grams(wa) & grams(wb)
            sa, sb = set(wa) - stop, set(wb) - stop
            overlap = len(sa & sb) / max(1, min(len(sa), len(sb)))
            if shared or overlap > 0.5:
                why = f"shared phrase \"{' '.join(sorted(shared)[0])}\"" if shared else f"{overlap:.0%} same words"
                out.append(f"role_{ra} bullet {ka + 1} and role_{rb} bullet {kb + 1} repeat each other ({why}): "
                           f"\"{ta}\" / \"{tb}\"")
    return out


ACADEMIC = re.compile(r"(?i)\b(faculty|professor|lab|labs|coursework|class|classes|student|students|thesis|volunteer\w*|"
                      r"unpaid|academic|research codebases?|research goals?|course project)\b")
RESULT = re.compile(r"\d|(?i:\b(cut|cutting|reduc\w*|improv\w*|increas\w*|rais\w*|boost\w*|lift\w*|enabl\w*|"
                    r"making|so that|achiev\w*|faster|eliminat\w*|prevent\w*|replac\w*|lower\w*|sav\w*|"
                    r"accelerat\w*|traceable|automat\w*|on-demand|real-time|unattended|doubl\w*|tripl\w*|reach\w*|fix\w*|"
                    r"speed\w*|sped|trusted|scal\w*|shorten\w*|halv\w*)\b)")
TOOLISH = re.compile(r"\b(?:[A-Z][a-z]*[A-Z][A-Za-z0-9]*|[A-Z]{2,}[A-Za-z0-9]*)\b")  # ACT, CLIP, LoRA, PySpark, MuJoCo


def proper_nouns(text: str) -> set[str]:
    """Capitalized words that don't start a sentence: tools, products, employers, places (Pydantic, Jinja2)."""
    out = set()
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        words = re.findall(r"[A-Za-z][\w+#.-]*", sentence)
        out |= {w.rstrip(".") for w in words[1:] if w[0].isupper()}
    return out


MAX_NO_RESULT = 1  # bullets allowed without a stated result, in the whole resume


def _content_words(t: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{4,}", t.lower()))


def moved_from(r: Resume, role: int, text: str) -> int | None:
    """The other role whose template bullet this text is (> 50% word overlap, better than any of its own role's),
    i.e. something the candidate did at another employer placed under the wrong one."""
    w = _content_words(text)

    def best(i: int) -> float:
        return max([len(w & (o := _content_words(to_plain(b)))) / max(1, min(len(w), len(o)))
                    for b in r.roles[i].bullets] or [0.0])
    scores = {i: best(i) for i in range(len(r.roles))}
    other = max((i for i in scores if i != role), key=scores.get, default=None)
    if other is not None and scores[other] > 0.5 and scores[other] > scores.get(role, 0):
        return other
    return None


def quality(r: Resume, jd: JobDescription, f: Filled) -> list[str]:
    """Bullets the user would reject: academic/unpaid wording, no technology named, no result stated."""
    tech = {t.lower() for t in jd.technical_skills}
    tech |= {x.strip().lower() for _, v in r.skills for x in re.split(r"[,()]", to_plain(v)) if len(x.strip()) > 1}
    tech |= {t.lower() for s in f.skills for x in s.tools_for for t in x.tools}
    tech |= {t.strip().lower() for s in f.skills for t in s.terms}
    tech |= {"llm", "llms", "gpu", "gpus", "cuda", "reinforcement learning", "machine learning", "deep learning",
             "computer vision", "neural network", "transformer", "etl", "api", "apis", "rest", "microservices"}
    out, no_result = [], []
    for e in f.roles:
        for k, b in enumerate(e.bullets):
            where = f"role_{e.role} bullet {k + 1} (\"{b.text}\")"
            if DASH.search(b.text):
                out.append(f"{where} uses a dash as punctuation; rewrite with a comma, semicolon or colon")
            if e.role < len(r.roles) and (other := moved_from(r, e.role, b.text)) is not None:
                out.append(f"{where} is the candidate's bullet from {r.roles[other].company}, not from "
                           f"{r.roles[e.role].company}: replace it with a new bullet for this role")
            if m := ACADEMIC.search(b.text):
                out.append(f"{where} uses academic/unpaid wording \"{m.group(0)}\"")
            low = b.text.lower()
            if not TOOLISH.search(b.text) and not proper_nouns(b.text) and not any(re.search(r"(?<![a-z])" + re.escape(t) + r"(?![a-z])", low)
                                                      for t in tech if t):
                out.append(f"{where} names no technology/tool")
            if not RESULT.search(b.text):
                no_result.append(f"{where} states no result")
    if DASH.search(f.summary):
        out.append("the summary uses a dash as punctuation; rewrite with a comma, semicolon or colon")
    return out + (no_result if len(no_result) > MAX_NO_RESULT else [])


# --- repair: rewrite only the flagged bullets -------------------------------------------------------------------

class BulletFix(BaseModel):
    role: int = Field(description="role_N index of the bullet")
    bullet: int = Field(description="1-based position of the bullet in that role")
    text: str = Field(max_length=300, description="The rewritten bullet, plain text, <= 130 characters")
    estimated: list[str] = Field(default_factory=list, max_length=3)


class Repair(BaseModel):
    fixes: list[BulletFix] = Field(min_length=1, max_length=8)


REPAIR_PROMPT = """You fix specific bullets of a tailored resume. Rewrite ONLY the bullets listed under PROBLEMS, each
in place, so that every problem is solved; leave all other bullets alone. Each rewritten bullet must keep covering
the JD items it covered (shown), name concrete technologies, state a result (a believable estimated metric is fine;
list it in `estimated`), stay under ~120 characters, not repeat any other bullet, use no academic wording and no
dashes as punctuation, and describe work done at THAT role's employer (never another employer's work). A change by
a percentage takes "by"."""


def build_repair_prompt(r: Resume, jd: JobDescription, f: Filled, issues: list[str]) -> list:
    items = jd_items(jd)
    lines = []
    for e in f.roles:
        role = r.roles[e.role]
        lines.append(f"role_{e.role}: {role.title} at {role.company} ({role.dates})")
        for k, b in enumerate(e.bullets, 1):
            covers = "; ".join(items[d][1][:80] for d in b.duties if 0 <= d < len(items))
            lines.append(f"  {k}. {b.text}" + (f"   [covers: {covers}]" if covers and b.original is None else ""))
    return [SystemMessage(with_today(REPAIR_PROMPT)),
            HumanMessage("RESUME BULLETS:\n" + "\n".join(lines) + "\n\nPROBLEMS:\n" + "\n".join(issues))]


def apply_repair(f: Filled, repair: Repair) -> int:
    """Replace the fixed bullets in place (a rewritten original becomes a new bullet). Returns how many applied."""
    by_role = {e.role: e for e in f.roles}
    done = 0
    for fx in repair.fixes:
        e = by_role.get(fx.role)
        if e is None or not 1 <= fx.bullet <= len(e.bullets):
            continue
        b = e.bullets[fx.bullet - 1]
        b.text, b.estimated, b.original = fx.text, list(fx.estimated), None
        done += 1
    return done


# --- ATS keyword pass (step 10): add missing keywords without breaking the page or the rules --------------------

class SkillAdd(BaseModel):
    category: str = Field(max_length=40, description="An existing category name exactly, or a new short one")
    terms: list[str] = Field(min_length=1, max_length=6, description="ONLY the new keywords to add to this category")


class KeywordPass(BaseModel):
    skills: list[SkillAdd] = Field(default_factory=list, max_length=4)
    bullets: list[BulletFix] = Field(default_factory=list, max_length=6)
    summary: str = Field("", max_length=800, description="The rewritten Summary (plain text, 2-3 sentences, <= 450 "
                                                          "characters), or empty to keep it")


KEYWORD_PROMPT = """ATS scans of this tailored resume found the posting's keywords listed under MISSING, and the checkers
gave the suggestions under ADVICE. The goal is an average ATS score above 90: work in EVERY missing item and follow
EVERY piece of advice, within the CANDIDATE FACTS:
- Prefer adding a keyword to the most fitting existing Technical Skills category (`skills`): list ONLY the new
  keywords in `terms`, never the skills already there; open a new short category only if none fits (at most 8).
- Where a keyword names work rather than a tool (a practice, a system, a domain), rewrite ONE existing bullet that
  fits it (`bullets`: role_N index and 1-based position), keeping that bullet's numbers, coverage and length
  (<= ~120 characters). Never move work to another employer.
- A MISSING entry may be a phrase about work ("leading small projects independently", "navigating large code
  bases") or a requirement ("2 years of fraud analysis"): work it into the Summary or a bullet in the posting's
  own words (years only within CANDIDATE FACTS).
- A MISSING item that names a skill already in Technical Skills ("experience with Kafka", "Terraform") means the
  checker wants to see it used in the work: put it in a bullet, not in the skills again. Rewrite at least 2
  bullets every round unless every MISSING item already appears in a bullet.
- LAST ROUND (if given) says what your previous answer did to the scores: build on what was kept, try something
  different from what was undone, and never resend changes that were already in place.
- Soft skills and qualities ("collaboration", "comfort with ambiguity") never go in Technical Skills: show them in
  the Summary or in what a bullet's work involved ("partnered with security and product teams to ...").
- Follow the ADVICE (vary repeated words, add a result to a line without one, name the teams the work involved,
  state the experience the posting asks for, open the Summary with the posting's exact title). Rewrite the Summary
  (`summary`) whenever that helps; keep it 2-3 sentences, <= 450 characters.
- Every keyword under KEEP is already matched by the checkers: a rewritten bullet or Summary must still contain
  each KEEP word it contained before. Never make a bullet or the Summary longer than it was (checkers penalise
  long sentences); swap words in rather than append clauses.
- Use the posting's exact spelling. Skip only words that are page boilerplate (salary, posted, applicants, etc).
- Never invent degrees, certifications, clearances, citizenship or work authorization, employers, job titles or
  dates, and never add a location.
- No dashes as punctuation; a change by a percentage takes "by"."""


def build_keyword_prompt(r: Resume, jd: JobDescription, f: Filled, missing: list[str],
                         advice: list[str] = (), keep: list[str] = (), last: str = "") -> list:
    lines = []
    for e in f.roles:
        role = r.roles[e.role]
        lines.append(f"role_{e.role}: {role.title} at {role.company} ({role.dates})")
        lines += [f"  {k}. {b.text}" for k, b in enumerate(e.bullets, 1)]
    skills = "\n".join(f"- {s.category}: {', '.join(s.render())}" for s in f.skills)
    context = {k: [t for _, t, _ in jd_items(jd) if k.lower() in t.lower()][:1] for k in missing}
    miss = "\n".join(f"- {k}" + (f"   (posting: {c[0][:120]})" if c else "") for k, c in context.items())
    tips = "\n".join(f"- {a}" for a in advice) or "(none)"
    return [SystemMessage(with_today(KEYWORD_PROMPT)),
            HumanMessage(f"{facts_block()}\n\n" + (f"LAST ROUND: {last}\n\n" if last else "") + f"MISSING:\n{miss}\n\nADVICE:\n{tips}\n\nKEEP:\n{', '.join(keep) or '(none)'}\n\nSUMMARY:\n{f.summary}\n\nTECHNICAL SKILLS:\n{skills}"
                         f"\n\nEXPERIENCE BULLETS:\n" + "\n".join(lines))]


# Plain English that ATS checkers report as "missing keywords" but that is never a skill on a resume
GENERIC_WORDS = set("""build builds building tool tools information info value values product products problem problems
design designs work works working management manage people person communication communications team teams user users
world range salary location locations posted updated employment job jobs role roles experience growth economic
critical thinking understanding think small large business businesses company companies opportunity opportunities
customer customers solution solutions process processes quality support service services system systems data
technology technologies skills skill ability knowledge strong excellent new great best help impact results result
develop development developer engineer engineering software project projects time year years day days""".split())


def is_generic(term: str) -> bool:
    return all(w in GENERIC_WORDS for w in re.findall(r"[a-z]+", term.lower())) if term.strip() else True


_FILLER_CATEGORY = re.compile(r"(?i)^\s*(technical\s+)?skills?\s*$|^\s*(other|misc\w*|additional|general|keywords?)\s*$")


def posting_spelling(term: str, jd_text: str) -> str:
    """The posting's own capitalisation of a term ("rust" -> "Rust", "aws" -> "AWS") when it appears there."""
    m = re.search(rf"(?<![\w-]){re.escape(term)}(?![\w-])", jd_text, re.I) if jd_text else None
    return m.group(0) if m and (m.group(0) != m.group(0).lower() or term == term.lower()) else term


def covered_by(term: str, phrases: list[str]) -> bool:
    """True when every word of `term` is in one of `phrases` ("fraud remediation" vs "fraud detection and
    remediation", "backend systems" vs the category "Backend and Data Systems"). Multi-word terms only."""
    words = set(re.findall(r"[a-z0-9+#.]+", term.lower()))
    # A one-word tool stays even inside a longer phrase ("Azure" next to "Azure DevOps CI/CD")
    return len(words) >= 2 and any(words <= set(re.findall(r"[a-z0-9+#.]+", p.lower())) for p in phrases)


def apply_keyword_pass(f: Filled, kp: KeywordPass, jd_text: str = "") -> Filled:
    """A copy of `f` with the keywords added: each skill once (never one already in any category), in the posting's
    spelling, in an existing category or a real new one (no "Technical Skills" / "Other" filler); bullets rewritten
    in place. Generic words never become skills."""
    g = f.model_copy(deep=True)
    have = {t.lower() for line in g.skills for t in line.render()}
    for add in kp.skills:
        terms = []
        for t in (posting_spelling(x.strip(), jd_text) for x in add.terms):
            if t and t.lower() not in have and not is_generic(t):
                terms.append(t)
                have.add(t.lower())
        line = next((s for s in g.skills if s.category.strip().lower() == add.category.strip().lower()), None)
        if line is not None:  # nothing the category name or one existing term already says
            terms = [t for t in terms if not covered_by(t, [line.category] + line.render())]
        if not terms:
            continue
        if line is None and (_FILLER_CATEGORY.match(add.category) or len(g.skills) >= 8):
            continue  # no filler category; no room for a ninth
        if line is None:
            g.skills.append(SkillLine(category=add.category, terms=terms))
        else:
            line.terms = line.terms + terms
    by_role = {e.role: e for e in g.roles}
    for fx in kp.bullets:
        e = by_role.get(fx.role)
        if e and 1 <= fx.bullet <= len(e.bullets):
            e.bullets[fx.bullet - 1].text = fx.text
    if 80 <= len(kp.summary.strip()) <= 500:   # an empty or runaway rewrite keeps the current Summary
        g.summary = kp.summary.strip()
    return g
