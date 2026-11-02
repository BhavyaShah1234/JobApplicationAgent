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
average rises (or holds within the noise while the page improves), else undone and reported back; <= 5 ATS rounds.
"""

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
from jobagent.state import JobDescription
from jobagent.steps import ats as A
from jobagent.tools import ats_sites, github, layout
from jobagent.tools import projects as P
from jobagent.tools import tailor as T
from jobagent.tools.vault import open_vault

AGENTS = ("experience", "projects", "skills", "summary")
# Each agent's model from the per-section report of the 3-posting model run (outputs/experiments/
# 20261031-1910-doordash-adobe-symbotic/SECTIONS.md): experience = fewest bullet issues and best keyword sub-score,
# projects = paragraphs on target length, skills = fewest half-empty lines (after devstral), summary = HireFlow
# experience fit (fast), validator/router = glm-5-3. Override with --role agent=model or --same-model.
# Summary moved from llama4-scout-17b to devstral2-123b (2026-11-02): as an agent llama4-scout copied the posting's
# requirement list into a ~5-line Summary and ignored the length limit in every validator pass.
DEFAULT_MODELS = {"experience": "devstral2-123b", "projects": "gpt-oss-120b", "skills": "glm-5-3-flash",
                  "summary": "devstral2-123b", "router": "glm-5-3"}
SITES = tuple(x for x in ats_sites.DEFAULT_SITES if x not in ats_sites.BUDGET_SITES)
LAYOUT_PASSES, ATS_ROUNDS, TARGET = 5, 5, 90.0
BLANK_HARD_PT = 24  # more free height than this (about 2 lines) at the bottom disqualifies a version
# Tasks that need the whole Experience section rewritten (coverage, length); anything else is a targeted bullet edit
FULL_REWRITE = __import__("re").compile(r"(?i)not covered|pages?; it must be one|overflows|failed to compile|"
                                        r"add bullets")


# --- section outputs ----------------------------------------------------------------------------------------

def experience_schema(r: T.Resume) -> type[BaseModel]:
    fields = {f"role_{i}": (list[T.FillBullet], Field(min_length=T.MIN_BULLETS, max_length=T.MAX_BULLETS,
                                                      description=f"Bullets for: {role.title} at {role.company}"))
              for i, role in enumerate(r.roles)}
    fields["notes"] = (str, Field(max_length=600, description="How duties were distributed across roles"))
    return create_model("ExperienceOut", **fields)


class SkillsOut(BaseModel):
    skills: list[T.SkillLine] = Field(min_length=3, max_length=8)


class SummaryOut(BaseModel):
    summary: str = Field(max_length=800, description="Plain text, 2-3 complete sentences, <= 450 characters")


class ProjectSwap(BaseModel):
    project: int = Field(description="1-based number under PROJECTS to replace")
    entry: P.ProjectEntry = Field(description="The better CANDIDATE repo, written like the others")


class ProjectsOut(BaseModel):
    projects: list[T.ProjectFix] = Field(default_factory=list, max_length=4)
    swaps: list[ProjectSwap] = Field(default_factory=list, max_length=2)


class Finding(BaseModel):
    agent: Literal["experience", "projects", "skills", "summary"]
    issue: str = Field(max_length=400, description="What is wrong and what to do, citing the text")


class Review(BaseModel):
    findings: list[Finding] = Field(default_factory=list, max_length=8)


class Task(BaseModel):
    agent: Literal["experience", "projects", "skills", "summary"]
    instruction: str = Field(max_length=500, description="What that agent must change, citing the items")


class RoutePlan(BaseModel):
    tasks: list[Task] = Field(default_factory=list, max_length=12)


ROLE_PROMPTS = {
    "experience": "You are the EXPERIENCE agent: write ONLY the Experience bullets (fields role_N), following the rules "
                  "below for bullets. Other agents write the Summary, Technical Skills and Projects.",
    "skills": "You are the SKILLS agent: write ONLY the Technical Skills lines (`skills`), following the rules below "
              "for skills. Size budget: 5-7 categories, each about ONE line on the page (roughly 100-120 characters "
              "of skills); the whole section 7-9 lines. A short line gets relevant skills added, preferring the "
              "candidate's own TEMPLATE SKILLS; a long one gets trimmed. Never list soft skills or words for "
              "applicants.",
    "summary": "You are the SUMMARY agent: write ONLY the Summary, following the rules below for the summary. Base it "
               "on the CURRENT RESUME's bullets, skills and projects so it never claims what they don't show.",
}

PROJECTS_EDIT_PROMPT = """You are the PROJECTS agent of a resume team. Do what TASKS ask for the Projects section:
- rewrite a paragraph in place (`projects`: 1-based number under PROJECTS and the new paragraph), or
- replace a weaker project with a better CANDIDATE repo (`swaps`: the number to replace and a full entry for the
  candidate: repo full_name exactly as listed, title, stack, paragraph, short).
