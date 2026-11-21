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
from pydantic import BaseModel, Field, model_validator

from jobagent import llm
from jobagent.clock import with_today
from jobagent.state import JobDescription


# --- parsing the template ------------------------------------------------------------------------------------

@dataclass
class Role:
    """One job in the resume template: its locked heading, title, company, dates, and the LaTeX of each bullet, with where the bullet list sits in the file and which bullets the template marks `% backfill` (reusable to top up the section)."""
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
    """The parsed resume template: its source, the Summary, the roles, the Technical Skills lines (category, items), and the spans the tailor may rewrite. `forbidden` holds related skills this job's candidate does not have; they are never rendered."""
    tex: str
    summary_span: tuple[int, int]
    summary: str
    roles: list[Role]
    skills_span: tuple[int, int]
    skills: list[tuple[str, str]]  # (category, items text)
    locked: list[str] = field(default_factory=list)
    forbidden: set = field(default_factory=set)   # related skills this job's candidate does not have (skill graph): never rendered


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
    """Parse the resume template into a `Resume`.

    Args:
        tex: The template's LaTeX source (needs the Summary, Experience, Education and Technical Skills sections).

    Returns:
        The `Resume` with its roles, skills lines and editable spans.

    Raises:
        ValueError: When a required section is missing.
    """
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
_SUPER = str.maketrans('⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾', '0123456789+-=()')
_SUB = str.maketrans('₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎', '0123456789+-=()')
_MATH = {'×':r'$\times$', '±':r'$\pm$', '≤':r'$\leq$', '≥':r'$\geq$',
         '≈':r'$\approx$', '≠':r'$\neq$', 'µ':r'$\mu$', 'μ':r'$\mu$'}


def to_plain(latex: str) -> str:
    r"""Turn LaTeX-escaped text (`\%`, `\&`, ...) back into plain text."""
    out = latex
    for command,symbol in {command:symbol for symbol,command in _MATH.items()}.items():
        out=out.replace(command,symbol)
    for command, table in (('textsuperscript', _SUPER), ('textsubscript', _SUB)):
        reverse = {value: chr(key) for key, value in table.items()}
        out = re.sub(r'\\' + command + r'\{([0-9+\-=()]+)\}',
                     lambda match: match.group(1).translate(str.maketrans(reverse)), out)
    for a, b in _UNESC:
        out = out.replace(a, b)
    return out


def summary_text(latex: str) -> str:
    """Unwrap the template's summary formatting before using it as plain-text fallback content."""
    text = latex.strip()
    wrapper = re.match(r"\\(?:small|footnotesize|normalsize)\s*\{", text)
    if wrapper:
        body, end = _braced(text, wrapper.end() - 1)
        if not text[end:].strip():
            text = body
    text = re.sub(r"^\\justifying\b\s*", "", text)
    return to_plain(text).strip()


_KEEP = set("–’‘“”®™°→") | set(_MATH) | set(map(chr, _SUPER)) | set(map(chr, _SUB))
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
    """Make plain text safe for the resume's LaTeX: escape special characters, replace arrows and non-ASCII punctuation, and use commands that print correctly in the template's font encoding."""
    plain = by_percent(no_dashes(plain))
    plain = "".join(c for c in re.sub(r"~\s*(?=\d)", "about ", re.sub(r"\s*(→|->)\s*", " to ", plain)).replace("‑", "-") if ord(c) < 128 or c in _KEEP)
    # Escape input in one pass, before inserting any commands of our own.
    # In particular, braces in JSON/code examples are text, not TeX groups.
    escapes = {c: "\\" + c for c in "%&$#_{}"}
    escapes["\\"] = "\\textbackslash{}"
    out = "".join(escapes.get(c, c) for c in plain)
    # The template's default (OT1) font encoding prints a bare < > as ¡ ¿ and × as Ö; use commands that work in any
    out = out.replace("<", "\\textless{}").replace(">", "\\textgreater{}")
    for symbol,command in _MATH.items():out=out.replace(symbol,command)
    out = out.replace("~", "\\textasciitilde{}").replace("^", "\\textasciicircum{}").replace("–", "--")
    # OT1 cannot print these Unicode glyphs. Dropping them changes facts such as
    # R² into R, or CO₂ into CO. Insert only our own commands after escaping.
    for command, table in (('textsuperscript', _SUPER), ('textsubscript', _SUB)):
        out = re.sub('[' + ''.join(map(chr, table)) + ']+',
                     lambda match: '\\' + command + '{' + match.group().translate(table) + '}', out)
    return out


def has_command(latex: str) -> bool:
    r"""True when a LaTeX string contains a link or formatting command (`\href`, `\url`, `\textbf`, `\textit`, `\underline`) that must be kept."""
    return bool(re.search(r"\\(href|url|textbf|textit|underline)\b", latex))


# --- helpers -------------------------------------------------------------------------------------------------

def _split_items(text: str) -> list[str]:
    return [t.strip() for t in re.split(r",(?![^{]*\})", text) if t.strip()]


PDF_STARTUP_POLICY = 1


