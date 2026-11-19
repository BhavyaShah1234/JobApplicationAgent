"""Multi-agent resume writer (experiment): each section has its own agent and model, validators review the page and
the ATS scores, and route every finding to the agent that owns that part of the resume.

    jobagent multi-agent outputs/<company>/<job-id>:<slug> [...] [--role experience=qwen3-coder-next ...]

Agents (each sees the JD, company profile, template, candidate facts and the current resume; edits only its part):
- experience: the bullets of every role (same rules as step 9);
- projects: chooses the GitHub projects and writes/rewrites their paragraphs;
- skills: the Technical Skills lines (posting terms first, the template's own skills to fill a line);
- summary: the Summary, written last from the other sections.
Validators:
- layout: compile, one page, bullet rules, JD coverage, the rendered-page checks (tools/layout.py) and a vision
  review; each finding goes to the agent whose section it is about;
- ats: the online checkers; a router model splits missing keywords and advice into tasks per agent.
Loop: write -> fix layout (<= 3 passes) -> score -> route -> agents edit -> fix layout -> rescore; a round is kept if the
average rises (or holds within the noise while the page improves), else undone and reported back; <= 3 ATS rounds.
"""

import hashlib
import json
import operator
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field, create_model

from jobagent import llm
from jobagent.clock import with_today
from jobagent.config import get_settings
from jobagent.log import progress, with_context
from jobagent.state import JobDescription
from jobagent.steps import ats as A
from jobagent.tools import ats_sites, bundle, github, layout
from jobagent.tools import projects as P
from jobagent.tools import skillgraph as SG
from jobagent.tools import tailor as T
from jobagent.tools.vault import optional_secret

AGENTS = ("experience", "projects", "skills", "summary")
# Each agent's model from the per-section report of the 3-posting model run (outputs/experiments/
# 20261031-1910-doordash-adobe-symbotic/SECTIONS.md): experience = fewest bullet issues and best keyword sub-score,
# projects = paragraphs on target length, skills = fewest half-empty lines (after devstral), summary = HireFlow
# experience fit (fast), validator/router = glm-5-3. Override with --role agent=model or --same-model.
# Summary moved from llama4-scout-17b to devstral2-123b (2026-11-02): as an agent llama4-scout copied the posting's
# requirement list into a ~5-line Summary and ignored the length limit in every validator pass.
DEFAULT_MODELS = {"experience": "devstral2-123b", "projects": "gpt-oss-120b", "skills": "glm-5-3-flash",
                  "summary": "devstral2-123b", "router": "glm-5-3"}
SITES = ats_sites.DEFAULT_SITES
# 3 validator fix passes and 3 ATS rounds after the first score (5 and 5 until 2026-11-08: the user asked for 3)
LAYOUT_PASSES, ATS_ROUNDS, TARGET = 3, 3, 90.0
BLANK_HARD_PT = 24  # more free height than this (about 2 lines) at the bottom disqualifies a version
# Tasks that need the whole Experience section rewritten (coverage, length); anything else is a targeted bullet edit
FULL_REWRITE = __import__("re").compile(r"(?i)not covered|pages?; it must be one|overflows|failed to compile|"
                                        r"add bullets")


# --- section outputs ----------------------------------------------------------------------------------------

def experience_schema(r: T.Resume) -> type[BaseModel]:
    """Build the structured-output schema for the experience agent: one bullet list per role of this template plus a note.

    Args:
        r: The parsed template (its roles decide the fields).

    Returns:
        A pydantic model class with `role_0 ... role_N` and `notes`.
    """
    fields = {f"role_{i}": (list[T.FillBullet], Field(min_length=T.MIN_BULLETS, max_length=T.MAX_BULLETS,
                                                      description=f"Bullets for: {role.title} at {role.company}"))
              for i, role in enumerate(r.roles)}
    fields["notes"] = (str, Field(max_length=600, description="How duties were distributed across roles"))
    return create_model("ExperienceOut", **fields)


class SkillsOut(BaseModel):
    """The skills agent's answer: three to eight Technical Skills lines."""
    skills: list[T.SkillLine] = Field(min_length=3, max_length=8)


class SummaryOut(BaseModel):
    """The summary agent's answer: the Summary as plain text, within the length limits."""
    summary: str = Field(max_length=T.SUMMARY_MAX, description=f"Plain text, 2-3 complete sentences, at most {T.SUMMARY_TARGET} characters")


class ProjectSwap(BaseModel):
    """Replace one Projects entry with a better candidate repository."""
    project: int = Field(description="1-based number under PROJECTS to replace")
    entry: P.ProjectEntry = Field(description="The better CANDIDATE repo, written like the others")


class ProjectsOut(BaseModel):
    """The projects agent's answer: rewritten descriptions and at most two swaps."""
    projects: list[T.ProjectFix] = Field(default_factory=list, max_length=4)
    swaps: list[ProjectSwap] = Field(default_factory=list, max_length=2)


class Finding(BaseModel):
    """One problem the reviewer found: which agent should fix it, what is wrong, and whether it is a real error or only a suggestion."""
    agent: Literal["experience", "projects", "skills", "summary"]
    issue: str = Field(max_length=400, description="What is wrong and what to do, citing the text")
    severity: Literal["error", "suggestion"] = Field(description="Error only for an actual correctness problem; a preference for a narrower category is a suggestion")


class Review(BaseModel):
    """The reviewer's list of findings (at most eight)."""
    findings: list[Finding] = Field(default_factory=list, max_length=8)


class Task(BaseModel):
    """An instruction for one agent about what to change."""
    agent: Literal["experience", "projects", "skills", "summary"]
    instruction: str = Field(max_length=500, description="What that agent must change, citing the items")


class RoutePlan(BaseModel):
    """The router's plan: which agent gets which instruction before the next edit."""
    tasks: list[Task] = Field(default_factory=list, max_length=12)


ROLE_PROMPTS = {
    "experience": "You are the EXPERIENCE agent: write ONLY the Experience bullets (fields role_N), following the rules "
                  "below for bullets. Other agents write the Summary, Technical Skills and Projects.",
    "skills": "You are the SKILLS agent: write ONLY the Technical Skills lines (`skills`), following the rules below "
              "for skills. Use 3-7 coherent categories, usually one line each. Category names must describe every "
              "term they contain: languages belong with languages, ML tools with ML, and so on. Prefer the "
              "candidate's own TEMPLATE SKILLS in their original groups, merging compatible groups if needed. "
              "Semantic accuracy and relevance come before line fullness: leave a short line rather than add "
              "an unrelated term. Trim long lines. Never list soft skills or words for applicants.",
    "summary": "You are the SUMMARY agent: write ONLY the Summary, following the rules below for the summary. Base it "
               "on the CURRENT RESUME's bullets, skills and projects so it never claims what they don't show.",
}

PROJECTS_EDIT_PROMPT = """You are the PROJECTS agent of a resume team. Do what TASKS ask for the Projects section:
- rewrite a paragraph in place (`projects`: 1-based number under PROJECTS and the new paragraph), or
- replace a weaker project with a better CANDIDATE repo (`swaps`: the number to replace and a full entry for the
  candidate: repo full_name exactly as listed, title, stack, paragraph, short).
Each paragraph: exactly 3 COMPLETE sentences, aiming for 330-370 characters (hard bounds {PMIN}-{PMAX}): the problem,
the system built (stack named), the wow factor (a result or scale). Finish the final sentence; never cut a sentence
to reach the character limit. The title is a real project name, never the bare repo slug. Use the posting's keywords the TASKS
ask for. No dashes as punctuation; "by N%". Never invent degrees, employers or dates.""".replace("{PMIN}", str(T.PARAGRAPH_MIN)).replace("{PMAX}", str(T.PARAGRAPH_MAX))
PROJECTS_EDIT_PROMPT += "\nEach indexed project's repo identity and its OWN source card bind its description. Never borrow " \
    "features, interfaces, supported counts or results from another project, even when editing several at once."

