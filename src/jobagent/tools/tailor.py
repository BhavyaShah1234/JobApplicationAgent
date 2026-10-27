"""Step 9: tailor the resume template to one job (Experience bullets, Summary, Technical Skills).

Kept byte-for-byte: preamble, header, section names, every role subheading, Education. Experience bullets are written
from the JD's duties (all non-soft duties covered, 2-4 bullets per role), then the template's bullets marked
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


_KEEP = set("–—’‘“”®™°±×→")


def to_latex(plain: str) -> str:
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
    """Write <name>.tex to out_dir, compile it in a temp dir, copy <name>.pdf back. Returns (pages, error)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{name}.tex").write_text(tex)
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, f"{name}.tex").write_text(tex)
        run = subprocess.run(["latexmk", "-pdf", "-interaction=nonstopmode", "-halt-on-error", f"{name}.tex"],
                             cwd=tmp, capture_output=True, text=True, timeout=180)
        if run.returncode:
            errs = [l for l in Path(tmp, f"{name}.log").read_text(errors="replace").splitlines() if l.startswith("!")]
            return 0, " | ".join(errs[:3]) or run.stdout[-300:]
        Path(out_dir / f"{name}.pdf").write_bytes(Path(tmp, f"{name}.pdf").read_bytes())
        info = subprocess.run(["pdfinfo", f"{name}.pdf"], cwd=tmp, capture_output=True, text=True).stdout
    return int(re.search(r"Pages:\s+(\d+)", info).group(1)), ""


# --- Experience bullets from the JD's duties, skills from its tech stack ----------------------------------------

PLACEHOLDER = re.compile(r"\b[XYZN]\s*%|\bby [XYZN]\b|\b[XYZN]x\b")


class FillBullet(BaseModel):
    text: str = Field(max_length=300, description="Plain text, no LaTeX, past tense, aim for <= 200 chars")
    duties: list[int] = Field(min_length=1, max_length=3, description="Indices of the JD duties this bullet covers")
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
        shown = {x.strip().lower() for t in self.tools_for for x in t.tools}
        out, seen = [], set()
        for term in (x.strip() for t in self.terms for x in t.split(",")):
            key = term.lower()
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
  (partnering, communicating, presenting, mentoring): fold such duties into a technical bullet as a short clause
  or skip them; duties marked "soft skill" need no bullet and no mention at all (never tack "with stakeholders"
  onto a technical bullet just to cover one).
- Write every role as professional industry work. Never use wording that makes it look academic, unpaid, student
  or volunteer work: no "faculty", "professor", "lab", "coursework", "class", "student", "thesis", "volunteer",
  "unpaid", "academic", "research codebase", "research goals". Use "teams", "stakeholders", "production" instead.
- Turn EVERY other numbered JD duty into resume bullets: reworded so it reads naturally as work the candidate DID
  (past tense, strong verb, the posting's own keywords kept verbatim for ATS: e.g. "PLC/HMI", "MES/SCADA",
  "PFMEA", "IQ/OQ/PQ"). Every duty index must appear in at least one bullet's `duties`; a bullet may combine up
  to 3 closely related duties, and a long duty may be shortened to its most important keywords.
- Work in two passes. Pass 1: distribute the duties across the roles (field role_N is the N-th role listed),
  each under the role where it fits best. Pass 2: for each role, add any of its ORIGINAL bullets that are
  relevant to this posting (see below). Keep whatever count that gives, 2-4 bullets per role., each placed under the role where it reads
  most plausibly for that employer and title (e.g. plant/controls work under an industrial automation company,
  robotics/vision/ML under a robotics lab, data/integration/software under a software platform role, docs/
  training/mentoring and analytics anywhere it fits). Mix it up; do not dump all duties into one role.