Each paragraph: exactly 3 sentences, 300-420 characters: the problem, the system built (stack named), the wow factor
(a result or scale). The title is a real project name, never the bare repo slug. Use the posting's keywords the TASKS
ask for. No dashes as punctuation; "by N%". Never invent degrees, employers or dates."""

REVIEW_PROMPT = """You are the VALIDATOR of a resume team. Check the CURRENT RESUME for this posting the way a hiring
manager would and report only real problems, each for the agent that owns that section:
- projects: a CANDIDATE (especially one marked TEMPLATE, the candidate's own showcase) fits the posting clearly
  better than a project on the page; a description that doesn't match its repo summary; a title that is a bare repo
  slug; a description under 3 sentences;
- experience: bullets that don't fit their employer, repeat each other, or miss the posting's main duties;
- skills: missing posting tools the bullets use; a category with nothing useful in it; soft skills listed;
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
    hard: list
    history: Annotated[list, operator.add]


def _context(ctx: dict, f: T.Filled | None, project_state: str | None) -> str:
    """What every agent sees about the current resume."""
    r = ctx["r"]
    parts = [T.facts_block(), "TEMPLATE SKILLS (the candidate's own): " +
             "; ".join(f"{c}: {T.to_plain(v)}" for c, v in r.skills)]
    if f is not None:
        lines = []
        for e in f.roles:
            role = r.roles[e.role]
            lines.append(f"role_{e.role}: {role.title} at {role.company} ({role.dates})")
            lines += [f"  {k}. {b.text}" for k, b in enumerate(e.bullets, 1)]
        parts.append("CURRENT RESUME\nSummary: " + f.summary + "\nExperience:\n" + "\n".join(lines) +
                     "\nTechnical Skills:\n" + "\n".join(f"- {s.category}: {', '.join(s.render())}" for s in f.skills))
    if project_state:
        parts.append("PROJECTS:\n" + "\n".join(f"{i}. {p}" for i, p in enumerate(A.project_paragraphs(project_state), 1)))
    if ctx.get("candidates"):
        known = set(P.known_entries(r.tex))
        parts.append("CANDIDATE REPOS (for the Projects section):\n" + "\n".join(
            f"- {c['full_name']}{' (TEMPLATE)' if c['full_name'].lower() in known else ''} [relevance {sc:.2f}]: "
            f"{c['title']}; {c['summary'][:220]}; stack {', '.join(c['tech_stack'][:6])}"
            for sc, c in ctx["candidates"]))
    return "\n\n".join(parts)


def _section_messages(ctx: dict, agent: str, f: T.Filled | None, tasks: list[str], last: str) -> list:
    """The step 9 prompt (all rules and the posting) with the agent's role, the current resume and its tasks."""
    base = T.build_fill_prompt(ctx["r"], ctx["jd"], ctx["company"], 14)
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


def _run_agent(ctx: dict, agent: str, f: T.Filled | None, tasks: list[str], last: str) -> tuple[str, object]:
    """One section agent's call; returns (agent, output) or (agent, None) on failure."""
    model = llm.writer(16000 if agent == "experience" else 8000, timeout=600, model=ctx["models"][agent])
    log, t0 = ctx["log"], time.monotonic()
    try:
        if agent == "projects":
            proj = ctx["job_dir"] / "projects.json"
            msgs = [SystemMessage(with_today(PROJECTS_EDIT_PROMPT)),
                    HumanMessage(_context(ctx, f, proj.read_text()) + "\n\nTASKS:\n" + "\n".join(f"- {t}" for t in tasks)
                                 + (f"\n\nLAST ROUND: {last}" if last else ""))]
            out = llm.structured_invoke(model, ProjectsOut, msgs, log=log)
        elif agent == "experience" and f is not None and not any(FULL_REWRITE.search(t) for t in tasks):
            # targeted edit: only the bullets the validators named (a full rewrite moves every orphan word)
            msgs = T.build_repair_prompt(ctx["r"], ctx["jd"], f, tasks + ([f"LAST ROUND: {last}"] if last else []))
            out = llm.structured_invoke(model, T.Repair, msgs, log=log)
        else:
            schema = {"experience": experience_schema(ctx["r"]), "skills": SkillsOut, "summary": SummaryOut}[agent]
            out = llm.structured_invoke(model, schema, _section_messages(ctx, agent, f, tasks, last), log=log)
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
    if outs.get("skills") is not None:
        g.skills = outs["skills"].skills
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

    def projects():
        token = (open_vault().get("github") or {}).get("token")
        chosen = P.choose(r, jd, llm.writer(8000, timeout=300, model=ctx["models"]["projects"]),
                          github.profile_user(r.tex), token, rediscover=False, log=log)
        P.save_state(ctx["job_dir"], list(chosen.entries), chosen)
        ctx["candidates"] = chosen.candidates
        ctx["cards"] = {c["full_name"].lower(): c for _, c in chosen.candidates}
        return chosen

    with ThreadPoolExecutor(2) as ex:
        fut_p = ex.submit(projects)
        _, exp = _run_agent(ctx, "experience", None, [], "")
        fut_p.result()
    if exp is None:
        raise RuntimeError("the experience agent failed on the first draft")
    roles = [T.FillRole(role=i, bullets=getattr(exp, f"role_{i}")) for i in range(len(r.roles))]
    f = T.Filled(summary=T.to_plain(r.summary), roles=roles,
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
    fitted = P.fit(tex, job_dir, lambda t: T.compile_pdf(t, job_dir))
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
    return plan_space(state["ctx"], state["f"])


def check_layout(state: State) -> dict:
    """Validator: checks the resume against the known failure list (one page, bullet rules, JD coverage, page filled,
    template bullets and skills used where they fit, enough full projects, no empty skills line), then the rendered
    page (tools/layout.py), then a content review; every finding goes to the agent whose section it is about."""
    ctx = state["ctx"]
    r, jd, job_dir, log = ctx["r"], ctx["jd"], ctx["job_dir"], ctx["log"]
    f, tex, problems, pages, err = render(state)
    tasks: dict[str, list[str]] = {a: [] for a in AGENTS}
    if err:
        tasks["experience"].append(f"The LaTeX failed to compile ({err}); write plain text only.")
    if pages and pages > 1:
        tasks["experience"].append(f"The resume is {pages} pages; it must be one: shorten every bullet to one line "
                                   "(<= 110 characters) and drop the least relevant bullet of the longest role.")
        tasks["skills"].append("The resume overflows one page: at most 2 tools per term, merge near-duplicates.")
    tasks["experience"] += T.duplicates(r, f) + T.quality(r, jd, f)
    uncovered = T.fill_checks(r, jd, f)[0]
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
    if lay:
        for issue in lay.issues:
            agent = ("skills" if issue.startswith("Technical Skills") else
                     "projects" if issue.startswith("the project") else "experience")
            tasks[agent].append(issue)
        used = " ".join(x for sl in f.skills for x in sl.render()).lower()
        pool = [t.strip() for _, items in r.skills for t in T.to_plain(items).split(",")
                if t.strip() and t.strip().lower() not in used][:15]
        if lay.short_skills:
            room = {k: f"about {round((1 - v) * 115)} more characters" for k, v in lay.skill_fill.items() if v < 0.85}
            tasks["skills"].append(f"Each category's last line should be full. Room left: {room}. Fill it with relevant "
                                   f"skills{' (prefer TEMPLATE SKILLS not yet listed: ' + str(pool) + ')' if pool else ''}"
                                   "; never wrap a category onto a new line for one or two words.")
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
    if pages == 1 and not err and not state.get("reviewed"):
        # once per cycle, on the first one-page draft: the content review (a better project skipped, a description
        # that doesn't match its repo...) and the vision check for what code can't see
        for fd in content_review(ctx, f):
            tasks[fd.agent].append(fd.issue)
        tasks["experience"] += [v for v in layout.review(job_dir / "resume.pdf", log=log)
                                if v.split(" ", 1)[0] in {"odd_characters", "overlap", "overflow", "alignment"}]
        reviewed = True
    else:
        reviewed = state.get("reviewed", False)
    tasks = {a: t for a, t in tasks.items() if t}
    n = sum(map(len, tasks.values()))
    log(f"validator pass {state.get('layout_pass', 0)}: {pages} page(s), {n} finding(s) "
        f"{ {a: len(t) for a, t in tasks.items()} }" +
        "".join(f"\n    [{a}] {t[:200]}" for a, ts in tasks.items() for t in ts))
    return {"f": f, "tasks": tasks, "layout_issues": [x for t in tasks.values() for x in t], "hard": hard_issues(ctx, f),
            "layout_pass": state.get("layout_pass", 0) + 1, "history": [f"validator {n}"],
            "reviewed": reviewed}


SUMMARY_MAX = 450


def summary_issues(summary: str, overflow: bool) -> list[str]:
    """The Summary's own rules: short (it shares the page), 2-3 sentences, no claims beyond the candidate's declared
    facts (years above the declared total, internships), no copied qualification list."""
    import re
    out = []
    sentences = [x for x in re.split(r"(?<=[.!?])\s+", summary.strip()) if x]
    if len(summary) > SUMMARY_MAX or len(sentences) > 3:
        out.append(f"The Summary is {len(summary)} characters / {len(sentences)} sentences: rewrite it in 2-3 "
                   f"sentences, at most {SUMMARY_MAX - 50} characters.")
    elif overflow and len(summary) > 330:
        out.append("The page overflows: shorten the Summary to at most 300 characters.")
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
    out = [i for i in lay.issues if i.startswith("the resume is") or i.startswith("the project")]
    if lay.pages == 1 and lay.free_pt is not None and lay.free_pt > BLANK_HARD_PT:
        out.append(f"{round(lay.free_pt / 11)} blank lines at the bottom")
    out += [f"empty skills line {sl.category}" for sl in f.skills if not sl.render()]
    return out


def content_review(ctx: dict, f: T.Filled) -> list:
    """The validator agent's read of the whole resume against the posting (judgement code can't make)."""
    proj = ctx["job_dir"] / "projects.json"
    job = {"title": ctx["jd"].title, "responsibilities": ctx["jd"].responsibilities,
           "basic": ctx["jd"].basic_qualifications, "preferred": ctx["jd"].preferred_qualifications,
           "skills": ctx["jd"].technical_skills}
    msgs = [SystemMessage(REVIEW_PROMPT),
            HumanMessage(f"POSTING: {json.dumps(job)}\n\n" + _context(ctx, f, proj.read_text() if proj.exists() else None))]
    try:
        out = llm.structured_invoke(llm.writer(4000, timeout=300, model=ctx["models"]["router"]), Review, msgs,
                                    log=ctx["log"])
        ctx["log"](f"  content review: {len(out.findings)} finding(s)")
        return out.findings
    except Exception as e:
        ctx["log"](f"  content review failed ({type(e).__name__})")
        return []


def after_layout(state: State) -> str:
    """Keep fixing while the validator has findings (up to LAYOUT_PASSES); then score."""
    if state["tasks"] and state["layout_pass"] <= LAYOUT_PASSES:
        return "fix"
    return "score"


def edit_sections(state: State) -> dict:
    """The agents with tasks edit their sections in parallel (projects.json is backed up for a rollback)."""
    ctx, f, tasks = state["ctx"], state["f"], state["tasks"]
    ctx["log"]("editing: " + ", ".join(f"{a} ({len(t)})" for a, t in tasks.items()))
    with ThreadPoolExecutor(len(tasks) or 1) as ex:
        outs = dict(ex.map(lambda a: _run_agent(ctx, a, f, tasks[a], state.get("last", "")), list(tasks)))
    return {"f": _merge(ctx, f, outs), "tasks": {}}


def score(state: State) -> dict:
    """ATS validator: score the current page (one page only); keep it if better than the best so far, else roll back."""
    ctx = state["ctx"]
    job_dir, jd, log = ctx["job_dir"], ctx["jd"], ctx["log"]
    f, tex, problems, pages, err = render(state)
    rnd = state.get("ats_round", 0)
    best = state.get("best")
    if pages != 1 or err:
        log(f"ats round {rnd}: page not valid ({pages} pages {err}); rolled back to the best version")
        return _rollback(state, "UNDONE: the edit broke the one-page layout; keep lines short") if best else \
            {"ats_round": rnd + 1, "last": "the page overflowed: shorten bullets"}
    ev = A.evaluate(job_dir, jd, ctx["jd_text"], rnd, ctx["sites"], log)
    missing_sites = [x for x in ctx["sites"] if x not in ev["scores"]]
    if missing_sites:  # a checker failed: once more, else the round doesn't count (no average over fewer sites)
        again = A.evaluate(job_dir, jd, ctx["jd_text"], rnd, tuple(missing_sites), log)
        ev["scores"].update(again["scores"]); ev["missing"].update(again["missing"]); ev["advice"] += again["advice"]
        missing_sites = [x for x in ctx["sites"] if x not in ev["scores"]]
    if missing_sites:
        log(f"ats round {rnd}: {missing_sites} failed twice; this round doesn't count")
        return _rollback(state, "a checker failed; the same version will be rescored") if best else \
            {"ats_round": rnd + 1, "last": ""}
    avg = A.average(ev["scores"])
    n_layout = len(layout.check(job_dir / "resume.pdf").issues)
    hard = hard_issues(ctx, f)
    log(f"ats round {rnd}: average {avg} {ev['scores']}, layout issues {n_layout}, hard issues {hard}")
    if best:
        better = (len(hard) < len(best["hard"]) or
                  (len(hard) == len(best["hard"]) and
                   (avg > best["avg"] or (avg >= best["avg"] - A.NOISE and n_layout < best["layout"]))))
        if not better:
            return _rollback(state, f"UNDONE: the last edits moved the scores to {ev['scores']} (average {avg}, best "
                                    f"{best['avg']}) with page problems {hard}; try different wording or other items",
                             ev)
    proj = job_dir / "projects.json"
    new_best = {"avg": avg, "scores": ev["scores"], "f": f.model_copy(deep=True), "layout": n_layout, "hard": hard,
                "tex": (job_dir / "resume.tex").read_text(), "pdf": (job_dir / "resume.pdf").read_bytes(),
                "projects": proj.read_text() if proj.exists() else None, "ev": ev}
    last = f"KEPT: the scores are {ev['scores']} (average {avg})" if best else ""
    return {"best": new_best, "scores": ev["scores"], "ats_round": rnd + 1, "last": last,
            "history": [f"ats {avg}"]}


def _rollback(state: State, why: str, ev: dict | None = None) -> dict:
    ctx, best = state["ctx"], state["best"]
    job_dir = ctx["job_dir"]
    (job_dir / "resume.tex").write_text(best["tex"])
    (job_dir / "resume.pdf").write_bytes(best["pdf"])
    if best["projects"] is not None:
        (job_dir / "projects.json").write_text(best["projects"])
    out = {"f": best["f"].model_copy(deep=True), "ats_round": state.get("ats_round", 0) + 1, "last": why,
           "history": ["rollback"]}
    if ev:  # the rejected version's findings still tell the agents what's missing
        out["best"] = {**best, "ev": {**best["ev"], "missing": ev["missing"], "advice": ev["advice"]}}
    return out


def after_score(state: State) -> str:
    best = state.get("best")
    if not state["ctx"]["sites"] and best:  # dry run (no ATS sites): the drafted, layout-checked page is the result
        return "done"
    if best and best["avg"] >= TARGET and not best["hard"] or state.get("ats_round", 0) > ATS_ROUNDS:
        return "done"
    return "route"


def route(state: State) -> dict:
    """ATS validator's router: missing keywords and advice -> tasks per agent."""
    ctx, best = state["ctx"], state.get("best")
    if not best:  # nothing scored yet: the page never fit
        return {"tasks": {"experience": ["The resume overflows one page: every bullet to one line (<= 110 characters), "
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
        agent = "projects" if "project" in h or "blank" in h else "skills" if "skills" in h else "experience"
        tasks.setdefault(agent, []).append(f"Page problem to fix: {h}")
    ctx["log"]("routed: " + ", ".join(f"{a} {len(t)}" for a, t in tasks.items()))
    return {"tasks": tasks, "layout_pass": 0, "reviewed": False}


def build_graph():
    g = StateGraph(State)
    g.add_node("write", write_initial)
    g.add_node("check_layout", check_layout)
    g.add_node("fix", edit_sections)
    g.add_node("score", score)
    g.add_node("route", route)
    g.add_node("edit", edit_sections)
    g.add_edge(START, "write")
    g.add_edge("write", "check_layout")
    g.add_conditional_edges("check_layout", after_layout, {"fix": "fix", "score": "score"})
    g.add_edge("fix", "check_layout")
    g.add_conditional_edges("score", after_score, {"route": "route", "done": END})
    g.add_edge("route", "edit")
    g.add_edge("edit", "check_layout")
    return g.compile()


def run_job(job_dir: Path, slug: str, models: dict, sites=SITES, log=print) -> dict:  # noqa: C901
    """One job through the agent graph; leaves the best one-page version in job_dir."""
    s = get_settings()
    jd = JobDescription.model_validate_json((job_dir / "jd.json").read_text())
    ctx = {"job_dir": job_dir, "jd": jd, "company": (s.state_dir / "companies" / f"{slug}.md").read_text(),
           "r": T.parse((s.data_dir / "templates" / "resume.tex").read_text()),
           "jd_text": (job_dir / "jd.txt").read_text() if (job_dir / "jd.txt").exists() else "",
           "models": models, "log": log, "sites": sites}
    final = build_graph().invoke({"ctx": ctx, "history": []}, {"recursion_limit": 200})
    best = final.get("best")
    if best:
        (job_dir / "resume.tex").write_text(best["tex"])
        (job_dir / "resume.pdf").write_bytes(best["pdf"])
        (job_dir / "filled.json").write_text(best["f"].model_dump_json(indent=1))
    lay = layout.check(job_dir / "resume.pdf").issues if best else []
    hard = best.get("hard", []) if best else []
    hist = [h for h in final.get("history", []) if h.startswith("ats ")]
    return {"average": best["avg"] if best else None, "scores": best["scores"] if best else {},
            "history": [float(h.split()[1]) for h in hist], "reached": bool(best and best["avg"] >= TARGET),
            "pages": layout.page_count(job_dir / "resume.pdf") if best else None, "layout_issues": lay,
            "hard_issues": hard,
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
        results = list(ex.map(one, jobs))
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