REVIEW_PROMPT = """You are the VALIDATOR of a resume team. Check the CURRENT RESUME for this posting the way a hiring
manager would. Label actual correctness problems severity=error and optional refinements severity=suggestion.
Broad or combined category headings are intentional: a term need only fit ONE named component. An AI tool belongs
under AI & Development Tools; workflow and monitoring systems can belong under Distributed Systems. A more specific
heading is a preference, not an error. Never demand a single canonical taxonomy. Report findings for their owning agent:
Review CURRENT RESUME and DISPLAYED PROJECTS only; template skills and candidate repos are source context, not text
necessarily printed on the page. Selected short project descriptions intentionally use two complete sentences;
full descriptions use three. Do not demand expansion of a valid short variant.
- projects: a description that contradicts its OWN repository source is an error. A preferred alternative project,
  more posting-specific wording, or a preferred descriptive title is a suggestion. Do not confuse one repo's features
  with another's. Estimated impact metrics allowed by the candidate's claims policy are not contradictions;
  unsupported product features or feature counts are different. Sentence-ending and bare-slug checks also run in code;
- experience: bullets that don't fit their employer, repeat each other, or miss the posting's main duties;
- skills: tools placed under a category that does not describe them (for example ML frameworks under Frontend or
  deployment platforms under Databases); an empty category; soft skills listed. Report only these correctness problems, never
  short lines or missing keywords. Prefer moving terms or merging compatible categories over adding padding;
- summary: claims the rest of the resume doesn't show; not opening with the posting's title.
Return an empty list if the resume is sound."""

ROUTER_PROMPT = """You are the ATS validator of a resume team. Online ATS checkers scored the resume; their MISSING
keywords and ADVICE are below. Turn them into tasks for the agents that own each part of the resume:
- experience: work phrases, experience requirements ("2 years of X", "experience with Terraform"), domain words,
  results/metrics advice, repeated-word advice in bullets;
- skills: tools, languages, frameworks, platforms, methods (one task listing all of them);
- summary: the posting's exact title, years of experience, soft skills/qualities, mission or domain words;
- projects: non-skill words that fit a project description (e.g. compliance, product, finance), project advice.
Every MISSING item must go to exactly one agent. Ignore page boilerplate (salary, posted, benefits) and words for
applicants (applicants, candidate, people, you). Keep instructions short and concrete. Never ask for a city or
location, degrees, certifications, clearances, citizenship or work authorization."""


# --- graph state -----------------------------------------------------------------------------------------------

class State(TypedDict, total=False):
    """The state of the writing graph: the job context, the current resume content, the tasks for the next edit, what happened last, the scores and the best checked version so far."""
    ctx: dict                       # job_dir, jd, company, r, jd_text, models, log
    f: T.Filled                     # summary + roles + skills
    tasks: dict                     # agent -> list of instructions for the next edit
    last: str                       # what happened to the last edit (fed back to the agents)
    scores: dict
    best: dict                      # {"avg", "scores", "f", "tex", "pdf", "projects"} of the best checked version
    ats_round: int
    layout_pass: int
    layout_issues: list
    reviewed: bool
    content_hash: str
    content_findings: list
    content_error: str
    semantic_hard: list[str]
    skills_padding_attempted: bool
    hard: list
    budget_feedback: dict            # initial limited-site feedback retained during unlimited-site refinements
    history: Annotated[list, operator.add]


def _context(ctx: dict, f: T.Filled | None, project_state: str | None, *, visible_only: bool = False) -> str:
    """What every agent sees about the current resume."""
    r = ctx["r"]
    parts = [T.facts_block(), "TEMPLATE SKILLS (the candidate's own): " +
             "; ".join(f"{c}: {T.to_plain(v)}" for c, v in r.skills)]
    if hint := SG.hint_text(ctx.get("graph")).strip():
        parts.append(hint)
    if f is not None:
        lines = []
        for e in f.roles:
            role = r.roles[e.role]
            lines.append(f"role_{e.role}: {role.title} at {role.company} ({role.dates})")
            lines += [f"  {k}. {b.text}" for k, b in enumerate(e.bullets, 1)]
        parts.append("CURRENT RESUME\nSummary: " + f.summary + "\nExperience:\n" + "\n".join(lines) +
                     "\nTechnical Skills:\n" + "\n".join(f"- {s.category}: {', '.join(s.render())}" for s in f.skills if s.render()))
    if project_state:
        st = json.loads(project_state)
        cards = dict(ctx.get('cards') or {})
        cards.update({c['full_name'].casefold(): c for _, c in ctx.get('candidates', [])})
        sources = {e.get('repo', '').casefold(): cards.get(e.get('repo', '').casefold(), {}).get('summary', '')[:1400]
                   for e in st.get('ranked', [])[:st.get('kept', 0)]}
        if visible_only:
            parts.append("DISPLAYED PROJECTS:\n" + "\n".join(
                f"{i}. repo={p['repo']}; title={p['title']}; stack={p['stack']} ({p['variant']}): {p['text']}\n"
                f"SOURCE FOR THIS REPO ONLY: {sources.get((p['repo'] or '').casefold(), '') or 'Not available'}"
                for i, p in enumerate(_displayed_projects(st), 1)))
        else:
            parts.append("PROJECTS:\n" + "\n".join(
                f"{i}. repo={e.get('repo')}; title={e.get('title')}; full={e.get('paragraph')}; short={e.get('short')}\n"
                f"SOURCE FOR THIS REPO ONLY: {sources.get(e.get('repo', '').casefold(), '') or 'Not available'}"
                for i, e in enumerate(st.get('ranked', [])[:st.get('kept', 0)], 1)))
    if ctx.get("candidates"):
        known = set(P.known_entries(r.tex))
        parts.append("CANDIDATE REPOS (for the Projects section):\n" + "\n".join(
            f"- {c['full_name']}{' (TEMPLATE)' if c['full_name'].lower() in known else ''} [relevance {sc:.2f}]: "
            f"{c['title']}; {c['summary'][:220]}; stack {', '.join(c['tech_stack'][:6])}"
            for sc, c in ctx["candidates"]))
    return "\n\n".join(parts)


def _section_messages(ctx: dict, agent: str, f: T.Filled | None, tasks: list[str], last: str) -> list:
    """The step 9 prompt (all rules and the posting) with the agent's role, the current resume and its tasks."""
    base = T.build_fill_prompt(ctx["r"], ctx["jd"], ctx["company"], 16)
    system = SystemMessage(ROLE_PROMPTS[agent] + "\n\n" + base[0].content)
    proj = (ctx["job_dir"] / "projects.json")
    extra = _context(ctx, f, proj.read_text() if proj.exists() else None)
    if tasks:
        extra += "\n\nTASKS FOR YOU (from the validators):\n" + "\n".join(f"- {t}" for t in tasks)
    if last:
        extra += f"\n\nLAST ROUND: {last}"
    if f is not None:
        extra += "\n\nKeep everything in your section that no task asks you to change."
    return [system, HumanMessage(base[1].content + "\n\n" + extra)]