def compile_pdf(tex: str, out_dir: Path, name: str = "resume") -> tuple[int, str]:
    """Write <name>.tex to out_dir, compile it in a temp dir, copy <name>.pdf back. Returns (pages, error); a page
    whose vertical spacing TeX had to squeeze counts as one page more."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{name}.tex").write_text(tex)
    # A failed attempt must not leave the PDF or measurements of another
    # attempt beside its new .tex. Kept versions are restored by bundle.publish.
    for suffix in ("pdf", "layout.json"):
        (out_dir / f"{name}.{suffix}").unlink(missing_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        # \vbadness=0 makes TeX report a page whose spacing it had to squeeze to fit ("Tight \vbox"); the \typeout
        # logs the height left free on the last page (for the layout validator)
        # Hyperref's default OpenAction only chooses the initial page, but some
        # recruiting scanners reject it as active content. Omit that optional
        # startup action while retaining real links and the owner's layout.
        startup = (r"\makeatletter\@ifpackageloaded{hyperref}{\hypersetup{pdfstartpage={}}}{}\makeatother" "\n")
        body = tex.replace("\\begin{document}", startup + "\\vbadness=0\n\\begin{document}", 1)
        body = body.replace("\\end{document}", "\\par\\typeout{JOBAGENT-SPACE=\\the\\pagegoal/\\the\\pagetotal}\n"
                            "\\end{document}", 1)
        Path(tmp, f"{name}.tex").write_text(body)
        try:
            run = subprocess.run(["latexmk", "-pdf", "-interaction=nonstopmode", "-halt-on-error", f"{name}.tex"],
                                 cwd=tmp, capture_output=True, text=True, timeout=180)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return 0, f"LaTeX compiler unavailable: {type(exc).__name__}: {str(exc)[:200]}"
        if run.returncode:
            log_path = Path(tmp, f"{name}.log")
            errs = [l for l in (log_path.read_text(errors="replace") if log_path.exists() else "").splitlines() if l.startswith("!")]
            return 0, " | ".join(errs[:3]) or (run.stdout or run.stderr or "LaTeX compilation failed")[-300:]
        try:
            info = subprocess.run(["pdfinfo", f"{name}.pdf"], cwd=tmp, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return 0, f"Cannot inspect compiled PDF: {type(exc).__name__}"
        count = re.search(r"Pages:\s+(\d+)", info.stdout)
        if info.returncode or not count:
            return 0, "The compiler did not produce a readable PDF"
        log = Path(tmp, f"{name}.log").read_text(errors="replace")
        squeezed = "Tight \\vbox" in log
        space = re.search(r"JOBAGENT-SPACE=([\d.]+)pt/([\d.]+)pt", log)
        goal, total = (float(space.group(1)), float(space.group(2))) if space else (0.0, 0.0)
        # \pagegoal is \maxdimen on a page nothing has started yet: no reading then
        free = round(goal - total, 1) if space and goal < 10000 else None
        (out_dir / f"{name}.pdf").write_bytes(Path(tmp, f"{name}.pdf").read_bytes())
        (out_dir / f"{name}.layout.json").write_text(json.dumps({"free_pt": free}))
    # A squeezed page doesn't really fit: it counts as one page too many, so the caller shortens the content.
    return int(count.group(1)) + squeezed, ""


# --- Experience bullets from the JD's duties, skills from its tech stack ----------------------------------------

PLACEHOLDER = re.compile(r"\b[XYZN]\s*%|\bby [XYZN]\b|\b[XYZN]x\b")

# --- LENGTH BUDGETS: one definition for the generation schemas, the prompts, the repair requests and the validators (F18).
# A budget is a request the writer is told about AND a rule it is checked against, with the same number everywhere, so an
# answer the schema accepts is never one a validator then refuses. Character budgets are a first filter: the rendered PDF
# (one page, one printed line per bullet) still decides whether the page fits.
BULLET_TARGET = 110            # a NEW bullet: about one printed line
BULLET_MAX = 130               # ... and never more than this; a bullet copied verbatim from the template (`original`) is exempt
BULLET_VERBATIM_MAX = 300      # the schema's ceiling, for those verbatim template bullets only
SUMMARY_TARGET = 400           # what the Summary is asked to be
SUMMARY_MAX = 450              # what the schema and the validator accept
SUMMARY_TIGHT = 300            # asked for when the page overflows
PARAGRAPH_MIN, PARAGRAPH_MAX = 300, 420   # a Projects paragraph: three sentences


def budget_text() -> str:
    """The budgets in one line, for prompts and repair feedback."""
    return (f"new bullet about {BULLET_TARGET} characters and at most {BULLET_MAX}; Summary at most {SUMMARY_TARGET} "
            f"(never over {SUMMARY_MAX}); project paragraph {PARAGRAPH_MIN}-{PARAGRAPH_MAX}")


class FillBullet(BaseModel):
    """One bullet the model writes: its text (plain, past tense, length-bounded), which job-description items it covers, the template bullet it reuses (if any), and any numbers it estimated and the candidate must confirm."""
    text: str = Field(max_length=BULLET_VERBATIM_MAX, description=f"Plain text, no LaTeX, past tense, about {BULLET_TARGET} "
                      f"characters, at most {BULLET_MAX} unless copied verbatim from an original bullet")
    duties: list[int] = Field(default_factory=list, max_length=3,
                             description="Indices of the JD ITEMS this bullet covers; empty for a reused original")
    original: int | None = Field(None, description="Index of this role's original bullet if reused, else null")
    estimated: list[str] = Field(default_factory=list, max_length=3,
                                 description="Each number/metric in this bullet that you estimated (e.g. '22%', '40 s')")

    @model_validator(mode="after")
    def _within_budget(self):
        if self.original is None and len(self.text) > BULLET_MAX:
            raise ValueError(f"this new bullet is {len(self.text)} characters; a new bullet may have at most {BULLET_MAX} "
                             f"(aim for {BULLET_TARGET}) so that it fits one line: shorten it, or reuse an original bullet "
                             f"verbatim. Text: {self.text[:90]!r}")
        return self


class FillRole(BaseModel):
    """The bullets chosen for one role of the template (2 to 4)."""
    role: int = Field(description="Role index as given")
    bullets: list[FillBullet] = Field(min_length=2, max_length=4)


class ToolsFor(BaseModel):
    """Concrete tools for one abstract skill term, e.g. 'Machine Vision' implemented by OpenCV and YOLOv8."""
    term: str = Field(max_length=40, description="One abstract term from `terms`, exactly as written there")
    tools: list[str] = Field(min_length=1, max_length=4, description="Concrete current tools implementing it")


class SkillLine(BaseModel):
    """One Technical Skills line: a category, its terms, and concrete tools for abstract terms."""
    category: str = Field(max_length=32, description="Short category name, no parentheses or colons")
    terms: list[str] = Field(min_length=1, max_length=16,
                             description="One skill per array element (never a comma list in one element)")
    tools_for: list[ToolsFor] = Field(default_factory=list, max_length=6,
                                      description="Concrete tools for the abstract terms of this line")

    def render(self) -> list[str]:
        """The terms of the line as they print: `Term (tool, tool)` where tools are known, with duplicates and tools already shown in parentheses removed.

        Returns:
            The rendered terms, in order.
        """
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
    """What the model returns for a whole resume: the Summary, the bullets of every role, the skills lines and a note on how duties were spread."""
    summary: str = Field(max_length=SUMMARY_MAX, description=f"Plain text, 2-3 complete sentences, at most {SUMMARY_TARGET} characters")
    roles: list[FillRole] = Field(min_length=1, max_length=6)
    skills: list[SkillLine] = Field(min_length=3, max_length=8)
    notes: str = Field(max_length=600, description="How duties were distributed across roles and why")


def filled_schema(r: "Resume") -> type[BaseModel]:
    """One fixed slot per role (role_0 = first role ...) so bullets cannot drift to a neighbouring role."""
    from pydantic import create_model
    fields = {"summary": (str, Field(max_length=SUMMARY_MAX, description=f"Plain text, 2-3 complete sentences, at most {SUMMARY_TARGET} characters"))}
    for i, role in enumerate(r.roles):
        fields[f"role_{i}"] = (list[FillBullet], Field(min_length=MIN_BULLETS, max_length=MAX_BULLETS,
                               description=f"Bullets for: {role.title} at {role.company}"))
    fields["skills"] = (list[SkillLine], Field(min_length=3, max_length=8))
    fields["notes"] = (str, Field(max_length=600, description="How duties were distributed across roles and why"))
    return create_model("FilledSlots", **fields)


def is_verbatim(r: "Resume", role: int, b: "FillBullet") -> bool:
    """True only when the bullet IS the template bullet its `original` index names (compared as plain text). The index alone
    proves nothing: a model can attach one to brand-new text to slip past the length budget (F18)."""
    if b.original is None or not 0 <= role < len(r.roles) or not 0 <= b.original < len(r.roles[role].bullets):
        return False
    return to_plain(r.roles[role].bullets[b.original]).strip() == b.text.strip()


def normalize_originals(r: "Resume", f: "Filled") -> "Filled":
    """A bullet that claims an original but differs from it is NEW text: it loses the claim (so the length budget and the
    new-bullet checks apply to it). Called wherever model output becomes a `Filled`."""
    for e in f.roles:
        for b in e.bullets:
            if b.original is not None and not is_verbatim(r, e.role, b):
                b.original = None
    return f


def to_filled(r: "Resume", out: BaseModel) -> Filled:
    """Assemble the model's per-role answer into a `Filled` and mark bullets that claim to be an original but are not.

    Args:
        r: The parsed template.
        out: The model's structured answer (`summary`, `role_N` lists, `skills`, `notes`).

    Returns:
        The `Filled` resume content.
    """
    roles = [FillRole(role=i, bullets=getattr(out, f"role_{i}")) for i in range(len(r.roles))]
    return normalize_originals(r, Filled(summary=out.summary, roles=roles, skills=out.skills, notes=out.notes))


FILL_PROMPT = """You write the Experience bullets and the Technical Skills of a resume for one job posting.
The candidate has told us they have done all of the posting's duties and know all of its skills.