- Weave in tools from the basic/preferred qualifications where they fit naturally (e.g. Ignition, OPC UA,
  structured text, EPLAN, 8D/A3, NFPA 79).
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
- The candidate's ORIGINAL bullets are listed per role. In pass 2 add one (set `original`, keep its facts and
  numbers, light rewording allowed) when it is relevant to this posting: it matches a duty or qualification or
  names a required technology. Skip ones that are not relevant; never pad a role just to reach a count.
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
current summary; take only facts from it (degree, years of experience; never claim more years than it states).
No pronouns ("he"/"his"). The degree is also in the Education section."""


SOFT = re.compile(r"(?i)^\W*(partner|communicat|collaborat|mentor|present|coach|liaise|work closely|engage)")
TECHLESS = re.compile(r"(?i)\b(python|sql|spark|azure|aws|git|ci/cd|api|pipeline|model|data|plc|robot|vision|cloud)")


def is_soft(duty: str) -> bool:
    """A people/collaboration duty with no technology in it: never a bullet of its own."""
    return bool(SOFT.search(duty)) and len(TECHLESS.findall(duty)) <= 1


# physical / logistics requirements that are not resume material
NON_SKILL = re.compile(r"(?i)\b(lift|lbs|stationary|sitting|bend|climb|driver.?s licen[cs]e|auto insurance|transportation|"
                      r"high.stress|travel locally|inquir|cabinets)\b")


def build_fill_prompt(r: Resume, jd: JobDescription, company_md: str, max_bullets: int, feedback: str = "") -> list:
    duties = "\n".join(f"  [{i}] {d}" + ("   <- soft skill: fold into a technical bullet as a clause, or skip"
                                         if is_soft(d) else "") for i, d in enumerate(jd.responsibilities))
    marked = any(any(x.pool) for x in r.roles)
    roles = []
    for i, role in enumerate(r.roles):
        lines = [f"[{i}] {role.title} — {role.company} ({role.dates})"]
        lines += [f"   ({j}) {to_plain(b)}" for j, b in enumerate(role.bullets) if eligible(role, j, marked)]
        roles.append("\n".join(lines))
    job = {"title": jd.title, "company": jd.company, "role_summary": jd.role_summary,
           "basic_qualifications": [q for q in jd.basic_qualifications if not NON_SKILL.search(q)],
           "preferred_qualifications": jd.preferred_qualifications, "technical_skills": jd.technical_skills}
    text = (f"JD DUTIES (cover every index):\n{duties}\n\nREST OF THE POSTING:\n"
            f"{json.dumps(job, indent=1, ensure_ascii=False)}\n\nCOMPANY PROFILE:\n{company_md[:2500]}\n\n"
            f"CURRENT SUMMARY:\n{to_plain(r.summary)}\n\nROLES (fixed) AND ORIGINAL BULLETS:\n" + "\n\n".join(roles) +
            "\n\nCURRENT SKILLS:\n" + "\n".join(f"- {c}: {to_plain(v)}" for c, v in r.skills))
    if feedback:
        text += f"\n\nREVISION REQUEST:\n{feedback}"
    return [SystemMessage(FILL_PROMPT.format(max_bullets=max_bullets)), HumanMessage(text)]


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
    m = re.match(r"\\(small|footnotesize|normalsize)\{", r.summary)  # keep the template's size wrapper
    body = f"\\{m.group(1)}{{{to_latex(f.summary)}}}" if m else to_latex(f.summary)
    tex = tex[:r.summary_span[0]] + "\n" + body + "\n\n" + tex[r.summary_span[1]:]
    for part in fill_locked_parts(r):
        if part not in tex:
            problems.append(f"LOCKED PART CHANGED: {part[:80]!r}")
    return tex, problems


def fill_checks(r: Resume, jd: JobDescription, f: Filled) -> tuple[list[int], list[str], list[str]]:
    """(uncovered duty indices, JD skills found nowhere in the resume, invented-number warnings)."""
    covered = {d for e in f.roles for b in e.bullets for d in b.duties}
    uncovered = [i for i, d in enumerate(jd.responsibilities) if i not in covered and not is_soft(d)]
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


MAX_NO_RESULT = 1  # bullets allowed without a stated result, in the whole resume


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
            if m := ACADEMIC.search(b.text):
                out.append(f"{where} uses academic/unpaid wording \"{m.group(0)}\"")
            low = b.text.lower()
            if not TOOLISH.search(b.text) and not any(re.search(r"(?<![a-z])" + re.escape(t) + r"(?![a-z])", low)
                                                      for t in tech if t):
                out.append(f"{where} names no technology/tool")
            if not RESULT.search(b.text):
                no_result.append(f"{where} states no result")
    return out + (no_result if len(no_result) > MAX_NO_RESULT else [])