def _invoke(ctx, stage, model, schema, messages, **kwargs):
    """Use private, source-bound artifacts only in the opt-in document strategy."""
    if ctx.get('incremental'):
        from jobagent.tools.incremental_documents import invoke
        role = stage if stage in ctx['models'] else 'router'
        return invoke(ctx['cache'], stage, ctx['models'][role], model, schema, messages, **kwargs)
    return llm.structured_invoke(model, schema, messages, **kwargs)


def _run_agent(ctx: dict, agent: str, f: T.Filled | None, tasks: list[str], last: str) -> tuple[str, object]:
    """One section agent's call; returns (agent, output) or (agent, None) on failure."""
    model = llm.writer(16000 if agent == "experience" else 4000 if agent == "projects" else 8000,
                       timeout=120 if agent == "projects" else 600, model=ctx["models"][agent])
    log, t0 = ctx["log"], time.monotonic()
    try:
        if agent == "projects":
            proj = ctx["job_dir"] / "projects.json"
            msgs = [SystemMessage(with_today(PROJECTS_EDIT_PROMPT)),
                    HumanMessage(_context(ctx, f, proj.read_text()) + "\n\nTASKS:\n" + "\n".join(f"- {t}" for t in tasks)
                                 + (f"\n\nLAST ROUND: {last}" if last else ""))]
            # A short paragraph repair must not spend ten minutes in a fallback
            # mode after length validation fails. Preserve the previous version
            # on failure; its hard findings still prevent publication.
            out = _invoke(ctx, agent, model, ProjectsOut, msgs, log=log, deadline_s=180)
        elif agent == "experience" and f is not None and not any(FULL_REWRITE.search(t) for t in tasks):
            # targeted edit: only the bullets the validators named (a full rewrite moves every orphan word)
            msgs = T.build_repair_prompt(ctx["r"], ctx["jd"], f, tasks + ([f"LAST ROUND: {last}"] if last else []))
            out = _invoke(ctx, agent, model, T.Repair, msgs, log=log)
        else:
            schema = {"experience": experience_schema(ctx["r"]), "skills": SkillsOut, "summary": SummaryOut}[agent]
            out = _invoke(ctx, agent, model, schema, _section_messages(ctx, agent, f, tasks, last), log=log)
        log(f"  {agent} agent ({ctx['models'][agent]}): {time.monotonic() - t0:.0f}s")
        return agent, out
    except Exception as e:
        log(f"  {agent} agent ({ctx['models'][agent]}): failed {type(e).__name__}: {str(e)[:120]}")
        return agent, None


def _merge(ctx: dict, f: T.Filled, outs: dict) -> T.Filled:
    """The current resume with each agent's new section (an agent that failed keeps its old section)."""
    g = f.model_copy(deep=True)
    r = ctx["r"]
    if isinstance(outs.get("experience"), T.Repair):
        T.apply_repair(g, outs["experience"])
    elif outs.get("experience") is not None:
        g.roles = [T.FillRole(role=i, bullets=getattr(outs["experience"], f"role_{i}")) for i in range(len(r.roles))]
    T.normalize_originals(r, g)                      # F18: a claimed `original` that is not the template's text is new text
    if outs.get("skills") is not None:
        g.skills = outs["skills"].skills
        g.skills, dropped = SG.enforce(g.skills, SG.forbidden_set(ctx.get("graph")))      # related skills you do not have are never added
        if dropped:
            ctx["log"](f"  skills: removed related skill(s) not in your files: {', '.join(dropped)}")
    if outs.get("summary") is not None and 80 <= len(outs["summary"].summary) <= 600:
        g.summary = outs["summary"].summary
    if outs.get("projects") is not None:
        proj = ctx["job_dir"] / "projects.json"
        new = A.apply_project_fixes(proj.read_text(), outs["projects"].projects)
        if new and outs["projects"].swaps:
            st = json.loads(new)
            cands = ctx.get("cards", {})
            for sw in outs["projects"].swaps:
                repo = sw.entry.repo.lower()
                if 1 <= sw.project <= st["kept"] and repo in cands and \
                        repo not in {e["repo"].lower() for e in st["ranked"]}:
                    st["ranked"][sw.project - 1] = sw.entry.model_dump()
                    st["cards"][repo] = {"url": cands[repo]["url"], "full_name": cands[repo]["full_name"]}
                    st["years"][sw.entry.repo] = P.month_year(cands[repo].get("pushed_at", ""))
                    ctx["log"](f"  projects: swapped in {sw.entry.repo}")
            new = json.dumps(st, indent=1)
        if new:
            st = json.loads(new)
            for e in st["ranked"]:  # an over-long rewrite takes 5 lines: keep whole sentences up to the limit
                if len(e["paragraph"]) > P.MAX_PARAGRAPH:
                    e["paragraph"] = P._sentences(e["paragraph"], P.MAX_PARAGRAPH - 10)
            proj.write_text(json.dumps(st, indent=1))
    return g


# --- nodes -------------------------------------------------------------------------------------------------------

def write_initial(state: State) -> dict:
    """Projects first (the page fitter needs them), experience alongside; then skills; then the summary."""
    ctx = state["ctx"]
    r, jd, log = ctx["r"], ctx["jd"], ctx["log"]
    log("initial draft")
    progress(log, "documents", "running", "First draft: experience, projects, skills, summary")

    def projects():
        token = optional_secret("github", "token")
        chosen = P.choose(r, jd, llm.writer(8000, timeout=300, model=ctx["models"]["projects"]),
                          github.profile_user(r.tex), token, rediscover=False, log=log,
                          **({'cache':ctx['cache'],'model_name':ctx['models']['projects']} if ctx.get('incremental') else {}))
        P.save_state(ctx["job_dir"], list(chosen.entries), chosen)
        ctx["candidates"] = chosen.candidates
        ctx["cards"] = {c["full_name"].lower(): c for _, c in chosen.candidates}
        return chosen

    with ThreadPoolExecutor(2) as ex:
        fut_p = ex.submit(with_context(projects))
        _, exp = _run_agent(ctx, "experience", None, [], "")
        fut_p.result()
    if exp is None:
        raise RuntimeError("the experience agent failed on the first draft")
    roles = [T.FillRole(role=i, bullets=getattr(exp, f"role_{i}")) for i in range(len(r.roles))]
    f = T.Filled(summary=T.summary_text(r.summary), roles=roles,
                 skills=[T.SkillLine(category=c, terms=[T.to_plain(v)]) for c, v in r.skills][:8] or
                 [T.SkillLine(category="Skills", terms=["Python"])] * 3, notes=exp.notes)
    _, sk = _run_agent(ctx, "skills", f, [], "")
    if sk is not None:
        f.skills = sk.skills
    _, su = _run_agent(ctx, "summary", f, [], "")
    if su is not None:
        f.summary = su.summary
    T.backfill(r, jd, f)
    return {"f": f, "tasks": {}, "last": "", "ats_round": 0, "layout_pass": 0, "history": ["draft"]}


def _fit(ctx: dict, f: T.Filled) -> tuple[str, list[str], int, str, int, int]:
    """Render f with the best projects layout that fits (all projects in full first; see P.layouts).
    Returns (tex, problems, pages, err, kept, n_short)."""
    tex, problems = T.apply_fill(ctx["r"], f)
    job_dir = ctx["job_dir"]
    fitted = P.fit(tex, job_dir, lambda t: T.compile_pdf(t, job_dir),
                   accept=lambda: P.project_layout_ok(job_dir / "resume.pdf"))
    if not fitted:
        return (tex, problems, *T.compile_pdf(tex, job_dir), 0, 0)
    tex, pages, err, kept, short = fitted
    if pages == 1 and not err:
        P.update_state(job_dir, kept, short)
    return tex, problems, pages, err, kept, len(short)