Experience bullets:
- Every bullet is a professional, technical accomplishment: action verb + the concrete technical work (named tools,
  techniques, systems) + the result achieved, as a metric (accuracy, throughput, latency, yield, cost, time).
- Name a concrete, popular technology stack in every bullet: the posting's own tools first, then the ones commonly
  used for that work (e.g. LangChain, LangGraph, LlamaIndex, Hugging Face, PyTorch, FAISS, Pinecone, pgvector,
  BigQuery, Vertex AI, Airflow, dbt, MLflow, Docker, Kubernetes, Tableau, Looker), as "using X and Y". Never a
  generic phrase like "machine learning tools", "data visualization libraries" or "AI tools".
- A people- or process-side duty (stakeholders, storytelling, evaluating new technology, framing business problems,
  advocating data-driven decisions, teamwork) still becomes a technical bullet: the system, analysis or artifact
  that did it (e.g. "Translated stakeholder objectives into KPI dashboards in Looker on BigQuery, cutting
  reporting time 30%"). Never a bullet about soft skills alone.
- The target company's recruiting values are context, not past employment: never claim to have championed that
  company's culture or worked there under a different employer's heading. Keep company-value alignment in the
  Summary or cover letter as an intention, rather than turning it into an Experience accomplishment.
- Write every role as professional industry work. Never use wording that makes it look academic, unpaid, student
  or volunteer work: no "faculty", "professor", "lab", "coursework", "class", "student", "thesis", "volunteer",
  "unpaid", "academic", "research codebase", "research goals". Use "teams", "stakeholders", "production" instead.
- Work in two passes; field role_N is the N-th role listed.
  Pass 1, NEW bullets (`original` null): turn the numbered JD ITEMS (duties AND technical qualifications) into
  bullets describing work the candidate DID (past tense, strong verb, the posting's own keywords kept verbatim for
  ATS). Every numbered item must appear in the `duties` of at least one NEW bullet (the Summary never counts as
  covering one): every responsibility and every preferred qualification ends up in an Experience bullet. A bullet
  may combine up to 3 closely related items; a long item may be shortened to its
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
  copied from an original bullet are not estimates. Every bullet carries one metric (two at most).
- Never repeat yourself across roles: no two bullets may share an opening phrase or say the same thing. If a
  duty appears under two roles, the second bullet must use different wording, a different angle and a different
  tool/result (e.g. one role builds the pipelines, another optimizes or monitors them).
- Respect each role's dates: only name a tool, product or practice under a role if it existed during that role
  (e.g. Microsoft Copilot agents / Copilot Studio agents are 2024+, Microsoft Fabric is late 2023+, LangGraph is
  2024+); otherwise use a contemporary equivalent or move the duty to a later role.
- {max_bullets} bullets in total at most (the resume must fit one page). Keep every bullet to ONE line on the
  page: ~{bullet_target} characters, never more than {bullet_max} (a bullet copied verbatim from the template is exempt).

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
_YEARS = re.compile(r"(?i)^\W*(at least\s+)?\d+(?:\.\d+)?(?:\s*(?:to|[-–])\s*\d+(?:\.\d+)?)?\+?\s*(or more\s+)?years?\s+of\s+((non-internship|professional|"
                    r"relevant|industry|hands-on|full)\s+)*")
TECH_WORDS = re.compile(r"(?i)\b(code|coding|software|systems?|backend|back-end|frontend|front-end|full.stack|"
                        r"infrastructure|database|architecture|algorithms?|distributed|programming|debug\w*|kernel|"
                        r"firmware|embedded|hardware|cloud|machine learning|deep learning|ml|ai|test\w*|deploy\w*|"
                        r"production|scal\w+|performance|security|devops|automation|object.oriented|design patterns|"
                        r"data|api|apis|sdk|compiler|network\w*|linux|processor|gpu|cpu)\b")
# These clauses can be misclassified as responsibilities by extraction. Match
# administrative wording, not a bare word such as salary inside a real duty.
ADMIN_DUTY = re.compile(r"(?i)^\W*(?:eoe\b|equal opportunity\b|"
    r"(?:we are|is) an? equal opportunity\b|(?:the\s+)?base pay\b|"
    r"(?:primary location\s*\||additional compensation for this role\b)|"
    r"[^.;\n]{1,90}\|\s*(?:salary|pay range)\s*:|"
    r"(?:must be|candidates? must be|applicants? must be)\s+legally authori[sz]ed to work\b)")


def is_technical(text: str, jd: JobDescription) -> bool:
    """True when a sentence names tools, technologies or technical work, or one of the posting's technical skills (so it can be a duty to cover)."""
    low = text.lower()
    return bool(TOOLISH.search(text) or TECHLESS.search(text) or TECH_WORDS.search(text) or
                any(len(k) > 2 and k.lower() in low for k in jd.technical_skills))


def jd_items(jd: JobDescription) -> list[tuple[str, str, str]]:
    """Return required experience coverage under the existing all-preferred writing policy.

    Actual responsibilities, technical basic qualifications and coverable preferred
    qualifications are required. Recruiting/values prose, credentials, logistics
    and administrative clauses stay in the JD but are not work-experience duties.
    """
    from .jdtext import non_experience_category
    # every responsibility, soft-sounding ones included, becomes Experience bullets (written as technical work)
    items = [("duty", d, "REQUIRED") for d in jd.responsibilities
             if not NON_SKILL.search(d) and not ADMIN_DUTY.search(d) and not non_experience_category(d)]
    for kind, quals in (("basic", jd.basic_qualifications), ("preferred", jd.preferred_qualifications)):
        for q in quals:
            q = _YEARS.sub("", q.lstrip("-• ").strip())
            # a preferred qualification is bullet material even without a tool in it ("consumer goods / supply
            # chain experience"); a basic one only when technical (the rest are traits the summary carries)
            if q and not NON_SKILL.search(q) and not NOT_BULLET.search(q) and not non_experience_category(q) and (kind == "preferred" or is_technical(q, jd)):
                items.append((kind, q[0].upper() + q[1:], "REQUIRED"))
    return items


def _graph_text(graph) -> str:
    from jobagent.tools import skillgraph
    return skillgraph.hint_text(graph or {})


def build_fill_prompt(r: Resume, jd: JobDescription, company_md: str, max_bullets: int, feedback: str = "", graph: dict | None = None) -> list:
    """Build the messages that ask the model to write the whole resume.

    Args:
        r: The parsed template (roles, original bullets, current skills).
        jd: The job posting.
        company_md: The researched company profile text.
        max_bullets: How many bullets the page can hold.
        feedback: Notes from the previous round to fix.
        graph: Related-skill suggestions from the skill graph (owned ones may fill space; the others are forbidden).

    Returns:
        The system and human messages.
    """
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
    return [SystemMessage(with_today(FILL_PROMPT.format(max_bullets=max_bullets, bullet_target=BULLET_TARGET, bullet_max=BULLET_MAX))),
            HumanMessage(text + learning_hint(jd) + _graph_text(graph) + "\n\n" + facts_block())]


def learning_hint(jd) -> str:
    """What past applications showed for this role family (step 15c), only when the candidate switched
    `learning_use_tailoring_hints` or `learning_use_lessons` on; '' otherwise, without touching the database."""
    try:
        from jobagent import db
        from jobagent.config import get_settings
        from jobagent.tools import learning

        s = get_settings()
        if not (s.learning_use_tailoring_hints or s.learning_use_lessons):
            return ""
        conn = db.connect()
        row = conn.execute("SELECT role_id FROM jobs WHERE url = ?", (getattr(jd, "source_url", "") or "",)).fetchone()
        return learning.prompt_hints(conn, (row["role_id"] if row and row["role_id"] else ""), s)
    except Exception:
        return ""


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
    """The parts of the template the tailor must never change (header, Education, Projects or Publications, section headings, role headings), for the byte-for-byte check."""
    tex = r.tex
    pub = next((i for i in (tex.find("\\section{Projects}"), tex.find("\\section{Publications}")) if i != -1), -1)
    start = pub if pub != -1 else tex.index("\\section{Education}")
    return [tex[:r.summary_span[0]], tex[start:r.skills_span[0]], "\\section{Experience}",
            "\\section{Technical Skills}"] + [role.header for role in r.roles]


def apply_fill(r: Resume, f: Filled) -> tuple[str, list[str]]:
    """Write a `Filled` resume into the template.

    Every writer and every refresh renders through here, so related skills this candidate does not have (`r.forbidden`) are removed from the skills lines first.

    Args:
        r: The parsed template.
        f: The resume content; its skills are replaced by the cleaned ones.

    Returns:
        The new LaTeX source and a list of problems found while placing the content.
    """
    problems = []
    if r.forbidden:                       # every render of every writer and refresh passes here: a skill the candidate's files do not show is removed
        from jobagent.tools import skillgraph
        f.skills, _ = skillgraph.enforce(f.skills, r.forbidden)
        if not f.skills:
            f.skills = [SkillLine(category=c, terms=[to_plain(v)]) for c, v in r.skills][:8]
    tex = r.tex
    # skills: the whole category list is regenerated
    s0 = r.skills_span[0]
    i0 = tex.index("\\small{\\item{", s0) + len("\\small{\\item{")
    i1 = tex.index("}}", i0)
    # a category whose terms were all deduplicated away (e.g. "Databases:" with nothing after it) is left out
    lines = [f"     \\textbf{{{to_latex(s.category)}}}{{: {', '.join(to_latex(x) for x in s.render())}}}" for s in f.skills
             if s.render()]
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


ITEMS_PER_BULLET = 3      # FillBullet.duties allows at most this many JD items per bullet


def coverage_target(jd: JobDescription, slots: int) -> tuple[list[int], list[int]]:
    """(REQUIRED JD item indices the build must cover, those it cannot). `slots` bullets cover at most ITEMS_PER_BULLET items
    each; the posting's order is its priority, so the items beyond that capacity are the ones given up - and said so, instead of
    asking the writer again and again for something the page cannot hold."""
    required = [i for i, (_, _, need) in enumerate(jd_items(jd)) if need == "REQUIRED"]
    cap = max(slots, 0) * ITEMS_PER_BULLET
    return required[:cap], required[cap:]


def feasibility(r: Resume, jd: JobDescription, slots: int) -> list[str]:
    """Combinations of coverage and page constraints that cannot be met, found BEFORE the models are called (F18). Character
    budgets are not a guarantee of fit - the rendered PDF still decides - but these are impossible on arithmetic alone."""
    out = []
    if len(r.roles) * MIN_BULLETS > slots:
        out.append(f"{len(r.roles)} roles need at least {MIN_BULLETS} bullets each ({len(r.roles) * MIN_BULLETS}), above the "
                   f"{slots} the page allows")
    _, dropped = coverage_target(jd, slots)
    if dropped:
        out.append(f"the posting has {len(coverage_target(jd, slots)[0]) + len(dropped)} REQUIRED items, but {slots} bullets cover at "
                   f"most {slots * ITEMS_PER_BULLET}: items {dropped} (the lowest priority) cannot be covered")
    return out


def fill_checks(r: Resume, jd: JobDescription, f: Filled, slots: int | None = None) -> tuple[list[int], list[str], list[str]]:
    """(REQUIRED JD item indices no NEW bullet covers, JD skills found nowhere, invented-number warnings). With `slots`,
    only the items that many bullets can hold are expected (see `coverage_target`)."""
    covered = {d for e in f.roles for b in e.bullets if b.original is None for d in b.duties}
    expected = coverage_target(jd, slots)[0] if slots is not None else None
    uncovered = [i for i, (_, _, need) in enumerate(jd_items(jd)) if need == "REQUIRED" and i not in covered
                 and (expected is None or i in expected)]
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


MIN_BULLETS, MAX_BULLETS = 3, 4  # every role carries its share of the posting's responsibilities
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


def _without_role_employer(r: Resume, role: int, text: str) -> str:
    """Remove the exact source employer before judging a bullet's wording.

    Args:
        r: Owner-supplied template with protected role identities.
        role: Index of this bullet's source role.
        text: Generated bullet, left unchanged for rendering and reporting.

    Returns:
        Text with only whole occurrences of that role's employer masked.
        Generic academic wording and names of other employers remain visible.
    """
    if not 0 <= role < len(r.roles):
        return text
    name = to_plain(getattr(r.roles[role], 'company', '')).strip()
    if not name:
        return text
    pattern = r'(?<!\w)' + r'\s+'.join(re.escape(word) for word in name.split()) + r'(?!\w)'
    return re.sub(pattern, ' ', text, flags=re.I)


def duplicates(r: Resume, f: Filled, *, substantive: bool = False) -> list[str]:
    """Pairs of bullets (anywhere in Experience) that repeat each other: a shared 4-word phrase (ignoring very
    common joiners) or > 50% word overlap. With substantive=True, a shared phrase
    needs three substantial words, so a repeated tool list alone does not demand
    rewriting distinct duties. Returned as lines for the model and report."""
    stop = {"and", "the", "to", "for", "of", "a", "an", "in", "on", "with", "by", "into", "from"}
    if substantive:stop |= {'using','via'}
    tokens=lambda text:re.findall(r'[a-z0-9]+(?:-[a-z0-9]+)*',text.lower().replace('‑','-')) if substantive else _words(text)
    technical = set()
    if substantive:
        terms = list(GENERIC_NAMES) + [to_plain(v) for _,v in getattr(r,'skills',[])]
        terms += [term for line in f.skills for term in line.render()]
        technical = {word for term in terms for word in tokens(term)}
    items = [(e.role, k, b.text) for e in f.roles for k, b in enumerate(e.bullets)]
    out = []
    for x in range(len(items)):
        for y in range(x + 1, len(items)):
            (ra, ka, ta), (rb, kb, tb) = items[x], items[y]
            # A technical compound is one concept. Splitting real-time made
            # the generic connector "for real-time constraints" a 4-word duty.
            # Repeating an inherited employer identity is not repeating a duty.
            wa, wb = tokens(_without_role_employer(r, ra, ta)), tokens(_without_role_employer(r, rb, tb))
            grams = lambda w: {tuple(w[i:i + 4]) for i in range(len(w) - 3) if not set(w[i:i + 4]) <= stop}
            shared = grams(wa) & grams(wb)
            if substantive:
                shared={gram for gram in shared if len({w for w in gram if w not in stop|technical and len(w)>=3})>=3}
            sa, sb = set(wa) - stop, set(wb) - stop
            overlap = len(sa & sb) / max(1, min(len(sa), len(sb)))
            copied = substantive and wa == wb
            content_overlap = not substantive or len({w for w in sa & sb if w not in technical and len(w)>=3}) >= 3
            if shared or (overlap > 0.5 and content_overlap) or copied:
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


MAX_NO_RESULT = 0  # every bullet states a measured result
GENERIC_NAMES = {"ai", "ml", "genai", "llm", "llms", "api", "apis", "kpi", "kpis", "roi", "us", "usa", "it",
                 "machine learning", "deep learning", "generative ai", "data science", "analytics", "cloud",
                 "neural network", "transformer", "etl", "rest", "microservices", "reinforcement learning",
                 "computer vision", "gpu", "gpus"}
GENERIC_TOOLS = re.compile(r"(?i)\b(machine learning|ml|ai|data visuali[sz]ation|analytics|data|visuali[sz]ation|"
                           r"various|modern|complex database) (tools|libraries|techniques|frameworks|technologies)\b")


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
    # Employer names are proper nouns, but do not establish a technology.
    # Explicitly listed technologies still count through the separate tech set
    # (for example a product which also happens to name the hiring company).
    identities = {word.casefold() for name in [jd.company] + [role.company for role in r.roles]
                  for word in re.findall(r'[A-Za-z][\w+#.-]*', name or '')}
    out, no_result = [], []
    for e in f.roles:
        for k, b in enumerate(e.bullets):
            where = f"role_{e.role} bullet {k + 1} (\"{b.text}\")"
            if not is_verbatim(r, e.role, b) and len(b.text) > BULLET_MAX:      # F18: an `original` index does not exempt new text
                out.append(f"{where} is {len(b.text)} characters; new or changed text may have at most {BULLET_MAX} "
                           f"(aim for {BULLET_TARGET}); only a bullet identical to its template original is exempt")
            if DASH.search(b.text):
                out.append(f"{where} uses a dash as punctuation; rewrite with a comma, semicolon or colon")
            if e.role < len(r.roles) and (other := moved_from(r, e.role, b.text)) is not None:
                out.append(f"{where} is the candidate's bullet from {r.roles[other].company}, not from "
                           f"{r.roles[e.role].company}: replace it with a new bullet for this role")
            if m := ACADEMIC.search(_without_role_employer(r, e.role, b.text)):
                out.append(f"{where} uses academic/unpaid wording \"{m.group(0)}\"")
            low = b.text.lower()
            # "AI", "ML", "LLM" or "machine learning" name a field, not a tool: they don't count
            named = {w for w in set(TOOLISH.findall(b.text)) | proper_nouns(b.text)
                     if w.lower() not in GENERIC_NAMES | identities}
            has_tool = bool(named) or any(re.search(r"(?<![a-z])" + re.escape(t) + r"(?![a-z])", low)
                                        for t in tech if t and t not in GENERIC_NAMES and not GENERIC_TOOLS.fullmatch(t))
            if not has_tool:
                out.append(f"{where} names no technology/tool (AI / ML / LLM alone don't count): name the stack")
            if not RESULT.search(b.text):
                no_result.append(f"{where} states no result")
            if (m := GENERIC_TOOLS.search(b.text)) and not has_tool:
                out.append(f"{where} says \"{m.group(0)}\": name the actual tools (e.g. LangChain, PyTorch, BigQuery)")
    if DASH.search(f.summary):
        out.append("the summary uses a dash as punctuation; rewrite with a comma, semicolon or colon")
    return out + (no_result if len(no_result) > MAX_NO_RESULT else [])


# --- repair: rewrite only the flagged bullets -------------------------------------------------------------------

class BulletFix(BaseModel):
    """A rewrite of one bullet: which role and position, the new text, and any estimated numbers."""
    role: int = Field(description="role_N index of the bullet")
    bullet: int = Field(description="1-based position of the bullet in that role")
    text: str = Field(max_length=BULLET_MAX, description=f"The rewritten bullet, plain text, about {BULLET_TARGET} characters, "
                                                           f"at most {BULLET_MAX}")
    estimated: list[str] = Field(default_factory=list, max_length=3)


class Repair(BaseModel):
    """A set of up to eight bullet rewrites that fix the flagged problems."""
    fixes: list[BulletFix] = Field(min_length=1, max_length=8)


_REPAIR_TEMPLATE = f"""You fix specific bullets of a tailored resume. {{coverage_rule}} Each rewritten bullet must
name concrete technologies, state a result (a believable estimated metric is fine;
list it in `estimated`), stay within the length budget (about {BULLET_TARGET} characters, at most {BULLET_MAX}), not repeat any other bullet, use no academic wording and no
dashes as punctuation, and describe work done at THAT role's employer (never another employer's work). A change by
a percentage takes "by"."""
REPAIR_PROMPT = _REPAIR_TEMPLATE.format(coverage_rule=(
    'Rewrite ONLY the bullets listed under PROBLEMS, each in place, so that every problem is solved; '
    'leave all other bullets alone. Each rewritten bullet must keep covering the JD items it covered (shown).'))


def build_repair_prompt(r: Resume, jd: JobDescription, f: Filled, issues: list[str], *, section_coverage: bool = False) -> list:
    """Build the messages that ask the model to fix specific bullet problems.

    Args:
        r: The parsed template.
        jd: The job posting.
        f: The current resume content.
        issues: The problems to fix.
        section_coverage: Allow existing bullets to exchange duties while preserving section coverage.

    Returns:
        The system and human messages.
    """
    items = jd_items(jd)
    lines = []
    for e in f.roles:
        role = r.roles[e.role]
        lines.append(f"role_{e.role}: {role.title} at {role.company} ({role.dates})")
        for k, b in enumerate(e.bullets, 1):
            covers = "; ".join(items[d][1][:80] for d in b.duties if 0 <= d < len(items))
            lines.append(f"  {k}. {b.text}" + (f"   [covers: {covers}]" if covers and b.original is None else ""))
    system=(_REPAIR_TEMPLATE.format(coverage_rule=(
        'Repair the named problems in place. For missing coverage, you may also edit suitable existing bullets. '
        'Preserve previously covered JD indices ACROSS THE WHOLE EXPERIENCE SECTION, not separately in each '
        'bullet. To resolve a repeated duty, keep one occurrence and replace the other with distinct relevant '
        'work, preferably an uncovered JD item. Do not just substitute synonyms. Do not add or remove bullets. '
        'Leave unrelated bullets unchanged.')) if section_coverage else REPAIR_PROMPT)
    return [SystemMessage(with_today(system)),
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
    """Keywords to add to one skills category (existing or new)."""
    category: str = Field(max_length=40, description="An existing category name exactly, or a new short one")
    terms: list[str] = Field(min_length=1, max_length=6, description="ONLY the new keywords to add to this category")


class ProjectFix(BaseModel):
    """A rewritten description for one of the Projects entries."""
    project: int = Field(description="1-based number of the project under PROJECTS")
    paragraph: str = Field(max_length=PARAGRAPH_MAX, description=f"The rewritten description: 3 sentences, {PARAGRAPH_MIN}-{PARAGRAPH_MAX} characters")


class KeywordPass(BaseModel):
    """The model's answer to a missing-keywords round: skills to add, bullets and project descriptions to rewrite, and optionally a new Summary."""
    skills: list[SkillAdd] = Field(default_factory=list, max_length=4)
    bullets: list[BulletFix] = Field(default_factory=list, max_length=6)
    projects: list[ProjectFix] = Field(default_factory=list, max_length=4)
    summary: str = Field("", max_length=SUMMARY_MAX, description=f"The rewritten Summary (plain text, 2-3 sentences, at most "
                                                                  f"{SUMMARY_TARGET} characters), or empty to keep it")


KEYWORD_PROMPT = f"""ATS scans of this tailored resume found the posting's keywords listed under MISSING, and the checkers
gave the suggestions under ADVICE. The goal is an average ATS score above 90: work in EVERY missing item and follow
EVERY piece of advice, within the CANDIDATE FACTS:
- Prefer adding a keyword to the most fitting existing Technical Skills category (`skills`): list ONLY the new
  keywords in `terms`, never the skills already there; open a new short category only if none fits (at most 8).
- Where a keyword names work rather than a tool (a practice, a system, a domain), rewrite ONE existing bullet that
  fits it (`bullets`: role_N index and 1-based position), keeping that bullet's numbers, coverage and length
  (at most {BULLET_MAX} characters). Never move work to another employer.
- A MISSING entry may be a phrase about work ("leading small projects independently", "navigating large code
  bases") or a requirement ("2 years of fraud analysis"): work it into the Summary or a bullet in the posting's
  own words (years only within CANDIDATE FACTS).
- A MISSING item that names a skill already in Technical Skills ("experience with Kafka", "Terraform") means the
  checker wants to see it used in the work: put it in a bullet, not in the skills again. Rewrite at least 2
  bullets every round unless every MISSING item already appears in a bullet.
- LAST ROUND (if given) says what your previous answer did to the scores: build on what was kept, try something
  different from what was undone, and never resend changes that were already in place.
- A MISSING word that is not a skill (finance, compliance, product, safety) goes into a natural sentence of the
  Summary, a bullet or a project description (`projects`: rewrite a PROJECTS paragraph, keeping its problem,
  system and result, 3 sentences, {PARAGRAPH_MIN}-{PARAGRAPH_MAX} characters). Never write words that refer to applicants.
- Soft skills and qualities ("collaboration", "comfort with ambiguity") never go in Technical Skills: show them in
  the Summary or in what a bullet's work involved ("partnered with security and product teams to ...").
- Follow the ADVICE (vary repeated words, add a result to a line without one, name the teams the work involved,
  state the experience the posting asks for, open the Summary with the posting's exact title). Rewrite the Summary
  (`summary`) whenever that helps; keep it 2-3 sentences, at most {SUMMARY_TARGET} characters.
- Every keyword under KEEP is already matched by the checkers: a rewritten bullet or Summary must still contain
  each KEEP word it contained before. Never make a bullet or the Summary longer than it was (checkers penalise
  long sentences); swap words in rather than append clauses.
- Use the posting's exact spelling. Skip only words that are page boilerplate (salary, posted, applicants, etc).
- Never invent degrees, certifications, clearances, citizenship or work authorization, employers, job titles or
  dates, and never add a location.
- No dashes as punctuation; a change by a percentage takes "by"."""


def build_keyword_prompt(r: Resume, jd: JobDescription, f: Filled, missing: list[str],
                         advice: list[str] = (), keep: list[str] = (), last: str = "",
                         projects: list[str] = ()) -> list:
    """Build the messages that ask the model to work missing ATS keywords into the resume truthfully.

    Args:
        r: The parsed template.
        jd: The job posting.
        f: The current resume content.
        missing: Keywords the checker sites say are missing.
        advice: Written advice from the sites.
        keep: Terms that must stay.
        last: What happened in the previous round.
        projects: The current Projects descriptions.

    Returns:
        The system and human messages.
    """
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
                         f"\n\nEXPERIENCE BULLETS:\n" + "\n".join(lines) +
                         ("\n\nPROJECTS:\n" + "\n".join(f"{i}. {p}" for i, p in enumerate(projects, 1)) if projects else ""))]


# Plain English that ATS checkers report as "missing keywords" but that is never a skill on a resume
GENERIC_WORDS = set("""build builds building tool tools information info value values product products problem problems
design designs work works working management manage people person communication communications team teams user users
world range salary location locations posted updated employment job jobs role roles experience growth economic
critical thinking understanding think small large business businesses company companies opportunity opportunities
customer customers solution solutions process processes quality support service services system systems data
technology technologies skills skill ability knowledge strong excellent new great best help impact results result
develop development developer engineer engineering software project projects time year years day days""".split())


def is_generic(term: str) -> bool:
    """True when every word of a term is a generic word ('experience', 'strong', ...), so it is not worth adding as a skill."""
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
