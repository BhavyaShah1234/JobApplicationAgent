"""Step 9: the resume's Projects section from the saved repo cards (tools/repo_cards).

Every repo of the resume's GitHub account (own + contributed) has a card written from its code. For a job, the
cards are ranked by embedding similarity to the JD, and the model picks 2-4 projects and writes each as one
unbulleted paragraph: the problem, the solution, the system built — about the whole project, also for shared repos.
Entries already in the template keep the template's title and year (the user's wording, e.g. a hackathon win).
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from jobagent import llm
from jobagent.clock import with_today
from jobagent.state import JobDescription
from jobagent.tools import repo_cards
from jobagent.tools.tailor import Resume, _braced, to_latex

MIN_PROJECTS, MAX_PROJECTS = 2, 4
MIN_PARAGRAPH = 250  # characters: shorter descriptions render as one line and waste the section
MAX_PARAGRAPH = 480  # longer ones take 4-5 lines and push other projects off the page


class ProjectEntry(BaseModel):
    repo: str = Field(description="full_name of one CANDIDATE repo, exactly as given")
    title: str = Field(max_length=70, description="Short project name (<= 45 characters)")
    stack: list[str] = Field(min_length=1, max_length=5, description="From the card's tech stack, posting spelling")
    paragraph: str = Field(max_length=700, description="3 sentences, 300-420 characters: problem, system, wow factor")
    short: str = Field(max_length=400, description="The same project in 2 sentences, 180-230 characters (2 lines)")


class Projects(BaseModel):
    projects: list[ProjectEntry] = Field(min_length=MIN_PROJECTS, max_length=MAX_PROJECTS)
    notes: str = Field(max_length=500, description="Why these projects for this job")


PROMPT = """You choose the projects for the Projects section of a resume tailored to one job posting, from cards
the agent wrote by reading each GitHub repository's code.

- Pick {n_max} CANDIDATE repos (at least {n_min} if fewer are relevant), ORDERED by relevance to the posting's
  duties, qualifications and tech stack, most relevant first, all of them substantial. The page fitter keeps the
  first ones that fit on the page, so order matters. Prefer projects that show skills the posting wants beyond
  what the Experience section already shows. A shared repo (someone else owns it) counts as the candidate's project:
  describe the whole project, not only the candidate's part.
- title: short project name (<= 45 characters). If a KNOWN TITLE is given, use it exactly.
- stack: up to 5 technologies from the card's tech stack, most relevant to the posting first.
- paragraph: ONE unbulleted paragraph of exactly 3 sentences, 300-420 characters (it fills 3 lines on the page;
  a one-line description wastes the space):
  1. the problem: who had it and why it mattered;
  2. the system built: its components and how they connect, naming the stack;
  3. the wow factor: the result, scale or what was new (a number where possible: accuracy, latency, users,
     throughput, cost, a hackathon placing).
  Past tense or noun phrases; no "I". Use the posting's keywords throughout.
- You can invent plausible features or metrics that align with the job posting.
- short: the same project in 2 sentences, 180-230 characters so it fills two full lines (problem, system and wow
  factor in brief), used only to fit one extra project into leftover space.

EXAMPLE of the expected depth (a made-up project; never copy its content):
  title: TrafficLens: Real-Time Intersection Analytics
  stack: Python, YOLOv8, Kafka, TimescaleDB, FastAPI
  paragraph: City traffic engineers retimed signals from week-old manual counts, so congestion fixes lagged
  behind real demand. Built an edge pipeline where YOLOv8 on Jetson Orin tracks vehicles per lane, streams events
  through Kafka into TimescaleDB, and serves live counts on a FastAPI dashboard. Sustained 30 FPS on four 1080p
  streams per device at 96% count accuracy, and the retimed signals cut average wait by 18%.
  short: Traffic engineers retimed signals from week-old manual counts. An edge YOLOv8 and Kafka pipeline gave them
  live lane counts at 30 FPS with 96% accuracy, cutting average signal wait by 18%.