def _removable(ctx: dict, f: T.Filled) -> tuple[int, int] | None:
    """(role, bullet index) of the bullet to drop to make room: the last bullet of the role with the most bullets
    (>= 4, then >= 3... never below 3), whose removal leaves every REQUIRED JD item covered."""
    base = set(T.fill_checks(ctx["r"], ctx["jd"], f)[0])
    for e in sorted(f.roles, key=lambda e: -len(e.bullets)):
        if len(e.bullets) <= 3:
            break
        for j in range(len(e.bullets) - 1, -1, -1):
            g = f.model_copy(deep=True)
            next(x for x in g.roles if x.role == e.role).bullets.pop(j)
            if set(T.fill_checks(ctx["r"], ctx["jd"], g)[0]) <= base:
                return e.role, j
    return None


def plan_space(ctx: dict, f: T.Filled) -> tuple[T.Filled, str, list[str], int, str]:
    """Space planner: full project paragraphs come before extra bullets. Fit the projects; while fewer than
    min(3, available) projects fit in full, drop a 4th bullet (one whose duty another bullet covers) and refit.
    Returns the planned resume and its compiled render."""
    st = json.loads((ctx["job_dir"] / "projects.json").read_text())
    want = min(3, len(st["ranked"]))
    best = None
    for _ in range(5):
        tex, problems, pages, err, kept, n_short = _fit(ctx, f)
        full = kept - n_short
        key = (pages == 1 and not err, min(full, want), sum(len(e.bullets) for e in f.roles))
        if best is None or key > best[0]:
            best = (key, f, tex, problems, pages, err)
        if (pages == 1 and not err and full >= want) or not (cut := _removable(ctx, f)):
            break
        f = f.model_copy(deep=True)
        role, j = cut
        dropped = next(x for x in f.roles if x.role == role).bullets.pop(j)
        ctx["log"](f"  space planner: dropped role_{role} bullet '{dropped.text[:50]}...' to fit full projects")
    f = best[1]
    tex, problems, pages, err, _, _ = _fit(ctx, f)  # the chosen plan on disk (resume.pdf, projects.json)
    return f, tex, problems, pages, err


def render(state: State) -> tuple[T.Filled, str, list[str], int, str]:
    """Render the current resume content to a PDF and check the page.

    Args:
        state: The graph state.

    Returns:
        The content, its LaTeX, the problems found, the page count and the compile error (if any).
    """
    return plan_space(state["ctx"], state["f"])


def check_layout(state: State) -> dict:
    """Validator: checks the resume against the known failure list (one page, bullet rules, JD coverage, page filled,
    template bullets and skills used where they fit, enough full projects, no empty skills line), then the rendered
    page (tools/layout.py), then a content review; every finding goes to the agent whose section it is about."""
    ctx = state["ctx"]
    r, jd, job_dir, log = ctx["r"], ctx["jd"], ctx["job_dir"], ctx["log"]
    f, tex, problems, pages, err = render(state)
    tasks: dict[str, list[str]] = {a: [] for a in AGENTS}
    padding_task = None
    if err:
        tasks["experience"].append(f"The LaTeX failed to compile ({err}); write plain text only.")
    if pages and pages > 1:
        tasks["experience"].append(f"The resume is {pages} pages; it must be one: shorten every bullet to one line "
                                   f"(about {T.BULLET_TARGET} characters, at most {T.BULLET_MAX}) and drop the least relevant bullet of the longest role.")
        tasks["skills"].append("The resume overflows one page: at most 2 tools per term, merge near-duplicates.")
    tasks["experience"] += T.duplicates(r, f) + T.quality(r, jd, f)
    slots = len(r.roles) * T.MAX_BULLETS          # the most bullets the page layout allows (F18)
    uncovered = T.fill_checks(r, jd, f, slots)[0]
    if uncovered:
        tasks["experience"].append(f"REQUIRED JD item indices not covered by any NEW bullet: {uncovered}.")
    tasks["summary"] += summary_issues(f.summary, bool(pages and pages > 1))
    empty = [sl.category for sl in f.skills if not sl.render()]
    if empty:
        tasks["skills"].append(f"These categories render empty (their terms repeat other lines): {empty}; give each "
                               "real, different skills or drop it.")
    lay = layout.check(job_dir / "resume.pdf") if pages == 1 and not err else None
    proj_path = job_dir / "projects.json"
    st = json.loads(proj_path.read_text()) if proj_path.exists() else None
    if st:
        tasks["projects"] += P.content_findings(st)
    if lay:
        for issue in lay.issues:
            if issue.startswith("Technical Skills") and "fills only" in issue:
                continue  # one grouped, optional padding task below; not N repeated repair demands
            agent = ("skills" if issue.startswith("Technical Skills") else
                     "projects" if issue.startswith(("the project", "text overflows")) else "experience")
            tasks[agent].append(issue)
        used = " ".join(x for sl in f.skills for x in sl.render()).lower()
        # Keep the source categories: a flat padding pool caused unrelated ML
        # terms to be inserted under Frontend and Observability in live drafts.
        pool = {category: [t.strip() for t in T.to_plain(items).split(",")
                           if t.strip() and t.strip().lower() not in used]
                for category, items in r.skills}
        pool = {category: terms for category, terms in pool.items() if terms}
        if lay.short_skills and not state.get("skills_padding_attempted"):
            room = {k: f"about {round((1 - v) * 115)} more characters" for k, v in lay.skill_fill.items() if v < 0.85}
            padding_task = (f"Short skills lines: {room}. Add a posting-relevant owned skill ONLY if it belongs "
                                   f"in that category. Unused template skills, grouped by their source category: {pool}. "
                                   "Merge compatible categories or leave a shorter line if no fitting term is available. "
                                   "Never move an unrelated term into a category to fill space, and never wrap a category "
                                   "onto a new line for one or two words.")
        full_projects = (st["kept"] - len(st["short"])) if st else 0
        if lay.free_pt and lay.free_pt > 12 and st and st["short"]:
            tasks["projects"].append(f"About {round(lay.free_pt / 11)} line(s) are free but projects are in their short "
                                     "form: make every paragraph a full 3 sentences of 300-380 characters.")
        if lay.free_pt and lay.free_pt > 12 and full_projects >= min(3, len(st["ranked"]) if st else 0):
            # space beyond full projects: more bullets (the space planner trims them again if projects need room)
            thin = [e for e in f.roles if len(e.bullets) < T.MAX_BULLETS]
            if thin:
                spare = {e.role: [T.to_plain(b) for j, b in enumerate(r.roles[e.role].bullets)
                                  if T.eligible(r.roles[e.role], j, any(any(x.pool) for x in r.roles))
                                  and T.to_plain(b)[:40].lower() not in " ".join(x.text for x in e.bullets).lower()]
                         for e in thin}
                tasks["experience"].append(
                    f"About {round(lay.free_pt / 11)} line(s) of the page are free: add bullets (up to {T.MAX_BULLETS} "
                    f"per role) to {[f'role_{e.role} ({len(e.bullets)} now)' for e in thin]}, preferring the candidate's "
                    f"own template bullets that fit the posting: {spare}. Keep every REQUIRED item covered.")
    if st and st["kept"] - len(st["short"]) < min(2, len(st["ranked"])):
        tasks["summary"].append(f"Projects are squeezed to short forms: shorten the Summary to at most {T.SUMMARY_TIGHT} characters.")
        tasks["skills"].append("Projects are squeezed to short forms: keep every category to one line.")
    if st and st["kept"] < min(3, len(st["ranked"])):
        tasks["projects"].append(f"Only {st['kept']} project(s) fit: make every paragraph 300-340 characters so "
                                 "another fits.")
        tasks["experience"].append("Room is needed for one more project: shorten the two longest bullets to one line.")
    if st:
        known_titles = {t.lower() for t, _ in P.known_entries(r.tex).values()}  # the user's own titles stay as they are
        for i, e in enumerate(st["ranked"][:st["kept"]], 1):
            if "/" not in e["title"] and e["title"].lower() == e["repo"].split("/")[-1].lower() and \
                    e["title"].lower() not in known_titles:
                tasks["projects"].append(f"Project {i} is titled with its bare repo name '{e['title']}': give it a "
                                         "real project title and a full 3-sentence description.")
    reviewed_content = _reviewed_content(ctx, f, state) if pages == 1 and not err else {}
    skill_tasks, padding_attempted = _skill_review_tasks(reviewed_content, padding_task,
        state.get("skills_padding_attempted", False), invalid=bool(empty))
    tasks["skills"] += skill_tasks
    for fd in reviewed_content.get("content_findings", []):
        if fd.severity == "suggestion":
            log(f"content review suggestion (no automatic rewrite): {fd.issue}")
        elif fd.agent != "skills":
            tasks[fd.agent].append(fd.issue)
    if pages == 1 and not err and not state.get("reviewed"):
        # Vision runs once per cycle; semantic findings above are bound to each
        # visible content revision, so later edits cannot bypass the review.
        tasks["experience"] += [v for v in layout.review(job_dir / "resume.pdf", log=log)
                                if v.split(" ", 1)[0] in {"odd_characters", "overlap", "overflow", "alignment"}]
        reviewed = True
    else:
        reviewed = state.get("reviewed", False)
    tasks = {a: t for a, t in tasks.items() if t}
    n = sum(map(len, tasks.values()))
    progress(log, "documents", "running", "Layout and quality check", state.get("layout_pass", 0) + 1, LAYOUT_PASSES + 1,
             f"{pages} page(s), {n} finding(s)")
    log(f"validator pass {state.get('layout_pass', 0)}: {pages} page(s), {n} finding(s) "
        f"{ {a: len(t) for a, t in tasks.items()} }" +
        "".join(f"\n    [{a}] {t[:200]}" for a, ts in tasks.items() for t in ts))
    return {"f": f, "tasks": tasks, "layout_issues": [x for t in tasks.values() for x in t],
            "hard": hard_issues(ctx, f) + reviewed_content.get("semantic_hard", []),
            "layout_pass": state.get("layout_pass", 0) + 1, "history": [f"validator {n}"],
            "reviewed": reviewed, "skills_padding_attempted": padding_attempted, **reviewed_content}


