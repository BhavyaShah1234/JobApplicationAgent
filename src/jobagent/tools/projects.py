"""Step 9: the resume's Projects section from the saved repo cards (tools/repo_cards).

Every repo of the resume's GitHub account (own + contributed) has a card written from its code. For a job, the
cards are ranked by embedding similarity to the JD, and the model picks 2-4 projects and writes each as one
unbulleted paragraph: the problem, the solution, the system built — about the whole project, also for shared repos.
Entries already in the template keep the template's title and year (the user's wording, e.g. a hackathon win).
"""

import re
from dataclasses import dataclass

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from jobagent.clock import with_today
from jobagent.state import JobDescription
from jobagent.tools import repo_cards
from jobagent.tools.tailor import Resume, _braced, to_latex

MIN_PROJECTS, MAX_PROJECTS = 2, 4


class ProjectEntry(BaseModel):
    repo: str = Field(description="full_name of one CANDIDATE repo, exactly as given")
    title: str = Field(max_length=70, description="Short project name (<= 45 characters)")
    stack: list[str] = Field(min_length=1, max_length=5, description="From the card's tech stack, posting spelling")
    paragraph: str = Field(max_length=600, description="2-3 sentences, <= 320 characters, plain text")
    short: str = Field(max_length=400, description="The same project in 1-2 sentences, <= 170 characters")


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
- paragraph: ONE unbulleted paragraph, 2-3 sentences, <= 320 characters: the problem the project addresses, the
  solution, and the system that was built (components, how they connect). Past tense or noun phrases; no "I".
  Use the posting's keywords where the card supports them. Numbers/results only from the card's results; never
  invent features or metrics.
- short: the same project in 1-2 sentences, <= 170 characters (problem and system in brief), used when the page
  has room for the project but not for its full paragraph. Same facts, no new ones.
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


def choose(r: Resume, jd: JobDescription, model, user: str, token: str | None = None, rediscover: bool = True,
           log=print) -> Chosen:
    """Refresh the repo cards (new/changed repos only), rank them for this job, pick and write 2-4 projects."""
    cards = repo_cards.refresh(user, token, rediscover=rediscover, log=log)
    job_text = " ".join([jd.title or ""] + jd.responsibilities + jd.basic_qualifications +
                        jd.preferred_qualifications + jd.technical_skills)
    cands = repo_cards.rank(cards, job_text)
    log("projects: top candidates: " + ", ".join(f"{c['full_name']} {s:.2f}" for s, c in cands))
    known = known_entries(r.tex)
    by_name = {c["full_name"].lower(): c for _, c in cands}
    messages = build_prompt(cands, jd, known)
    out, chosen = None, []
    for _ in range(3):
        try:
            out = model.with_structured_output(Projects, method="json_schema").invoke(messages)
        except Exception as e:  # timeout / token limit: one more try
            log(f"projects: model error {type(e).__name__}; retrying")
            continue
        chosen = list({e.repo.lower(): e for e in out.projects if e.repo.lower() in by_name}.values())
        if MIN_PROJECTS <= len(chosen) <= MAX_PROJECTS:
            break
        messages = messages + [HumanMessage(f"Use {MIN_PROJECTS}-{MAX_PROJECTS} DIFFERENT repos, each exactly one "
                                            "of the CANDIDATE full_names.")]
    if out is None:
        raise RuntimeError("could not choose projects (the model failed 3 times)")
    years = {}
    for e in chosen:
        c = by_name[e.repo.lower()]
        e.repo = c["full_name"]
        if e.repo.lower() in known:  # the user's own title (e.g. hackathon wins)
            e.title = known[e.repo.lower()][0]
        years[e.repo] = month_year(c.get("created_at") or c.get("pushed_at") or "") or \
            known.get(e.repo.lower(), ("", ""))[1]
    return Chosen(chosen, cands, years, out.notes, "\\newcommand{\\resumeProjectText}" in r.tex)