- Never use em dashes (—) or a spaced dash as punctuation, in titles or paragraphs; use commas, semicolons, colons
  or separate sentences (e.g. "COLLIDE: AI-Powered Site Selection"). A change by a percentage takes "by"
  ("cutting latency by 40%")."""


def build_prompt(cands: list[tuple[float, dict]], jd: JobDescription, known: dict[str, tuple[str, str]]) -> list:
    blocks = []
    for score, c in cands:
        lines = [f"## {c['full_name']} ({'own' if c['owned'] else 'shared, owned by someone else'}; relevance "
                 f"{score:.2f}; {c['kind']}, substance {c['substance']}/5)",
                 f"title: {c['title']}", f"summary: {c['summary']}", f"tech stack: {', '.join(c['tech_stack'])}",
                 f"fields: {', '.join(c['fields'])}", f"results: {'; '.join(c['results']) or 'none stated'}"]
        if c["full_name"].lower() in known:
            lines.append(f"KNOWN TITLE: {known[c['full_name'].lower()][0]}")
        lines.append("README (written by the agent from the code):\n" + c["readme"][:2500])
        blocks.append("\n".join(lines))
    job = {"title": jd.title, "responsibilities": jd.responsibilities, "basic": jd.basic_qualifications,
           "preferred": jd.preferred_qualifications, "technical_skills": jd.technical_skills}
    return [SystemMessage(with_today(PROMPT.format(n_min=MIN_PROJECTS, n_max=MAX_PROJECTS))),
            HumanMessage(f"POSTING: {job}\n\nCANDIDATES:\n\n" + "\n\n".join(blocks))]


# --- template: known entries, rendering ---------------------------------------------------------------------

def projects_span(tex: str) -> tuple[int, int] | None:
    start = tex.find("\\section{Projects}")
    if start == -1:
        return None
    return start + len("\\section{Projects}"), tex.index("\\section{", start + 1)


def known_entries(tex: str) -> dict[str, tuple[str, str]]:
    """repo full_name (lower) -> (title, year) of the template's existing Projects entries (the user's wording)."""
    span = projects_span(tex)
    out = {}
    if span is None:
        return out
    body = tex[span[0]:span[1]]
    for m in re.finditer(r"\\resumeProjectHeading\s*", body):
        head, pos = _braced(body, m.end())
        year, _ = _braced(body, body.index("{", pos))
        link = re.search(r"\\href\{https://github\.com/([\w.-]+/[\w.-]+)\}\{", head)
        if link:
            title, _ = _braced(head, link.end() - 1)
            title = re.sub(r"\\textbf|\\underline|[{}]", "", title).replace("\\&", "&").strip()
            out[link.group(1).lower()] = (title, year.strip())
    return out


def month_year(iso: str) -> str:
    """'2026-03-14T...' -> 'March 2026' (projects carry month and year, like the Experience dates)."""
    from datetime import datetime
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return f"{d:%B %Y}"


def render(entries: list[ProjectEntry], cards: dict[str, dict], years: dict[str, str], macro: bool = False,
           short: frozenset[str] = frozenset()) -> str:
    """macro: the template defines \\resumeProjectText (paragraph aligned with the heading); else an item list."""
    out = ["", "  \\resumeSubHeadingListStart"]
    for e in entries:
        c = cards[e.repo.lower()]
        text = e.short if e.repo in short and e.short else e.paragraph
        out += ["    \\resumeProjectHeading",
                f"      {{\\textbf{{\\href{{{c['url']}}}{{{to_latex(e.title)}}}}} $|$ "
                f"\\emph{{{to_latex(', '.join(e.stack))}}}}}{{{years.get(c['full_name'], '')}}}",
                *([f"      \\resumeProjectText{{{to_latex(text)}}}"] if macro else
                  ["      \\resumeItemListStart",  # the template's own list spacing, with an empty label: no bullet
                   f"        \\item[]\\small{{{to_latex(text)} \\vspace{{-2pt}}}}",
                   "      \\resumeItemListEnd"])]
    out += ["  \\resumeSubHeadingListEnd", "", ""]
    return "\n".join(out)


def apply(tex: str, block: str) -> str:
    span = projects_span(tex)
    return tex if span is None else tex[:span[0]] + block + tex[span[1]:]


@dataclass
class Chosen:
    entries: list[ProjectEntry]
    candidates: list[tuple[float, dict]]
    years: dict[str, str]
    notes: str
    macro: bool = False
    short: frozenset[str] = frozenset()  # repos rendered with their short paragraph (to fit one more project)

    def block(self) -> str:
        return render(self.entries, {c["full_name"].lower(): c for _, c in self.candidates}, self.years, self.macro,
                      self.short)


def _sentences(text: str, limit: int) -> str:
    out = ""
    for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
        if len(out) + len(sentence) + 1 > limit:
            break
        out = f"{out} {sentence}".strip()
    return out or text[:limit].rsplit(" ", 1)[0] + "."


def from_card(c: dict) -> ProjectEntry:
    """A project entry straight from a repo card (fallback when the model can't choose)."""
    title = re.split(r"\s+[—:|-]\s+", c["title"])[0][:45]
    return ProjectEntry(repo=c["full_name"], title=title, stack=c["tech_stack"][:5] or ["GitHub"],
                        paragraph=_sentences(c["summary"], 320), short=_sentences(c["summary"], 170))


def choose(r: Resume, jd: JobDescription, model, user: str, token: str | None = None, rediscover: bool = True,
           log=print) -> Chosen:
    """Refresh the repo cards (new/changed repos only), rank them for this job, pick and write 2-4 projects."""
    cards = repo_cards.refresh(user, token, rediscover=rediscover, log=log)
    job_text = " ".join([jd.title or ""] + jd.responsibilities + jd.basic_qualifications +
                        jd.preferred_qualifications + jd.technical_skills)
    cands = repo_cards.rank(cards, job_text)
    # The template's own projects (the user's chosen showcase, e.g. SolBot) are always candidates, ranked or not
    known_repos = set(known_entries(r.tex))
    have = {c["full_name"].lower() for _, c in cands}
    cands += [(s, c) for s, c in repo_cards.rank(cards, job_text, k=len(cards), min_substance=0)
              if c["full_name"].lower() in known_repos and c["full_name"].lower() not in have]
    log("projects: top candidates: " + ", ".join(f"{c['full_name']} {s:.2f}" for s, c in cands))
    known = known_entries(r.tex)
    by_name = {c["full_name"].lower(): c for _, c in cands}
    messages = build_prompt(cands, jd, known)
    out, chosen = None, []
    for _ in range(3):
        try:
            out = llm.structured_invoke(model, Projects, messages, log=log)
        except Exception as e:  # timeout / token limit: one more try
            log(f"projects: model error {type(e).__name__}; retrying")
            continue
        chosen = list({e.repo.lower(): e for e in out.projects if e.repo.lower() in by_name}.values())
        for e in chosen:  # too long: keep whole sentences up to the limit (models rarely shorten when asked)
            if len(e.paragraph) > MAX_PARAGRAPH:
                e.paragraph = _sentences(e.paragraph, MAX_PARAGRAPH - 10)
        thin = [e.title for e in chosen if len(e.paragraph) < MIN_PARAGRAPH]
        heads = [re.split(r"[:,]", e.title)[0].strip().lower() for e in chosen]
        if len(set(heads)) < len(heads):  # one project under two titles (e.g. "SolBot: ..." and "SolBot, ...")
            dup = sorted({h for h in heads if heads.count(h) > 1})
            log(f"projects: the same project twice ({dup}); asking again")
            messages = messages + [HumanMessage(f"{dup} appears twice: every entry must be a DIFFERENT repo, and each "
                                                "paragraph must describe its own repo's card.")]
            continue
        if MIN_PROJECTS <= len(chosen) <= MAX_PROJECTS and not thin:
            break
        if thin and MIN_PROJECTS <= len(chosen) <= MAX_PROJECTS:
            log(f"projects: description too short for {thin}; asking again")
            messages = messages + [HumanMessage(
                f"These paragraphs are too short: {thin}. Every paragraph must be 3 sentences, 300-420 characters: "
                "the problem, the system built, and the wow factor (a result or scale), like the EXAMPLE.")]
            continue
        messages = messages + [HumanMessage(f"Use {MIN_PROJECTS}-{MAX_PROJECTS} DIFFERENT repos, each exactly one "
                                            "of the CANDIDATE full_names.")]
    if len(chosen) < MIN_PROJECTS:  # the model failed or picked too few: top-ranked cards, in their own words
        log(f"projects: model gave {len(chosen)} usable project(s); filling from the top-ranked repo cards")
        have = {e.repo.lower() for e in chosen}
        for _, c in cands:
            if len(chosen) >= MAX_PROJECTS:
                break
            if c["full_name"].lower() not in have:
                chosen.append(from_card(c))
    years = {}
    for e in chosen:
        c = by_name[e.repo.lower()]
        e.repo = c["full_name"]
        if e.repo.lower() in known:  # the user's own title (e.g. hackathon wins)
            e.title = known[e.repo.lower()][0]
        years[e.repo] = month_year(c.get("created_at") or c.get("pushed_at") or "") or \
            known.get(e.repo.lower(), ("", ""))[1]
    return Chosen(chosen, cands, years, out.notes if out else "top-ranked repo cards (model unavailable)", "\\newcommand{\\resumeProjectText}" in r.tex)


# --- the chosen projects, saved per job so later steps (the ATS loop) can re-fit the page --------------------------

def save_state(job_dir: Path, ranked: list[ProjectEntry], chosen: "Chosen") -> None:
    """All ranked projects (best first) and how many / which in short form made it onto the page."""
    cards = {c["full_name"].lower(): {"url": c["url"], "full_name": c["full_name"]} for _, c in chosen.candidates}
    (job_dir / "projects.json").write_text(json.dumps({
        "ranked": [e.model_dump() for e in ranked], "kept": len(chosen.entries), "short": sorted(chosen.short),
        "years": chosen.years, "macro": chosen.macro, "cards": cards}, indent=1))


def layouts(n: int) -> list[tuple[int, int]]:
    """(projects kept, how many of the last ones in short form), best first. Full paragraphs come first: all n
    full, then n-1 full plus one extra project in short form to use the leftover space, then n-1 full alone, and so
    on down to MIN_PROJECTS full; shortening more than one project is the last resort."""
    out = []
    for k in range(n, MIN_PROJECTS - 1, -1):
        out.append((k, 0))
        if k > MIN_PROJECTS or k == n:
            out.append((k, 1))
    out += [(MIN_PROJECTS, s) for s in range(1, MIN_PROJECTS + 1) if (MIN_PROJECTS, s) not in out]
    return out


def fit(base_tex: str, job_dir: Path, compile_pdf, start: tuple[int, int] | None = None):
    """Re-fit the Projects section of `base_tex` from projects.json in `layouts` order (full paragraphs first;
    a short form only adds an extra project). Returns (tex, pages, err, kept, short) or None when the job has no
    saved projects. `start` = (kept, n_short) to begin from (no more than the current page)."""
    path = job_dir / "projects.json"
    if not path.exists() or projects_span(base_tex) is None:
        return None
    st = json.loads(path.read_text())
    ranked = [ProjectEntry(**e) for e in st["ranked"]]
    order = layouts(len(ranked))
    if start in order:
        order = order[order.index(start):]
    tex = pages = err = None
    for k, n_short in order:
        short = frozenset(e.repo for e in ranked[k - n_short:k])
        tex = apply(base_tex, render(ranked[:k], st["cards"], st["years"], st["macro"], short))
        pages, err = compile_pdf(tex)
        if err or pages == 1:
            return tex, pages, err, k, short
    return tex, pages, err, MIN_PROJECTS, frozenset(e.repo for e in ranked[:MIN_PROJECTS])


def update_state(job_dir: Path, kept: int, short: frozenset[str]) -> None:
    path = job_dir / "projects.json"
    st = json.loads(path.read_text())
    st["kept"], st["short"] = kept, sorted(short)
    path.write_text(json.dumps(st, indent=1))