SUMMARY_MAX = T.SUMMARY_MAX          # one definition (tools/tailor.py) for the schema, the prompts and this check (F18)


def summary_issues(summary: str, overflow: bool) -> list[str]:
    """The Summary's own rules: short (it shares the page), 2-3 sentences, no claims beyond the candidate's declared
    facts (years above the declared total, internships), no copied qualification list."""
    import re
    out = []
    sentences = [x for x in re.split(r"(?<=[.!?])\s+", summary.strip()) if x]
    if len(summary) > SUMMARY_MAX or len(sentences) > 3:
        out.append(f"The Summary is {len(summary)} characters / {len(sentences)} sentences: rewrite it in 2-3 "
                   f"sentences, at most {T.SUMMARY_TARGET} characters (never over {SUMMARY_MAX}).")
    elif overflow and len(summary) > T.SUMMARY_TIGHT + 30:
        out.append(f"The page overflows: shorten the Summary to at most {T.SUMMARY_TIGHT} characters.")
    years = T.declared_claims().get("years_experience")
    claimed = [int(n) for n in re.findall(r"(\d+)\+?\s*(?:years?|yrs)", summary, re.I)]
    if years and any(n > years for n in claimed):
        out.append(f"The Summary claims more than the declared {years} years of experience.")
    if re.search(r"(?i)\binternships?\b|\bor related (field|discipline)|\bsuch as\b.*\bor\b", summary):
        out.append("The Summary copies the posting's qualification wording (internships, 'or related discipline', "
                   "'such as X or Y'): state what the candidate did, from the resume, never the posting's list.")
    return out


def hard_issues(ctx: dict, f: T.Filled) -> list[str]:
    """What disqualifies a version whatever its ATS score (the user's review list): not one page, a one-line project
    description, a blank band at the bottom, an empty skills line. Reads the resume.pdf on disk."""
    pdf = ctx["job_dir"] / "resume.pdf"
    lay = layout.check(pdf)
    out = [i for i in lay.issues if i.startswith(("the resume is", "the project", "text overflows"))]
    proj = ctx["job_dir"] / "projects.json"
    if proj.exists():  # a short form only adds an extra project: at least 2 (or all available) must be in full
        st = json.loads(proj.read_text())
        out += P.content_findings(st)
        full, want = st["kept"] - len(st["short"]), min(2, len(st["ranked"]))
        if full < want:
            out.append(f"only {full} project(s) in full (short forms: {st['short']}); shorten the Summary or skills "
                       "to make room")
    if lay.pages == 1 and lay.free_pt is not None and lay.free_pt > BLANK_HARD_PT:
        out.append(f"{round(lay.free_pt / 11)} blank lines at the bottom")
    out += [f"empty skills line {sl.category}" for sl in f.skills if not sl.render()]
    return out


def content_review(ctx: dict, f: T.Filled) -> list:
    """The validator agent's read of the whole resume against the posting (judgement code can't make)."""
    if ctx.get('incremental'):
        from jobagent.tools.incremental_documents import review
        return review(ctx, f)
    proj = ctx["job_dir"] / "projects.json"
    job = {"title": ctx["jd"].title, "responsibilities": ctx["jd"].responsibilities,
           "basic": ctx["jd"].basic_qualifications, "preferred": ctx["jd"].preferred_qualifications,
           "skills": ctx["jd"].technical_skills}
    msgs = [SystemMessage(REVIEW_PROMPT),
            HumanMessage(f"POSTING: {json.dumps(job)}\n\n" + _context(ctx, f, proj.read_text() if proj.exists() else None, visible_only=True))]
    try:
        out = llm.structured_invoke(llm.writer(4000, timeout=120, model=ctx["models"]["router"]), Review, msgs,
                                    log=ctx["log"], deadline_s=180)
        ctx["log"](f"  content review: {len(out.findings)} finding(s)")
        return out.findings
    except Exception as e:
        ctx["log"](f"  content review failed ({type(e).__name__})")
        raise


def _displayed_projects(st: dict) -> list[dict]:
    short = set(st.get("short", []))
    return [{"repo": e.get("repo"), "title": e.get("title"), "stack": e.get("stack"),
             "variant": "short, two sentences" if e.get("repo") in short and e.get("short") else "full, three sentences",
             "text": e.get("short") if e.get("repo") in short and e.get("short") else e.get("paragraph")}
            for e in st.get("ranked", [])[:st.get("kept", 0)]]


def _content_hash(ctx: dict, f: T.Filled) -> str:
    """Bind a semantic review to visible content, not an earlier layout pass."""
    path = ctx["job_dir"] / "projects.json"
    visible = _displayed_projects(json.loads(path.read_text()) if path.exists() else {})
    value = {"summary": f.summary, "roles": [(e.role, [b.text for b in e.bullets]) for e in f.roles],
             "skills": [(s.category, s.render()) for s in f.skills if s.render()], "projects": visible}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _reviewed_content(ctx: dict, f: T.Filled, state: dict) -> dict:
    fingerprint = _content_hash(ctx, f)
    if fingerprint == state.get("content_hash"):
        findings, error = state.get("content_findings", []), state.get("content_error", "")
    else:
        try:
            findings, error = content_review(ctx, f), ""
        except Exception as exc:
            findings, error = [], f"skills content review unavailable ({type(exc).__name__})"
    hard = [f"{fd.agent}: {fd.issue}" for fd in findings if (ctx.get("incremental") or fd.agent in {"skills", "projects"}) and fd.severity == "error"]
    if error:
        hard.append(error)
    return {"content_hash": fingerprint, "content_findings": findings, "content_error": error, "semantic_hard": hard}


def _skill_review_tasks(review: dict, padding: str | None, attempted: bool, invalid: bool = False) -> tuple[list[str], bool]:
    errors = [fd.issue for fd in review.get("content_findings", []) if fd.agent == "skills" and fd.severity == "error"]
    # Correctness gets the budget. Never reintroduce padding after repairing a
    # category error, and never edit solely because a critic prefers finer labels.
    tasks = list(errors)
    if padding and not attempted and not errors and not invalid and not review.get("content_error"):
        tasks.append(padding)
    return tasks, bool(attempted or padding or errors or invalid)


def after_layout(state: State) -> str:
    """Keep fixing while the validator has findings (up to LAYOUT_PASSES); then score."""
    if state.get("content_error"):
        return "score"  # an unavailable reviewer is not a reason to rewrite sound content
    if state["tasks"] and state["layout_pass"] <= LAYOUT_PASSES:
        return "fix"
    return "score"


def edit_sections(state: State) -> dict:
    """The agents with tasks edit their sections in parallel (projects.json is backed up for a rollback)."""
    ctx, f, tasks = state["ctx"], state["f"], state["tasks"]
    ctx["log"]("editing: " + ", ".join(f"{a} ({len(t)})" for a, t in tasks.items()))
    progress(ctx["log"], "documents", "running", "Fixing findings", detail=", ".join(f"{a} ({len(t)})" for a, t in tasks.items()))
    if ctx.get('incremental'):
        other = {a: t for a, t in tasks.items() if a != 'summary'}
        with ThreadPoolExecutor(len(other) or 1) as ex:
            outs = dict(ex.map(with_context(lambda a: _run_agent(ctx, a, f, other[a], state.get('last', ''))), other))
        updated = _merge(ctx, f, outs)
        summary_tasks = list(tasks.get('summary', []))
        if any(value is not None for value in outs.values()):
            summary_tasks.append('Reconcile the summary with the final changed experience, projects and skills; preserve accurate text.')
        if summary_tasks:
            _, summary = _run_agent(ctx, 'summary', updated, summary_tasks, state.get('last', ''))
            updated = _merge(ctx, updated, {'summary': summary})
        return {'f': updated, 'tasks': {}}
    with ThreadPoolExecutor(len(tasks) or 1) as ex:
        outs = dict(ex.map(with_context(lambda a: _run_agent(ctx, a, f, tasks[a], state.get("last", ""))), list(tasks)))
    return {"f": _merge(ctx, f, outs), "tasks": {}}


def score(state: State) -> dict:
    """ATS validator: score the current page (one page only); keep it if better than the best so far, else roll back."""
    ctx = state["ctx"]
    job_dir, jd, log = ctx["job_dir"], ctx["jd"], ctx["log"]
    f, tex, problems, pages, err = render(state)
    rnd = state.get("ats_round", 0)
    best = state.get("best")
    progress(log, "documents", "running", ("ATS baseline" if rnd == 0 else "ATS round") if ctx['sites'] else 'Document validation',
             rnd if ctx['sites'] else None, ATS_ROUNDS if ctx['sites'] else None,
             'scoring on the checker sites' if ctx['sites'] else 'Optional ATS scans skipped')
    if pages != 1 or err:
        log(f"ats round {rnd}: page not valid ({pages} pages {err}); rolled back to the best version")
        return _rollback(state, "UNDONE: the edit broke the one-page layout; keep lines short") if best else \
            {"ats_round": rnd + 1, "last": "the page overflowed: shorten bullets"}
    hard = list(dict.fromkeys(hard_issues(ctx, f) + state.get("semantic_hard", [])))
    if state.get("content_hash") and _content_hash(ctx, f) != state["content_hash"]:
        hard.append("skills content changed after its semantic review")
    if hard:
        log(f"ats round {rnd}: document checks failed; no scans spent: {hard}")
        return _rollback(state, "UNDONE: document checks failed: " + "; ".join(hard)) if best else {
            "ats_round": rnd + 1, "hard": hard, "last": "document checks failed: " + "; ".join(hard),
        }
    budget_sites = tuple(name for name in ctx["sites"] if name in ats_sites.BUDGET_SITES)
    first_score = best is None
    sites = tuple(name for name in ctx["sites"] if first_score or name not in budget_sites)
    ev = A.evaluate(job_dir, jd, ctx["jd_text"], rnd, sites, log)
    required = [name for name in sites if name not in ats_sites.OPTIONAL_SITES]

    missing_sites = [name for name in required if name not in ev["completed"]]

    if missing_sites:
        again = A.evaluate(job_dir, jd, ctx["jd_text"], rnd, tuple(missing_sites), log)
        ev["scores"].update(again["scores"])
        ev["missing"].update(again["missing"])

        for key in ("advice", "template", "keep", "completed"):
            ev[key] += again[key]
        ev.setdefault("advice_by_site", {}).update(again.get("advice_by_site", {}))

        missing_sites = [name for name in required if name not in ev["completed"]]

    if missing_sites:
        log(f"ats round {rnd}: {missing_sites} failed twice; this round doesn't count")
        if best:
            return _rollback(state, "a checker failed; the same version will be rescored")
        return {"ats_round": rnd + 1, "last": ""}
    budget_feedback = state.get("budget_feedback", {})
    if first_score:
        budget_feedback = {
            "missing": {name: ev["missing"].get(name, []) for name in budget_sites},
            "advice": [a for name in budget_sites for a in ev.get("advice_by_site", {}).get(name, [])],
            "scores": {name: value for name, value in ev["scores"].items() if name in budget_sites},
        }
    else:
        ev["missing"].update(budget_feedback.get("missing", {}))
        ev["advice"] = list(dict.fromkeys(ev["advice"] + budget_feedback.get("advice", [])))
    # Compare revisions using the same unlimited scorers; a budget score belongs to its original PDF.
    avg = A.average({name: value for name, value in ev["scores"].items() if name not in budget_sites})
    n_layout = len(layout.check(job_dir / "resume.pdf").issues)
    log(f"ats round {rnd}: unlimited-site comparison average {avg} {ev['scores']}, layout issues {n_layout}, hard issues {hard}")
    progress(log, "documents", "running", ("ATS baseline" if rnd == 0 else "ATS round") if ctx['sites'] else 'Document validation',
             rnd if ctx['sites'] else None, ATS_ROUNDS if ctx['sites'] else None,
             f'average {avg} (target {TARGET:g})' if ctx['sites'] else 'Optional ATS scans skipped')
    if best:
        better = (len(hard) < len(best["hard"]) or
                  (len(hard) == len(best["hard"]) and
                   (avg > best["avg"] or (avg >= best["avg"] - A.NOISE and (n_layout < best["layout"] or ctx.get("incremental") and n_layout == best["layout"])))))
        if not better:
            return _rollback(state, f"UNDONE: the last edits moved the scores to {ev['scores']} (average {avg}, best "
                                    f"{best['avg']}) with page problems {hard}; try different wording or other items",
                             ev)
    proj = job_dir / "projects.json"
    new_best = {"avg": avg, "scores": ev["scores"], "f": f.model_copy(deep=True), "layout": n_layout, "hard": hard,
                "tex": (job_dir / "resume.tex").read_text(), "pdf": (job_dir / "resume.pdf").read_bytes(),
                "projects": proj.read_text() if proj.exists() else None, "filled": f.model_dump_json(indent=1) if hasattr(f, "model_dump_json") else None, "ev": ev,
                "content_suggestions": [fd.issue for fd in state.get("content_findings", []) if fd.severity == "suggestion"]}
    last = f"KEPT: the scores are {ev['scores']} (average {avg})" if best else ""
    return {"best": new_best, "scores": ev["scores"], "ats_round": rnd + 1, "last": last,
            "budget_feedback": budget_feedback,
            "history": [f"ats {avg}"]}


def _version_files(best: dict) -> dict:
    """A kept version as the bundle of files that describe it: its source, PDF, projects layout and structure. A version that
    had no projects layout means NO such file (a later attempt's must not survive a rollback to it)."""
    files = {"resume.tex": best["tex"], "resume.pdf": best["pdf"], "projects.json": best.get("projects")}
    if best.get("filled"):
        files["filled.json"] = best["filled"]
    elif hasattr(best.get("f"), "model_dump_json"):
        files["filled.json"] = best["f"].model_dump_json(indent=1)
    return files


def _rollback(state: State, why: str, ev: dict | None = None) -> dict:
    ctx, best = state["ctx"], state["best"]
    job_dir = ctx["job_dir"]
    bundle.publish(job_dir, _version_files(best))          # the whole version, or nothing (F25)
    out = {"f": best["f"].model_copy(deep=True), "ats_round": state.get("ats_round", 0) + 1, "last": why,
           "history": ["rollback"]}
    if ev:  # the rejected version's findings still tell the agents what's missing
        out["best"] = {**best, "ev": {**best["ev"], "missing": ev["missing"], "advice": ev["advice"]}}
    return out


def after_score(state: State) -> str:
    """Decide where the graph goes after scoring.

    Args:
        state: The graph state.

    Returns:
        `done` when the result is final (a content error, a dry run with no ATS sites, the target reached, or the rounds used up); otherwise the name of the next node.
    """
    best = state.get("best")
    if state.get("content_error") or (not state["ctx"]["sites"] and state.get("hard") and not best):
        return "done"
    if not state["ctx"]["sites"] and best:  # dry run (no ATS sites): the drafted, layout-checked page is the result
        return "done"
    estimated_scores = {**state.get("budget_feedback", {}).get("scores", {}),
                        **(best["scores"] if best else {})}
    estimate = ats_sites.score_summary(estimated_scores, state["ctx"]["sites"])["average"]
    if (best and estimate is not None and estimate >= TARGET and not best["hard"]) or state.get("ats_round", 0) > ATS_ROUNDS:
        return "done"
    return "route"


def route(state: State) -> dict:
    """ATS validator's router: missing keywords and advice -> tasks per agent."""
    ctx, best = state["ctx"], state.get("best")
    if not best and state.get("hard"):
        tasks: dict[str, list[str]] = {}
        for issue in state["hard"]:
            agent = "skills" if issue.startswith("skills") else "projects" if "project" in issue else "experience"
            tasks.setdefault(agent, []).append(issue)
        return {"tasks": tasks, "layout_pass": 0}
    if not best:  # nothing scored yet: the page never fit
        return {"tasks": {"experience": [f"The resume overflows one page: every bullet to one line (about {T.BULLET_TARGET} characters), "
                                         "drop the least relevant bullet of the longest role."],
                          "projects": ["The resume overflows one page: make every paragraph 300-350 characters."]},
                "layout_pass": 0}
    ev = best["ev"]
    missing = A.merged_missing(ev["missing"])[:20]
    msgs = [SystemMessage(ROUTER_PROMPT),
            HumanMessage(f"SCORES: {best['scores']}\n\nMISSING:\n" + "\n".join(f"- {m}" for m in missing) +
                         "\n\nADVICE:\n" + "\n".join(f"- {a}" for a in ev["advice"]) +
                         f"\n\nLAST ROUND: {state.get('last', '')}\n\n" + _context(ctx, state["f"], None))]
    try:
        plan = llm.structured_invoke(llm.writer(4000, timeout=300, model=ctx["models"]["router"]), RoutePlan, msgs,
                                     log=ctx["log"])
        tasks: dict[str, list[str]] = {}
        for t in plan.tasks:
            tasks.setdefault(t.agent, []).append(t.instruction)
    except Exception as e:
        ctx["log"](f"router failed ({type(e).__name__}); every agent gets the full list")
        tasks = {a: [f"Work in what fits your section: {missing}; advice: {ev['advice']}"] for a in AGENTS}
    for h in best.get("hard", []):  # page problems the kept version still has
        if h.startswith("only "):  # projects squeezed to short forms: the Summary and skills give the room back
            tasks.setdefault("summary", []).append(f"Page problem: {h}. Shorten the Summary to at most {T.SUMMARY_TIGHT} characters.")
            tasks.setdefault("skills", []).append(f"Page problem: {h}. Keep every category to one line.")
            continue
        agent = "projects" if "project" in h or "blank" in h else "skills" if "skills" in h else "experience"
        tasks.setdefault(agent, []).append(f"Page problem to fix: {h}")
    ctx["log"]("routed: " + ", ".join(f"{a} {len(t)}" for a, t in tasks.items()))
    return {"tasks": tasks, "layout_pass": 0, "reviewed": False, "skills_padding_attempted": False}


def build_graph(start="write"):
    """Build the LangGraph that writes, lays out, edits and scores a resume: write, check layout, fix, score, route, and the loops between them.

    Returns:
        The compiled graph.
    """
    g = StateGraph(State)
    g.add_node("write", write_initial)
    g.add_node("check_layout", check_layout)
    g.add_node("fix", edit_sections)
    g.add_node("score", score)
    g.add_node("route", route)
    g.add_node("edit", edit_sections)
    g.add_edge(START, start)
    g.add_edge("write", "check_layout")
    g.add_conditional_edges("check_layout", after_layout, {"fix": "fix", "score": "score"})
    g.add_edge("fix", "check_layout")
    g.add_conditional_edges("score", after_score, {"route": "route", "done": END})
    g.add_edge("route", "edit")
    g.add_edge("edit", "check_layout")
    return g.compile()


def run_job(job_dir: Path, slug: str, models: dict, sites=SITES, log=print, *, document_strategy=None, ats_refine=False) -> dict:  # noqa: C901
    """One job through the agent graph; leaves the best one-page version in job_dir."""
    started = time.monotonic()
    panel_history = []
    if sites and all(name in ats_sites.FEEDBACK_ONLY_SITES for name in sites):
        raise ValueError("The ATS improvement loop requires at least one numeric checker alongside SkillsFirst's written feedback.")
    s = get_settings()
    strategy = document_strategy or getattr(s, 'document_strategy', 'current')
    if strategy not in ('current','incremental'): raise ValueError('document strategy must be current or incremental')
    if ats_refine and strategy != 'incremental': raise ValueError('--ats-refine requires incremental documents')
    if strategy == 'current' and sites and not any(name not in ats_sites.BUDGET_SITES and name not in ats_sites.FEEDBACK_ONLY_SITES
                                                 for name in sites):
        raise ValueError("The multi-agent loop needs an unlimited numeric checker alongside limited-credit sites.")
    from jobagent.tools import documents
    documents.begin(job_dir)       # fail closed: an in-progress record replaces the manifest before anything is generated
    jd = JobDescription.model_validate_json((job_dir / "jd.json").read_text())
    template_text = (s.data_dir / "templates" / "resume.tex").read_text()
    documents.validate_template(template_text)
    ctx = {"job_dir": job_dir, "jd": jd, "company": (s.state_dir / "companies" / f"{slug}.md").read_text(),
           "r": T.parse(template_text),
           "jd_text": (job_dir / "jd.txt").read_text() if (job_dir / "jd.txt").exists() else "",
           "models": models, "log": log, "sites": () if strategy == "incremental" else sites}
    ctx["graph"] = SG.for_tailor(job_dir, jd, ctx["r"])               # related skills (step 4c); {} when off or empty
    SG.guard_resume(ctx["r"], job_dir)                                # every render of this resume drops skills that are not the candidate's
    if strategy == 'incremental':
        from jobagent.tools.incremental_documents import cache_for
        from jobagent.tools import job_context, applicant
        ctx.update(incremental=True, cache=cache_for(job_dir))
        ctx['source_snapshot'] = job_context.snapshot(job_dir,company=ctx['company'],
            candidate=applicant.load(),template=template_text,claims=T.declared_claims())
    final = build_graph().invoke({"ctx": ctx, "history": []}, {"recursion_limit": 200})
    best = final.get("best")
    if best:
        bundle.publish(job_dir, _version_files(best))      # source, PDF, projects layout and structure: one version (F25)
        if strategy == 'incremental' and sites:
            from jobagent.tools.incremental_documents import final_panel
            best, panel_history = final_panel(ctx, final, sites, ats_refine)
            bundle.publish(job_dir, _version_files(best))
        budget = tuple(name for name in sites if name in ats_sites.BUDGET_SITES) if strategy == 'current' else ()
        if budget:
            final_ev = A.evaluate(job_dir, jd, ctx["jd_text"], final.get("ats_round", 0) + 1, budget, log)
            best["scores"] = {**{name: value for name, value in best["scores"].items() if name not in budget},
                              **final_ev["scores"]}
    if not best:                    # no valid best version: whatever the graph left behind is not a resume
        (job_dir / "resume.pdf").unlink(missing_ok=True)
    lay = layout.check(job_dir / "resume.pdf").issues if best else []
    hard = best.get("hard", []) if best else final.get("hard", [])
    claims = (documents.claims_from_filled(best["f"]) + SG.violations(job_dir, best["f"])) if best else []
    hist = [h for h in final.get("history", []) if h.startswith("ats ")]
    summary = ats_sites.write_summary(job_dir, best["scores"] if best else {}, sites)
    avg = summary["average"]
    if strategy == 'incremental':
        from jobagent.tools.artifact_cache import write_private
        elapsed = time.monotonic()-started
        ats_wait = sum(r['seconds'] for r in panel_history)
        write_private(job_dir/'incremental-metrics-v1.json', {'version':1,'stages':ctx['cache'].events,
            'elapsed_s':elapsed,'external_ats_s':ats_wait,'generation_s':max(0,elapsed-ats_wait),
            'ats_rounds':len(panel_history)})
    return {"document_strategy": strategy, "average": avg, "scores": summary["scores"],
            "template_sha256": documents.template_fingerprint(template_text),
            "average_sites": summary["sites"], "average_missing": summary["missing"],
            "average_complete": summary["complete"],
            "history": [p['panel']['average'] for p in panel_history] if strategy=='incremental' else [float(h.split()[1]) for h in hist],
            "history_metric": "configured final-panel averages" if strategy=='incremental' else "unlimited-site comparison",
            "reached": bool(best and avg is not None and avg >= TARGET),
            "pages": layout.page_count(job_dir / "resume.pdf") if best else None, "layout_issues": lay,
            "hard_issues": hard, "claims_issues": claims,
            "content_suggestions": best.get("content_suggestions", []) if best else [],
            "coverage_limited": (T.feasibility(ctx["r"], ctx["jd"], len(ctx["r"].roles) * T.MAX_BULLETS)
                                 if best and ctx.get("r") is not None and ctx.get("jd") is not None else []),
            "produced": bool(best),
            "failure": None if best else "no valid best version: document checks did not pass",
            "steps": final.get("history", [])}


def run(jobs: list[tuple[str | Path, str]], models: dict | None = None, sites=SITES, log=print) -> Path:
    """Every job through the agent team (jobs in parallel: their agent calls overlap, scoring takes turns)."""
    import shutil
    import traceback
    from datetime import datetime

    from jobagent.clock import now
    s = get_settings()
    models = {**DEFAULT_MODELS, **(models or {})}
    jobs = [(Path(d) if Path(d).is_absolute() else Path("/app") / d, slug) for d, slug in jobs]
    variant = f"same-{models['experience']}" if len(set(models.values())) == 1 else "team"
    root = s.outputs_dir / "experiments" / f"{datetime.now():%Y%m%d-%H%M}-multiagent-{variant}"
    root.mkdir(parents=True, exist_ok=True)
    (root / "models.json").write_text(json.dumps(models, indent=1))
    log(f"multi-agent experiment: {len(jobs)} jobs, models {models} -> {root}")

    def one(src_slug):
        src, slug = src_slug
        d = root / src.parent.name
        d.mkdir(parents=True, exist_ok=True)
        for name in ("jd.json", "jd.txt", "jd.md"):
            if (src / name).exists():
                shutil.copy(src / name, d / name)
        logf = (d / "run.log").open("a")
        jlog = lambda m: (logf.write(f"{now():%H:%M:%S} {m}\n"), logf.flush())
        t0 = time.monotonic()
        try:
            out = {"job": d.name, "status": "ok", **run_job(d, slug, models, sites, jlog)}
        except Exception as e:
            jlog(traceback.format_exc())
            out = {"job": d.name, "status": f"failed: {type(e).__name__}: {str(e)[:200]}"}
        out["minutes"] = round((time.monotonic() - t0) / 60, 1)
        jlog(f"result: {json.dumps(out)}")
        logf.close()
        return out

    with ThreadPoolExecutor(len(jobs)) as ex:
        results = list(ex.map(with_context(one), jobs))
    (root / "results.json").write_text(json.dumps(results, indent=1))
    lines = [f"# Multi-agent experiment ({now():%Y-%m-%d %H:%M} America/Phoenix)", "",
             "Agents and models: " + ", ".join(f"{k} = {v}" for k, v in models.items()), "",
             "| Job | Average | Rounds | " + " | ".join(sites) + " | Pages | Hard issues | Layout issues | Minutes | Status |",
             "|---|---|---|" + "---|" * len(sites) + "---|---|---|---|---|"]
    for r in results:
        sc = r.get("scores") or {}
        lines.append(f"| {r['job']} | {r.get('average', '-')}{' ✓' if r.get('reached') else ''} | "
                     f"{' → '.join(map(str, r.get('history') or [])) or '-'} | " +
                     " | ".join(str(sc.get(x, "-")) for x in sites) +
                     f" | {r.get('pages', '-')} | {'; '.join(r.get('hard_issues') or []) or 'none'} | "
                     f"{len(r.get('layout_issues') or [])} | {r['minutes']} | "
                     f"{r['status'][:80]} |")
    avgs = [r["average"] for r in results if r.get("average") is not None]
    lines += ["", f"Average over jobs: {round(sum(avgs) / len(avgs), 1) if avgs else '-'}"]
    (root / "results.md").write_text("\n".join(lines) + "\n")
    log(f"results: {root / 'results.md'}")
    return root
