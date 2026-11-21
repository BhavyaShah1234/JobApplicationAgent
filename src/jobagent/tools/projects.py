"""Step 9: the resume's Projects section from the saved repo cards (tools/repo_cards).

Every repo of the resume's GitHub account (own + contributed) has a card written from its code. For a job, the
cards are ranked by embedding similarity to the JD, and the model picks 2-4 projects and writes each as one
unbulleted paragraph: the problem, the solution, the system built — about the whole project, also for shared repos.
Entries already in the template keep the template's title and year (the user's wording, e.g. a hackathon win).

SOURCE-SENTENCE CONTRACT: the displayed description (full AND short) is never written by the model. It is a sequence of COMPLETE
sentences of that same project's own saved card (its summary and results), picked by number, joined with single spaces and nothing else.
The model ranks and selects; it cannot add a verb, clause or feature, and a qualifier in the source ("prototype", "simulated", "README
claims", "not measured", "planned") travels with its sentence. `bound_text` re-derives that property from the job's saved card, so it holds
for every writer, repair and refresh without trusting a flag. A project whose card cannot fill a variant from whole sentences is skipped.
"""

import functools
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
from jobagent.tools.tailor import DASH, Resume, _braced, to_latex

MIN_PROJECTS, MAX_PROJECTS = 2, 4
MIN_PARAGRAPH = 250  # characters: shorter descriptions render as one line and waste the section
MAX_PARAGRAPH = 480  # longer ones take 4-5 lines and push other projects off the page
FULL_LIMIT, FULL_FLOOR = MAX_PARAGRAPH - 10, 200      # the full description, in characters: wanted MIN_PARAGRAPH, accepted from FULL_FLOOR
SHORT_LIMIT, SHORT_FLOOR, SHORT_WANTED = 240, 100, 120   # the short description (two lines, used to fit one extra project)
MAX_SENTENCES = 6    # source sentences in one description


class ProjectEntry(BaseModel):
    """A project as it is stored and rendered. Its texts are built by `entry_from_pick` / `faithful_description` from source sentences."""
    repo: str = Field(description="full_name of one CANDIDATE repo, exactly as given")
    title: str = Field(max_length=70, description="Short project name (<= 45 characters)")
    stack: list[str] = Field(min_length=1, max_length=5, description="From the card's tech stack, posting spelling")
    paragraph: str = Field(max_length=700, description="The full description: whole source sentences of this repo's own card")
    short: str = Field(max_length=400, description="The short description: whole source sentences, or empty when the card cannot fill it")


class ProjectPick(BaseModel):
    """What the writer returns for one project: a selection of that repository's numbered SOURCE SENTENCES, never prose."""
    repo: str = Field(description="full_name of one CANDIDATE repo, exactly as given")
    title: str = Field(max_length=70, description="The project's name as its own title shows it (<= 45 characters)")
    stack: list[str] = Field(min_length=1, max_length=5, description="From the card's tech stack, posting spelling")
    paragraph_sentences: list[int] = Field(min_length=1, max_length=MAX_SENTENCES,
                                           description="Numbers of this repo's SOURCE SENTENCES that make the full description (300-420 characters together, never over 470), in reading order")
    short_sentences: list[int] = Field(default_factory=list, max_length=4,
                                       description="Numbers of SOURCE SENTENCES for the short description (120-240 characters together); empty when none fits")


class Projects(BaseModel):
    projects: list[ProjectPick] = Field(min_length=MIN_PROJECTS, max_length=MAX_PROJECTS + 2)
    notes: str = Field(max_length=500, description="One brief sentence explaining the selection, at most 300 characters")


SOURCE_POLICY = ("Describe each repository's own supplied features and results. Project numerical measurements, "
                 "benchmarks, user counts and capability counts must be present in that repository's supplied "
                 "sources. When no measurement is supplied, use a qualitative outcome instead of inventing a number. "
                 "Work-experience impact estimates do not establish repository measurements or product features. "
                 "Compliance mapping or configured tests establish checks, not guaranteed or achieved compliance. "
                 "Describe what the system maps, checks or validates unless its own source establishes the outcome. "
                 "Preserve documented scope: checks in selected stages do not establish compliance at every stage. "
                 "Never say a project works only, solely or exclusively from or with something unless its own source "
                 "states that restriction; documented fallbacks, alternatives and user-supplied sources widen the scope.")

PROMPT = """You choose the projects for the Projects section of a resume tailored to one job posting, from cards
the agent wrote by reading each GitHub repository's code.

- Pick {n_max} CANDIDATE repos (at least {n_min} if fewer are relevant), ORDERED by relevance to the posting's
  duties, qualifications and tech stack, most relevant first, all of them substantial. The page fitter keeps the
  first ones that fit on the page, so order matters. Prefer projects that show skills the posting wants beyond
  what the Experience section already shows. A shared repo (someone else owns it) counts as the candidate's project:
  describe the whole project, not only the candidate's part.
- title: short project name (<= 45 characters), as that card's own title shows it. If a KNOWN TITLE is given, use it exactly.
- stack: up to 5 technologies from the card's tech stack, most relevant to the posting first. Write "Python", never
  a version ("Python 3.12" -> "Python").
- paragraph_sentences: you do NOT write the description. Each candidate lists its SOURCE SENTENCES, numbered, each a complete
  sentence of that repository's own card with its length. Return the numbers of the sentences that together are the most relevant
  to the posting and total 300-420 characters (usually 2-3; never over 470), in reading order. They are printed exactly as written, so a
  qualifier in a sentence (prototype, simulated, planned, not measured, README claims) stays with it: pick a sentence only when
  you accept it as written. Numbers belong to that candidate only; never mix repositories.
- short_sentences: numbers of one or two other (or the same) SOURCE SENTENCES of the SAME repo that total 120-240 characters, used
  only to fit one extra project into leftover space. Leave it empty when no sentence of that size fits.
  Never write, shorten, join or reword a sentence; there is no field for prose."""
EXAMPLE_BLOCK = """
EXAMPLE of the expected selection (a made-up project; never copy its content):
  TrafficLens SOURCE SENTENCES: [1] (118 chars) City traffic engineers retimed signals from week-old manual counts. [2] (131 chars)
  An edge pipeline runs YOLOv8 on Jetson Orin and streams events through Kafka into TimescaleDB. [3] (96 chars) Sustained 30 FPS on
  four streams in a lab test; the signal-wait effect was not measured.
  title: TrafficLens: Real-Time Intersection Analytics
  stack: Python, YOLOv8, Kafka, TimescaleDB, FastAPI
  paragraph_sentences: [1, 2, 3]   short_sentences: [2]"""
PROMPT += """
- Never use em dashes (—) or a spaced dash as punctuation in a title."""
PROMPT += "\n" + SOURCE_POLICY


def source_lines(card: dict, limit: int = 40) -> str:
    """The card's numbered SOURCE SENTENCES as the writer sees them: `[n] (length chars) sentence`, the same numbering `compose` uses."""
    return "\n".join(f"[{i}] ({len(s)} chars) {s}" for i, s in enumerate(source_sentences(card)[:limit], 1)) or "none usable"


def build_prompt(cands: list[tuple[float, dict]], jd: JobDescription, known: dict[str, tuple[str, str]], *, grounded=False) -> list:
    blocks = []
    for score, c in cands:
        lines = [f"## {c['full_name']} ({'own' if c['owned'] else 'shared, owned by someone else'}; relevance "
                 f"{score:.2f}; {c['kind']}, substance {c['substance']}/5)",
                 f"title: {c['title']}", f"tech stack: {', '.join(c['tech_stack'])}", f"fields: {', '.join(c['fields'])}"]
        if c["full_name"].lower() in known:
            lines.append(f"KNOWN TITLE: {known[c['full_name'].lower()][0]}")
        lines.append("SOURCE SENTENCES (this repo only; pick by number):\n" + source_lines(c))
        blocks.append("\n".join(lines))
    job = {"title": jd.title, "responsibilities": jd.responsibilities, "basic": jd.basic_qualifications,
           "preferred": jd.preferred_qualifications, "technical_skills": jd.technical_skills}
    prompt = PROMPT.format(n_min=MIN_PROJECTS, n_max=MAX_PROJECTS)
    if not grounded:
        # The invented example is kept for the original strategy only: a concrete made-up achievement was copied into a
        # different repository during live validation, so the grounded strategy states the shape without it.
        prompt += "\n" + EXAMPLE_BLOCK
    return [SystemMessage(with_today(prompt)),
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


PY_VERSION = re.compile(r"(?i)^python\s*v?\d+(\.\d+)*(\+|\.x)?$")


def heading_stack(stack: list[str]) -> list[str]:
    """The stack as the heading shows it: plain "Python", never "Python 3.12" / "Python 3.14" (the user's rule,
    2026-11-08); a repeat left by that is dropped."""
    out = ["Python" if PY_VERSION.match(s.strip()) else s.strip() for s in stack]
    return list(dict.fromkeys(x for x in out if x))


def render(entries: list[ProjectEntry], cards: dict[str, dict], years: dict[str, str], macro: bool = False,
           short: frozenset[str] = frozenset()) -> str:
    """macro: the template defines \\resumeProjectText (paragraph aligned with the heading); else an item list."""
    out = ["", "  \\resumeSubHeadingListStart"]
    for e in entries:
        c = cards[e.repo.lower()]
        text = e.short if e.repo in short and e.short else e.paragraph
        date = to_latex(years.get(c["full_name"], ""))
        heading = (f"\\textbf{{\\href{{{c['url']}}}{{{to_latex(e.title)}}}}} $|$ "
                   f"\\emph{{{to_latex(', '.join(heading_stack(e.stack)))}}}")
        # The template's l/r tabular columns do not wrap. Reserve the actual
        # date width before laying out the title/stack, keeping fonts unchanged.
        heading = (r"\begingroup\setbox0=\hbox{\normalsize " + date + "}"
                   r"\parbox[t]{\dimexpr0.97\textwidth-\wd0-2\tabcolsep-1em\relax}"
                   r"{\raggedright " + heading + r"}\endgroup")
        out += ["    \\resumeProjectHeading",
                f"      {{{heading}}}{{{date}}}",
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


# --- the source-sentence contract ---------------------------------------------------------------------------------------------------------
# A displayed description is composed of whole sentences of the project's OWN card. The splitter errs towards MERGING (an abbreviation or
# initialism never ends a sentence), so a piece it returns is always a contiguous, complete stretch of source text, never half of one.
_TERMINAL = re.compile(r"[.!?][\"'\u201d\u2019)\]]*(?=\s)")
_SENTENCE_START = re.compile(r"[A-Z0-9]")
_ABBREVIATION_END = re.compile(r"(?i)\b(?:e\.g|i\.e|etc|vs|approx|inc|ltd|dr|mr|mrs|fig|cf)\.[\"')\]]*$|(?<![A-Za-z0-9])(?:[A-Za-z]\.){2,}[\"')\]]*$")


def split_sentences(text: str) -> list[str]:
    """The text as sentences: a boundary is a terminal . ! or ? (with closing quotes/brackets) followed by space and a capital or digit, and is
    never taken after an abbreviation or initialism (`e.g.`, `U.S.`). Whitespace is normalised. The pieces joined with single spaces give the text back."""
    text = " ".join(str(text or "").split())
    out, start = [], 0
    for match in _TERMINAL.finditer(text):
        rest = text[match.end():].lstrip()
        if not rest or not _SENTENCE_START.match(rest) or _ABBREVIATION_END.search(text[start:match.end()]):
            continue
        out.append(text[start:match.end()].strip())
        start = match.end()
    if tail := text[start:].strip():
        out.append(tail)
    return out


def _single_quotes_balanced(sentence: str) -> bool:
    """Every opening single quote has a closing one. An apostrophe inside a word ("company's", "don't") or ending a plural possessive
    ("customers' data") is not a quote, so a normal possessive no longer makes a complete sentence look unfinished."""
    text = re.sub(r"(?<=\w)'(?=\w)", "", sentence)
    opens = len(re.findall(r"(?:^|[\s(\[\u201c\"])'(?=\w)", text))
    closes = len(re.findall(r"(?<=[\w.,!?)])'(?!\w)", text))
    return closes >= opens


def _complete(sentence: str) -> bool:
    """A usable whole sentence: starts like a sentence, ends in terminal punctuation (never a synthetic one), is long enough to say something,
    has balanced brackets and quotes, does not end in an abbreviation and holds no dash used as punctuation."""
    return (len(sentence) >= 30 and bool(_SENTENCE_START.match(sentence)) and bool(re.search(r'[.!?]["\')\]\u201d\u2019]*$', sentence))
            and not _ABBREVIATION_END.search(sentence) and sentence.count("(") == sentence.count(")") and sentence.count('"') % 2 == 0
            and _single_quotes_balanced(sentence) and not DASH.search(sentence))


def source_sentences(card: dict) -> list[str]:
    """The numbered pool a description may be built from: the complete sentences of the card's summary, then its results, each once, in card order.

    Numbers (1-based) are positions in this list. It depends on the card's text only, so the writer, the validators and a later refresh agree on it.
    """
    out: list[str] = []
    for value in [(card or {}).get("summary")] + list((card or {}).get("results") or []):
        if isinstance(value, str):
            out += [s for s in split_sentences(value) if _complete(s) and s not in out]
    return out


def bound_text(text: str, card: dict) -> str | None:
    """The text when it is made only of whole, distinct source sentences of THIS card (its pieces joined by single spaces), else None.

    A half sentence, an added clause, a changed verb, a dropped negation or another repository's sentence all fail, because each piece must be
    one of the card's own sentences exactly. A text with no sentence at all is not bound.
    """
    pieces = split_sentences(text)
    pool = set(source_sentences(card))
    if not pieces or len(pieces) > MAX_SENTENCES or len(set(pieces)) != len(pieces) or not all(p in pool for p in pieces):
        return None
    return " ".join(pieces)


def unbound_excerpt(text: str, card: dict) -> str:
    """The first piece of the text that is not a source sentence of the card, around its first word the card does not use (for the finding shown to the
    writer: the added clause, not the harmless start of the sentence)."""
    pool = set(source_sentences(card))
    pieces = split_sentences(text)
    piece = next((p for p in pieces if p not in pool), pieces[0] if pieces else "")
    known = set(re.findall(r"[a-z0-9]+", " ".join(_card_strings(card)).lower()))
    novel = next((m.start() for m in re.finditer(r"[A-Za-z0-9]+", piece) if m.group(0).lower() not in known), 0)
    start = piece.rfind(" ", 0, max(0, novel - 30)) + 1 if novel > 30 else 0
    return ("\u2026" if start else "") + piece[start:start + 220] + ("\u2026" if start + 220 < len(piece) else "")


def compose(card: dict, ids, limit: int, floor: int = 0) -> str:
    """The description for a selection of source sentence numbers: those sentences, in the order given, joined by single spaces.

    Sentences that no longer fit under `limit` are left out whole from the end (the description is never cut inside a sentence).

    Args:
        card: The project's saved card.
        ids: 1-based source sentence numbers, distinct, in reading order.
        limit: Longest allowed description.
        floor: Shortest acceptable description.

    Raises:
        ValueError: A number is missing, repeated, out of range or not a number, or the fitting sentences are shorter than `floor`.
    """
    pool = source_sentences(card)
    ids = list(ids or [])
    if not ids:
        raise ValueError(f"no source sentence numbers given; choose from 1-{len(pool)}")
    if len(ids) > MAX_SENTENCES or len(set(ids)) != len(ids):
        raise ValueError(f"source sentence numbers must be distinct and at most {MAX_SENTENCES}: {ids}")
    if bad := [i for i in ids if isinstance(i, bool) or not isinstance(i, int) or not 1 <= i <= len(pool)]:
        raise ValueError(f"source sentence number(s) {bad} do not exist for this project; its sentences are 1-{len(pool)}")
    chosen: list[str] = []
    for i in ids:
        if len(" ".join(chosen + [pool[i - 1]])) > limit:
            break
        chosen.append(pool[i - 1])
    text = " ".join(chosen)
    if not chosen:
        raise ValueError(f"no complete useful prefix of the selected source sentences fits {limit} characters; choose shorter whole sentences totalling {floor}-{limit}")
    if len(text) < floor:
        raise ValueError(f"the selected source sentences give {len(text)} characters (limit {limit}); choose whole sentences totalling {floor}-{limit}")
    return text


_JOB_STOP = frozenset("a an the of to from in on at by for with within via using use uses used its their his her our is are was were be been "
                                    "being and or as that which while but so than this these those it they when if into over across through about "
                                    "experience ability strong work working team teams years year role position company skills knowledge".split())


def job_terms(jd) -> frozenset[str]:
    """The content words of a posting (title, duties, qualifications, skills), lower case, used only to rank source sentences."""
    text = " ".join([getattr(jd, "title", "") or ""] + list(getattr(jd, "responsibilities", []) or []) + list(getattr(jd, "basic_qualifications", []) or [])
                    + list(getattr(jd, "preferred_qualifications", []) or []) + list(getattr(jd, "technical_skills", []) or []))
    return frozenset(w for w in re.findall(r"[a-z][a-z0-9+#.-]{2,}", text.lower()) if w not in _JOB_STOP)


def _relevance(sentence: str, terms: frozenset[str]) -> int:
    """How many distinct posting words a source sentence uses (ranking only)."""
    return len(terms & {w.strip(".-") for w in re.findall(r"[a-z][a-z0-9+#.-]{2,}", sentence.lower())})


# --- the title ---------------------------------------------------------------------------------------------------------------------------------
def _title_words(text: str) -> list[str]:
    return [w.rstrip(".") for w in re.findall(r"[a-z0-9][a-z0-9.+#]*", (text or "").casefold())]


def card_title(card: dict) -> str:
    """A title built only from the card's own: its first segment ("SolBot — Agentic RAG..." -> "SolBot"), cut at a word boundary to 45 characters,
    else the repository name."""
    first = re.split(r"\s+[—–:|-]\s+", str(card.get("title") or ""))[0].strip()
    words, out = first.split(), ""
    for w in words:
        if len(f"{out} {w}".strip()) > 45:
            break
        out = f"{out} {w}".strip()
    return out or str(card.get("full_name") or "").split("/")[-1] or "Project"


def title_supported(title: str, card: dict, owner_title: str | None = None) -> bool:
    """The title is the owner's template title for this repository, or uses only words of the card's own title / the repository name, in their order.

    A generated title may drop words of the source title but never add one, so it cannot name a feature, outcome or award the card does not.
    """
    if owner_title and " ".join(_title_words(title)) == " ".join(_title_words(owner_title)):
        return True
    words = _title_words(title)
    name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(card.get("full_name") or "").split("/")[-1]).replace("-", " ").replace("_", " ")
    reference = iter(_title_words(name) + _title_words(card.get("title") or ""))
    return bool(words) and all(w in reference for w in words)      # `in` on an iterator consumes it: an ordered subsequence


def entry_binding_issues(entry: dict, card: dict, owner: tuple[str, str] | None = None) -> list[str]:
    """Why this stored project is not bound to its own card: each of its descriptions must be whole source sentences, its title the owner's or the card's.

    Args:
        entry: One `projects.json` ranked entry.
        card: That repository's saved evidence card.
        owner: The template's (title, year) for this repository, when it has one.

    Returns:
        Problems, empty when bound. An absent short description is allowed (the project then renders its full one).
    """
    out = []
    for field, label in (("paragraph", "full"), ("short", "short")):
        text = entry.get(field) or ""
        if not text and field == "short":
            continue
        if bound_text(text, card) is None:
            out.append(f"{label} description is not made of complete sentences of its own source card (not a source sentence: "
                       f"{unbound_excerpt(text, card)!r})")
    if not title_supported(entry.get("title") or "", card, owner[0] if owner else None):
        out.append(f"title {entry.get('title')!r} is not the owner's template title or words of its own card title")
    return out


def recover_years(state: dict, known: dict | None = None) -> list[str]:
    """Give every project whose saved date is neither the owner's nor from its own repository the date `choose` would have set (the card's creation
    month, else its last push, else the owner's template date).

    Args:
        state: `projects.json`; its `years` are changed in place.
        known: `known_entries` of the owner template.

    Returns:
        The repositories whose date changed.
    """
    known, evidence, fixed = known or {}, saved_evidence(state), []
    years = state.setdefault("years", {})
    for entry in state.get("ranked", []):
        repo = str(entry.get("repo") or "")
        card, owner = evidence.get(repo.casefold()), known.get(repo.casefold())
        if card is None or repo not in years:
            continue
        allowed = {d for d in (month_year(card.get("created_at") or ""), month_year(card.get("pushed_at") or ""), owner[1] if owner else "") if d}
        if allowed and years[repo] not in allowed:
            years[repo] = month_year(card.get("created_at") or card.get("pushed_at") or "") or (owner[1] if owner else "")
            fixed.append(repo)
    return fixed


def binding_findings(state: dict, known: dict | None = None) -> list[str]:
    """The source-sentence contract checked against the job's OWN saved evidence, independently of anything the writer or a cache claims.

    Every ranked project (not only the displayed ones: a layout change can show a hidden one) with saved evidence must have a full and a short
    description made of whole sentences of that evidence card, a title that is the owner's or the card's, and a date that is the owner's or the
    card's. A project with no saved evidence is reported by `missing_evidence`. Prose saved before this contract (free text, another
    repository's sentences, an edited sentence) is reported here and needs regeneration or `recover_scope`.

    Args:
        state: `projects.json`.
        known: `known_entries` of the owner template (repo lower case -> (title, year)).

    Returns:
        One instruction per problem, in the wording the project writer receives.
    """
    known, evidence, out = known or {}, saved_evidence(state), []
    years = state.get("years") if isinstance(state.get("years"), dict) else {}
    for i, entry in enumerate(state.get("ranked", []), 1):
        repo = str(entry.get("repo") or "")
        card = evidence.get(repo.casefold())
        if card is None:
            continue
        owner = known.get(repo.casefold())
        out += [f"project {i} ({repo}) {issue}; use only numbered source sentences of this repository, exactly as written"
                for issue in entry_binding_issues(entry, card, owner)]
        allowed = {d for d in (month_year(card.get("created_at") or ""), month_year(card.get("pushed_at") or ""), owner[1] if owner else "") if d}
        if allowed and repo in years and years[repo] not in allowed:
            out.append(f"project {i} ({repo}) date {years[repo]!r} is neither the owner's template date nor from its own repository")
    return out


# --- capability is not achievement -------------------------------------------------------------------------------------------------------
# A card that lists a tool or an audit ("Playwright + axe-core (WCAG 2.1 AA audits)") shows what the project USES or TESTS AGAINST. Saying the project
# "achieved high accessibility standards", "is HIPAA compliant" or "earned SOC 2 certification" claims a result the repository has to show itself.
NAMED_STANDARD = re.compile(r"(?i)\b(WCAG(?:\s*\d(?:\.\d)?)?(?:\s*(?:AAA|AA|A)\b)?|HIPAA|SOC\s*2|GDPR|CCPA|FERPA|PCI(?:[-\s]?DSS)?|FedRAMP|ISO(?:/IEC)?[-\s]?\d{4,5}|"
                            r"Section\s*508|OWASP(?:\s*Top\s*10)?|NIST(?:\s*\d[\w.-]*)?)\b")
ACHIEVED_CUE = re.compile(r"(?i)\b(?:compl(?:y|ies|ied|iant|iance)|conform\w*|certif\w*|accredit\w*|passed|passes|passing|achiev\w*|attain\w*|meets?|met|adher\w*|"
                          r"satisf\w*|fulfil\w*|zero\s+violations|no\s+violations|0\s+violations)\b")
# A claim or a card line that denies or postpones the standard is not an achievement ("not HIPAA compliant", "working toward SOC 2", "designed to meet WCAG").
NEGATED = re.compile(r"(?i)\b(?:not|no|never|without|lack(?:s|ing)?|isn't|aren't|wasn't|doesn't|don't|non|yet\s+to|working\s+toward\w*|pursuing|preparing\s+for|planned|"
                     r"aims?\s+to|aiming\s+to|ready\s+for|designed\s+to|intended\s+to|goal\s+of|toward)\b[^;]{0,30}$")
GENERIC_STANDARD_CLAIM = re.compile(r"(?i)\b(?:achiev\w*|attain\w*|reach\w*|meets?|met|exceed\w*|pass\w*|satisf\w*|ensur\w*|guarantee\w*)\b[^.!?]{0,40}?"
                                    r"\b(standards?|compliance|conformance|certifications?|accreditations?)\b")


# The adjective form of the same outcome: "ensuring deterministic and compliant cloud setups", "generates compliant infrastructure". A card that maps
# controls or runs checks documents a capability; a result adjective needs the card to affirm the same outcome. Reading it as an outcome requires the
# word to describe what the project produces, so a checked or detected property ("flags non-compliant resources", "verifies the plan is compliant",
# "checks whether it is compliant") and a denial or plan ("not yet compliant", "designed to be compliant") are left alone.
ADJECTIVE_OUTCOME = re.compile(r"(?i)(?<!\w)(?:compliant|conformant)\b")


def _card_strings(card: dict) -> list[str]:
    """Every text value of a card (embeddings excluded), one string each: evidence is judged inside a single value, never across neighbouring fields."""
    out: list[str] = []

    def walk(value, key=""):
        """Collect the strings under one card value (recursing into lists and dicts), skipping embeddings."""
        if key in ("embedding", "vector", "embeddings"):
            return
        if isinstance(value, str):
            out.append(value)
        elif isinstance(value, dict):
            for k, v in value.items():
                walk(v, k)
        elif isinstance(value, list):
            for v in value:
                walk(v, key)
    walk(card or {})
    return out


# "compliance mapping", "certification tracking", "conformance checker" name a CAPABILITY of the project, not a standard it achieved.
_CAPABILITY = re.compile(r"(?i)\b(?:compliance|conformance|certification|accreditation)\s+(?:mapping|maps?|checks?|checker|scann\w*|tool\w*|framework|report\w*|matri\w*|dashboard|"
                         r"automation|engine|workflow|templates?|controls?|polic\w*|evidence|documentation|logging|rules?|validation|testing|analysis|assessment|audits?|"
                         r"tracking|requirements?)\b")


def _mask(text: str) -> str:
    """The text with capability phrases blanked out (same length, so positions hold) before looking for achievement cues."""
    return _CAPABILITY.sub(lambda m: " " * len(m.group(0)), text)


def _family(standard: str) -> str:
    """The standard without its version or level: `WCAG 2.1 AA` -> wcag, `SOC 2` -> soc2, `ISO/IEC 27001` -> iso27001, `Section 508` -> section508."""
    s = re.sub(r"(?i)/iec", "", standard.lower())
    keep = re.match(r"(?i)(wcag|hipaa|soc\s*2|gdpr|ccpa|ferpa|pci|fedramp|iso[-\s]?\d{4,5}|section\s*508|owasp|nist)", s)
    return re.sub(r"[^a-z0-9]", "", keep.group(1)) if keep else re.sub(r"[^a-z0-9]", "", s)


def _levels(standard: str) -> tuple[list[str], str]:
    """The version numbers and conformance level written with a standard: `WCAG 2.1 AA` -> (['2.1'], 'aa')."""
    level = re.search(r"(?i)\b(AAA|AA|A)\s*$", standard.strip())
    return re.findall(r"\d+(?:\.\d+)?", standard), (level.group(1).lower() if level else "")


def _affirms(window: str, cue: "re.Match") -> bool:
    """The cue in this window is an affirmation, not a denial or a plan."""
    return not NEGATED.search(window[max(0, cue.start() - 45):cue.start()])


_OUTCOME_VERB = re.compile(r"(?i)\b(?:ensur(?:e|es|ing)|guarantee(?:s|ing)?|produc(?:e|es|ing)|generat(?:e|es|ing)|deliver(?:s|ing)?|yield(?:s|ing)?|creat(?:e|es|ing)|"
                           r"build(?:s|ing)?|deploy(?:s|ing)?|provid(?:e|es|ing))\b")
_CHECK_VERB = re.compile(r"(?i)\b(?:check\w*|validat\w*|verif\w*|scan\w*|detect\w*|flag\w*|audit\w*|test\w*|assess\w*|identif\w*|determine[sd]?|determining|report\w*|whether)\b")


def _generic_outcome_matches(text: str) -> list[tuple[str, int]]:
    """Affirmed generic standard outcomes with their position: a noun after an achievement verb, or the adjective ("compliant") as a result.

    Mapping/checking capabilities, denials and plans are excluded; so is an adjective that describes something a verb of checking examines
    ("verifies that the plan is compliant"), unless an outcome verb ("ensuring", "produces") follows that check in the same clause.
    """
    out = []
    masked = _mask(text)
    for match in GENERIC_STANDARD_CLAIM.finditer(masked):
        if _affirms(text, match):
            noun = match.group(1).lower().removesuffix('s')
            out.append(('compliance' if noun == 'conformance' else noun, match.start()))
    for match in ADJECTIVE_OUTCOME.finditer(masked):
        clause = re.split(r"[.!?;,]", masked[max(0, match.start() - 60):match.start()])[-1]      # a comma ends the checking clause
        clause = _OUTCOME_VERB.split(clause)[-1]                                                 # so does an outcome verb
        if _affirms(text, match) and not _CHECK_VERB.search(clause):
            out.append(('compliance', match.start()))
    return out


def _generic_outcomes(text: str) -> set[str]:
    """Affirmed generic standard outcomes, excluding mapping/checking capabilities, denials and plans."""
    return {noun for noun, _ in _generic_outcome_matches(text)}


def _shown(sentence: str, position: int) -> str:
    """The sentence as the writer is told about it: cut at 160 characters, but never so early that the offending phrase is lost."""
    sentence = sentence.strip()
    if len(sentence) <= 160:
        return sentence
    start = sentence.rfind(" ", 0, max(0, position - 60)) + 1 if position > 100 else 0
    return sentence[start:start + 160]


def standard_outcome_claims(text: str, card: dict) -> list[str]:
    """Achieved-standard claims in a project paragraph that the project's OWN card does not support.

    "Tested accessibility with Playwright and axe-core WCAG 2.1 AA audits" (capability) is fine. "Achieved high accessibility standards" or "HIPAA-compliant"
    is an outcome and needs the card to say so too: the named standard next to an achievement cue within 150 characters, or the same generic phrase.
    Ordinary qualitative benefits and numbers the repository supplies are not touched.

    Args:
        text: The displayed paragraph (the variant that is actually rendered).
        card: The project's saved card.

    Returns:
        The unsupported claim phrases, in reading order.
    """
    strings = _card_strings(card)
    generic_supported = set().union(*(_generic_outcomes(s) for s in strings))
    out = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        named = list(NAMED_STANDARD.finditer(sentence))
        for m in named:
            near = sentence[max(0, m.start() - 60):m.end() + 60]
            cues = [c for c in ACHIEVED_CUE.finditer(_mask(near)) if _affirms(near, c)]
            if not cues:
                continue                                  # "audits against WCAG 2.1 AA", "supports HIPAA-aligned logging", "not yet SOC 2 certified": no achievement claimed
            ident = _family(m.group(1))
            want_versions, want_level = _levels(m.group(1))

            def supports(s: str, e: "re.Match") -> bool:
                """This card occurrence of the standard affirms it, with the same version and level when both give one."""
                versions, level = _levels(e.group(1))
                if want_versions and versions != want_versions:
                    return False                          # another version (2.1 for 2.2), or none: generic WCAG evidence does not establish a specific version
                if want_level and level != want_level:
                    return False                          # AA is not AAA, and an unstated level does not establish a specific one
                window = s[max(0, e.start() - 150):e.end() + 150]
                return any(_affirms(window, c) for c in ACHIEVED_CUE.finditer(_mask(window)))
            if not any(supports(s, e) for s in strings for e in NAMED_STANDARD.finditer(s)
                       if _family(e.group(1)) == ident):
                out.append(sentence.strip()[:160])
                break
        if not named and (unsupported := [pos for noun, pos in _generic_outcome_matches(sentence) if noun not in generic_supported]):
            out.append(_shown(sentence, unsupported[0]))                       # an unnamed standard ("meets industry standards"); a named one was judged above
    return out


# --- an exclusivity claim needs the source's own exclusivity ---------------------------------------------------------------------------
# "answers using only official documentation" is a restriction. A card that merely names its main corpus, while also documenting a web fallback or
# user-supplied URLs, describes a primary source, not an exclusive one. The generated restriction is supported only when one card value states a
# restriction (only/solely/exclusively) over the same content words; "primarily", "fully local" or a corpus name do not establish it.
_RESTRICTION = re.compile(r"(?i)\b(?:only|solely|exclusively)\b")
_NOT_RESTRICTION_BEFORE = re.compile(r"(?i)(?:\b(?:the|one\s+and|if)\s+$|\b(?:not|never|neither|nor|no\s+longer)\b[^.;,:]{0,20}$|n't\b[^.;,:]{0,20}$)")   # "not only X but also Y", "does not rely only on", "the only X", "if only"
_QUANTITY_AFTER = re.compile(r"(?i)^\s*(?:\d|one\b|two\b|three\b|four\b|five\b|a\b|an\b|few\b|some\b|several\b|once\b|twice\b)")
_SCOPE_STOP = frozenset("a an the of to from in on at by for with within via using use uses used its their his her our is are was were be been being and or "
                        "as that which while whereas but so then than this these those it they when if once after until unless where into onto over across through "
                        "against about".split())
_SCOPE_ALIAS = {"docs": "documentation", "doc": "documentation", "internet": "web", "online": "web"}


def _scope_words(text: str) -> list[str]:
    """Content words of a phrase, lower case; a few common abbreviations unified. Inflections are compared by `_same_word`, never cut here."""
    words = []
    for w in re.findall(r"[a-z0-9][a-z0-9+#.-]*", text.lower()):
        w = _SCOPE_ALIAS.get(w.strip(".-"), w.strip(".-"))
        if w and w not in _SCOPE_STOP:
            words.append(w)
    return words


_INFLECTIONS = ("ations", "ation", "ings", "ing", "ies", "es", "ed", "s")


def _forms(word: str) -> set[str]:
    """The word and the stems its regular inflections could come from (reports/report, stored/store, documentation/document)."""
    forms = {word}
    for suffix in _INFLECTIONS:
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            stem = word[:-len(suffix)]
            forms |= {stem, stem + "e"} | ({stem + "y"} if suffix == "ies" else set())
    return forms


def _same_word(a: str, b: str) -> bool:
    """The same word up to regular inflection. Words that merely begin alike (private, privacy-safe) are different concepts."""
    return bool(_forms(a) & _forms(b))


_PREPOSITION = re.compile(r"(?i)\b(?:from|using|with|in|on|via|by|to|for|through|within|at|into|across|over|about|during|when|while)\b")
_SPLIT_ITEMS = re.compile(r"(?i)\s*(?:/|\band\b|\bor\b|\bplus\b|&)\s*")


def _items(phrase: str) -> list[list[str]]:
    """The separately restricted things in a phrase ("public reports and private datasets" is two), each cut at its first preposition so that
    "local models for inference" restricts the models, not their purpose."""
    items = []
    phrase = _SCOPE_CONDITION.split(phrase, maxsplit=1)[0]  # conditions have their own binding, not restricted objects
    for part in _SPLIT_ITEMS.split(phrase):
        part = re.sub(r"(?i)^\W*(?:(?:from|using|with|in|on|via|by|to|through|within)\s+)+", "", part)
        part = _PREPOSITION.split(part, maxsplit=1)[0]
        if words := _scope_words(part)[:6]:
            items.append(words)
    return items


def _restrictions(sentence: str) -> list[tuple[str, list[list[str]]]]:
    """Affirmed restrictions in a sentence: (printed phrase, the separately restricted word groups).

    "not only X", "the only X" and "only three X" are not restrictions. A marker followed by words restricts those words; a trailing marker
    ("... official documentation only") restricts the words just before it, back to the last preposition or clause break.
    """
    out = []
    for m in _RESTRICTION.finditer(sentence):
        if _NOT_RESTRICTION_BEFORE.search(sentence[max(0, m.start() - 34):m.start()]) or _QUANTITY_AFTER.match(sentence[m.end():]):
            continue
        after = re.split(r"[,;:.()!?]|\b(?:that|which|while|whereas|but)\b", sentence[m.end():], maxsplit=1, flags=re.I)[0]
        before = re.split(r"[,;:.()!?]", sentence[:m.start()])[-1]
        before = re.split(r"(?i)\b(?:from|using|with|in|on|via|by|to|of|for|through)\b", before)[-1]
        lo, hi = max(0, m.start() - 40), m.end() + 60
        shown = sentence[lo:hi].strip()
        shown = shown.split(' ', 1)[1] if lo and ' ' in shown else shown       # no half word at either end
        groups = _items(after) or _items(before)
        out.append((shown.rsplit(' ', 1)[0] if hi < len(sentence) and ' ' in shown else shown, groups))
    return out


def _covers(known: list[str], wanted: list[str]) -> bool:
    """Every word of the generated group appears, up to inflection, in ONE restricted group of the card (modifiers are not borrowed across items)."""
    return all(any(_same_word(w, c) for c in known) for w in wanted)


_SCOPE_CONDITION = re.compile(r"(?i)\b(when|if|unless|until|before|after|while)\b((?:[^,;:.!?]|,\s*(?:and|or)\b)*)")


def _scope_conditions(sentence: str) -> tuple[tuple[str, ...], ...]:
    """Keep a restriction's stated conditions, including negation and and/or, rather than broadening it to an unconditional promise.

    This is a conservative sentence-level check: only identical normalized conditions establish support. Pronouns and copulas may be omitted
    ("when it is available" / "when available"); logical operators and polarity may not. Unconditional source support needs no such qualification.
    """
    conditions = []
    for match in _SCOPE_CONDITION.finditer(sentence):
        marker = "if" if match[1].lower() == "when" else match[1].lower()
        words = re.findall(r"[a-z0-9][a-z0-9+#.-]*", match[2].lower())
        words = [_SCOPE_ALIAS.get(w.strip(".-"), w.strip(".-")) for w in words
                 if w not in {"it", "they", "is", "are", "was", "were", "the", "a", "an"}]
        conditions.append((marker, *words))
    return tuple(conditions)


def scope_claims(text: str, card: dict) -> list[str]:
    """Generated restrictions (only/solely/exclusively) that the project's OWN card does not state.

    A restriction is supported when one card value states a restriction over the same content words, in either position ("only from official
    documentation", "official documentation only"), and each restricted item of the claim lies within one restricted item of the card. Documented
    fallbacks or alternatives, a named primary corpus and "not only X but Y" never support an exclusivity claim. Faithful rewording of a card's
    restriction (plurals, -ing/-ed forms, "docs" for "documentation") still matches. Ordinary qualitative benefits, numbers and sentences with no
    restriction word are not touched.

    Args:
        text: The displayed paragraph.
        card: The project's saved card.

    Returns:
        The unsupported phrases, in reading order.
    """
    supported = [(group, _scope_conditions(sentence)) for value in _card_strings(card)
                 for sentence in re.split(r"(?<=[.!?])\s+", value)
                 for _, groups in _restrictions(sentence) for group in groups]
    claims = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        for phrase, groups in _restrictions(sentence):
            conditions = _scope_conditions(sentence)
            if groups and not all(any(_covers(known, wanted) and (not qualified or qualified == conditions)
                                      for known, qualified in supported) for wanted in groups):
                claims.append(phrase)
    return claims


def faithful_description(card: dict, limit: int, floor: int, vocabulary: list[str] | None = None, terms: frozenset[str] | None = None,
                         minimum: int | None = None) -> str | None:
    """A description made only of complete sentences of the card's own summary and results (never cut, never rejoined with new punctuation).

    Candidate sentences are the card's `source_sentences` that also pass the achieved-standard, exclusivity and technology checks as generated
    text would. With `terms` (the posting's words) the most relevant sentences are taken first, otherwise they are taken in card order; either
    way they are printed in card order, until `floor` characters are reached or nothing more fits under `limit`.

    Args:
        card: The project's saved card.
        limit: Longest allowed description.
        floor: Length at which the description is long enough (selection stops).
        vocabulary: Technology names that the description must not name unless the card establishes them (`tech_vocabulary`).
        terms: The posting's content words (`job_terms`), to prefer relevant sentences.
        minimum: Shortest acceptable result; default half of `floor`, at least 60.

    Returns:
        The description, or None when the card's whole sentences cannot reach `minimum` (the caller skips the variant or project, or keeps the finding).
    """
    pool = [s for s in source_sentences(card) if not unsupported_claims(s, card, vocabulary)]
    order = sorted(range(len(pool)), key=lambda i: (-_relevance(pool[i], terms or frozenset()), i))
    chosen: list[int] = []
    for i in order:
        text = " ".join(pool[j] for j in sorted(chosen + [i]))
        if len(text) > limit or len(chosen) >= MAX_SENTENCES:
            continue
        chosen.append(i)
        if len(text) >= floor:
            break
    text = " ".join(pool[j] for j in sorted(chosen))
    return text if text and len(text) >= (minimum if minimum is not None else max(60, floor // 2)) else None


def faithful_variants(card: dict, vocabulary: list[str] | None = None, terms: frozenset[str] | None = None) -> tuple[str | None, str | None]:
    """(full, short) descriptions of whole source sentences within the display bounds; None for a variant the card cannot fill."""
    return (faithful_description(card, FULL_LIMIT, MIN_PARAGRAPH, vocabulary, terms, minimum=FULL_FLOOR),
            faithful_description(card, SHORT_LIMIT, SHORT_WANTED, vocabulary, terms, minimum=SHORT_FLOOR))


def recover_scope(entry: dict, card: dict, vocabulary: list[str] | None = None, terms: frozenset[str] | None = None) -> list[str]:
    """Replace any displayed variant (full or short) of a saved project entry that has a source finding (an unsupported restriction, achieved
    standard or technology) or is not made of its card's whole sentences, with the card's own sentences (`recover_stack` handles the printed stack,
    `recover_title` the title).

    Args:
        entry: One `projects.json` ranked entry; changed in place.
        card: The project's saved card.
        vocabulary: Technology names to look for in prose (`tech_vocabulary`).
        terms: The posting's words (`job_terms`), to prefer relevant sentences.

    Returns:
        The fields changed (empty when nothing was wrong or no faithful text exists).
    """
    fixed = []
    for field, limit, floor, minimum in (("paragraph", FULL_LIMIT, MIN_PARAGRAPH, FULL_FLOOR), ("short", SHORT_LIMIT, SHORT_WANTED, SHORT_FLOOR)):
        text = entry.get(field) or ""
        if field == "short" and not text:
            continue
        if unsupported_claims(text, card, vocabulary) or bound_text(text, card) is None:
            new = faithful_description(card, limit, floor, vocabulary, terms, minimum=minimum)
            if new:
                entry[field], _ = new, fixed.append(field)
            elif field == "short":
                entry[field], _ = "", fixed.append(field)         # the card cannot fill a short variant: the project is shown with its full one, not with prose
    return fixed


def recover_title(entry: dict, card: dict, owner: tuple[str, str] | None = None) -> bool:
    """Give an entry whose title is neither the owner's nor made of its own card title's words the card's own title.

    Args:
        entry: One `projects.json` ranked entry; changed in place.
        card: That repository's saved card.
        owner: The template's (title, year) for this repository, when it has one.

    Returns:
        Whether the title changed.
    """
    if title_supported(entry.get("title") or "", card, owner[0] if owner else None):
        return False
    entry["title"] = owner[0] if owner else card_title(card)
    return True


def recover_stack(entry: dict, card: dict) -> bool:
    """Drop printed technologies the entry's own card does not show (the same grounding the heading gets when a project is first chosen).

    Args:
        entry: One `projects.json` ranked entry; changed in place.
        card: The project's saved card.

    Returns:
        Whether the stack changed.
    """
    if not entry.get("stack"):
        return False
    kept, dropped = ground_stack(list(entry["stack"]), card)
    if dropped:
        entry["stack"] = kept
    return bool(dropped)


def content_findings(state: dict, cards: dict | None = None) -> list[str]:
    """Check the actual project variants selected for rendering, including short forms.

    A model can satisfy max_length by stopping mid-sentence. Page count and text
    bounds do not detect that, so these drafts must be rewritten before use.

    Args:
        state: `projects.json` (ranked entries, how many are kept, which use their short form).
        cards: Saved repository cards by lower-case full name. When given, an achieved-standard claim the card does not support is also reported
            (`standard_outcome_claims`); without cards only completeness is checked.

    Returns:
        One instruction per problem, in the wording the project writer receives.
    """
    short = set(state.get("short", []))
    out = []
    vocab = tech_vocabulary(cards or {}, state.get("ranked", [])) if cards else []
    for i, entry in enumerate(state.get("ranked", [])[:state.get("kept", 0)], 1):
        text = entry.get("short") if entry.get("repo") in short and entry.get("short") else entry.get("paragraph", "")
        if not re.search(r'''[.!?]["'\u201d\u2019)\]]*\s*$''', text):
            out.append(f"project {i} description ends without a complete sentence: {text[-100:]!r}; "
                       "rewrite the whole description within its length budget, ending the final sentence")
        card = (cards or {}).get((entry.get("repo") or "").casefold())
        if card and entry.get("stack"):
            _, ungrounded = ground_stack(list(entry["stack"]), card)
            if ungrounded:
                out.append(f"project {i} prints technologies its own card does not show: {ungrounded}; remove them (a stack comes only from that repository's own card)")
        for claim in (standard_outcome_claims(text, card) if card else []):
            out.append(f"project {i} claims an achieved standard or compliance that its own card does not show ({claim!r}): the card lists tooling or capability "
                       "only. Say what the project uses, tests or audits against (for example 'tested with <tool> against <standard>'); never that the standard "
                       "was achieved, met, passed or certified")
        for claim in (prose_tech_claims(text, card, vocab) if card else []):
            out.append(f"project {i} describes technologies its own card does not show ({claim!r}): a description names only what that repository's own card "
                       "establishes (another repository's card is not evidence); remove it or say what the card shows instead")
        for claim in (scope_claims(text, card) if card else []):
            out.append(f"project {i} claims a restriction that its own card does not state ({claim!r}): the card documents a primary source, not an exclusive "
                       "one (fallbacks, alternative or user-supplied sources are part of its scope). Remove only/solely/exclusively, or say what the project "
                       "primarily uses without limiting it")
    return out


# --- the printed stack must be this repository's own ------------------------------------------------------------------------------------
_VERSION = re.compile(r"(?i)^v?\d+(?:\.\d+)*[+x]?$")
_CANON = ((r"\bamazon web services\b", "aws"), (r"\bgoogle cloud platform\b", "gcp"), (r"\bpostgres\b", "postgresql"), (r"\bnodejs\b", "node.js"),
          (r"\bsklearn\b", "scikit-learn"))


def _tokens(text: str) -> list[str]:
    """Whole words of a technology text, lower case, with common spellings unified and bare versions dropped ("Python 3.12" -> python)."""
    low = (text or "").lower()
    for pattern, canonical in _CANON:
        low = re.sub(pattern, canonical, low)
    return [t.rstrip(".") for t in re.findall(r"\.?[a-z0-9][a-z0-9.+#_-]*", low) if not _VERSION.match(t.rstrip("."))]   # '.net' keeps its dot: it is not 'net'


def card_terms(card: dict) -> list[str]:
    """The technologies a card lists, preserving meaningful punctuation and splitting grouped names on commas, semicolons and spaced `+` or `/`."""
    out: list[str] = []
    for line in card.get("tech_stack") or []:
        for part in re.split(r"[,;]|\s[/+]\s", re.sub(r"\([^)]*\)", " ", str(line))):
            term = re.sub(r"\s+", " ", part).strip().rstrip(".")
            if 0 < len(term) <= 28 and re.search(r"[A-Za-z0-9]", term) and term.lower() not in {t.lower() for t in out}:      # "R", "C" and "Go" are languages
                out.append(term)
    return out


_SEGMENT = re.compile(r"[,;:()|\n\[\]]|\s[/+]\s|\.\s|\s[-\u2013\u2014]\s")      # "DuckDB / Parquet" splits; "CI/CD", "C++", "TCP/IP" do not
# Words that are also ordinary English or very short: they establish a language/product only when the card's STRUCTURED tech_stack lists them, never from prose.
AMBIGUOUS_TERMS = {"go", "r", "c", "d", "swift", "rust", "ruby", "julia", "dart", "spark", "express", "next", "spring", "ada", "chef", "puppet", "unity"}


def _segments(card: dict) -> list[tuple[bool, list[str]]]:
    """The card's text as short phrases: `(is_structured_stack, tokens)`. A technology must appear as ONE contiguous phrase inside one of them,
    so words scattered over different fields or separated by commas never assemble into a product (`React` + `native browser inputs` is not React Native)."""
    out = []
    for key in ("tech_stack", "title", "summary", "evidence_notes", "readme"):
        value = card.get(key)
        for s in (value if isinstance(value, list) else [value]):
            for part in _SEGMENT.split(str(s or "")[:6000]):
                tokens = _tokens(part)
                if tokens:
                    out.append((key == "tech_stack", tokens))
    return out


def _contains(tokens: list[str], phrase: list[str]) -> bool:
    return any(tokens[i:i + len(phrase)] == phrase for i in range(len(tokens) - len(phrase) + 1))


def ground_stack(stack: list[str], card: dict) -> tuple[list[str], list[str]]:
    """Keep only the technologies this repository's own card establishes.

    A term is established when its words (versions ignored, common spellings unified) form ONE contiguous phrase inside one segment of the card's
    tech stack, title, summary, notes or README excerpt; a one-word term that is also an ordinary word or very short (`Go`, `R`, `Rust`, `Spark`) must
    come from the structured `tech_stack`. `AWS` in CloudForge's card does not make `AWS` true of another repository; `React` plus "native browser inputs"
    does not establish `React Native`; `Go` is not found inside `Django`; `Python 3.12` is `Python`; `AWS STS/IAM` establishes `AWS`.

    Args:
        stack: The technologies the writer chose, in its order.
        card: That repository's saved card.

    Returns:
        `(kept, dropped)`; when nothing is established, the card's own first technologies replace the list (never an empty heading).
    """
    segments = _segments(card)
    kept, dropped = [], []
    for term in stack:
        phrase = _tokens(term)
        ok = bool(phrase) and any(_contains(tokens, phrase) for structured, tokens in segments
                                  if structured or not (len(phrase) == 1 and phrase[0] in AMBIGUOUS_TERMS))
        if ok:
            if term not in kept:
                kept.append(term)
        else:
            dropped.append(term)
    return (kept or card_terms(card)[:5] or ["GitHub"]), dropped


# --- the prose may only name technologies this repository's own card establishes ----------------------------------------------------------
# The heading's stack is grounded (`ground_stack`), but a paragraph can still say "React for a dynamic frontend" after React was dropped from the heading.
# The vocabulary is DATA, not a list in this file: the technologies listed in the structured `tech_stack` of the job's saved cards (plus the printed
# stacks). A vocabulary term found in the selected prose must be established by that project's OWN card, by the same rule as the heading. Limits: a
# technology no saved card lists is not recognised, one-word names that are also ordinary words or very short (AMBIGUOUS_TERMS, `Go`, `R`) are never
# read from prose, and a lower-case mention ("react to events") is not taken for the framework. This cannot prove every feature a paragraph describes.
_PROSE_SKIP_AFTER = re.compile(r"(?i)^\s+(?:to|when|as|if|quickly|instantly|in\s+real)\b")


def tech_vocabulary(cards: "list[dict] | dict", entries: list | None = None) -> list[str]:
    """Technology names to look for in prose: every term in the structured `tech_stack` of the given cards and in the entries' printed stacks.

    Args:
        cards: Saved repository cards (a list, or a mapping of name to card).
        entries: Optional project entries (dicts) whose `stack` lists add to the vocabulary.

    Returns:
        Distinct terms, longest first so "React Native" is read before "React".
    """
    pool = list(cards.values()) if isinstance(cards, dict) else list(cards or [])
    terms: dict[str, str] = {}
    for card in pool:
        for term in card_terms(card) if isinstance(card, dict) else []:
            terms.setdefault(term.casefold(), term)
    for entry in entries or []:
        for term in (entry.get("stack") if isinstance(entry, dict) else None) or []:
            if isinstance(term, str) and term.strip():
                terms.setdefault(term.strip().casefold(), term.strip())
    usable = [t for t in terms.values() if (phrase := _tokens(t)) and not (len(phrase) == 1 and phrase[0] in AMBIGUOUS_TERMS)
              and not (re.fullmatch(r"[A-Za-z]{1,2}", t))]
    return sorted(usable, key=lambda t: (-len(t), t.casefold()))


@functools.lru_cache(maxsize=4096)
def _term_pattern(term: str) -> "re.Pattern | None":
    """A pattern for one technology that tolerates case, spacing and `.js`-style punctuation differences and never matches inside a longer name."""
    parts = []
    for token in _tokens(term):
        runs = re.findall(r"[a-z0-9]+|[+#]+", token)
        if not runs:
            return None
        parts.append(("\\." if token.startswith(".") else "") + r"[.\-_]?".join(re.escape(r) for r in runs))
    return re.compile(r"(?<![A-Za-z0-9+#])" + r"[\s\-_]?".join(parts) + r"(?![A-Za-z0-9+#]|\.(?!js\b)[A-Za-z0-9])", re.IGNORECASE)


# A technology the PROSE denies ("without React or TypeScript") is not a claim to have used it; a technology the SOURCE denies or postpones ("does not use React",
# "React is planned for a future release") is not evidence that it was built. Both are decided per clause, so a denial of one name never hides another.
_DENY_CUE = re.compile(r"(?i)\b(?:without|not|no|never|nor|neither|instead\s+of|rather\s+than|avoid\w*|excluding|minus|free\s+of)\b|n't\b|\bnon-")
_DENY_FILLER = re.compile(r"(?i)^(?:[\s,/&+]|\b(?:and|or|nor|any|either|using|use|uses|used|a|an|the|of|with)\b)*$")
_NOT_BUILT_CUE = re.compile(r"(?i)\b(?:not|no|never|without|nor|neither|instead\s+of|rather\s+than|avoid\w*|excluding|plans?|planned|planning|future|roadmap|upcoming|"
                            r"will|would|could|might|may|consider\w*|evaluat\w*|explor\w*|intend\w*|aims?|hop\w*|later|eventually|someday|todo|yet)\b|n't\b")
_CLAUSE = re.compile(r"(?<=[.!?;\n])\s+|[;\n]|,\s+(?:but|and|while|whereas|although)\b")


def _denied_in_prose(text: str, start: int, vocabulary: list[str]) -> bool:
    """Whether the technology mention at `start` is governed by a negation in its own clause (only list words / other names in between)."""
    clause = max((text.rfind(c, 0, start) for c in ".;:!?\n"), default=-1) + 1
    before = text[clause:start]
    cues = list(_DENY_CUE.finditer(before))
    if not cues:
        return False
    between = before[cues[-1].end():]
    for term in vocabulary:
        pattern = _term_pattern(term)
        if pattern is not None:
            between = pattern.sub(" ", between)
    return bool(_DENY_FILLER.match(between))


def _affirmed_card(card: dict, term: str) -> dict:
    """The card with every clause removed that names `term` only to deny or postpone it (`does not use React`, `React is planned`).

    The structured `tech_stack` is untouched (it lists what the repository uses); free-text fields lose just the non-affirming clauses.
    """
    pattern = _term_pattern(term)
    if pattern is None:
        return card
    out = dict(card)
    for key in ("title", "summary", "evidence_notes", "readme"):
        value = card.get(key)
        if value is None:
            continue

        def clean(raw) -> str:
            """Join the clauses of one field that do not deny or postpone the term."""
            kept = [c for c in _CLAUSE.split(str(raw)) if c and not (pattern.search(c) and _NOT_BUILT_CUE.search(c))]
            return " ".join(kept)
        out[key] = [clean(v) for v in value] if isinstance(value, list) else clean(value)
    return out


def prose_tech_claims(text: str, card: dict, vocabulary: list[str]) -> list[str]:
    """Technologies the displayed prose names that the project's OWN card does not establish.

    Longer names win over their prefixes ("React Native" is one claim, not React plus Native); every named technology is judged by `ground_stack`
    against this repository's card only (another repository's card listing it is not evidence). A lower-case or sentence-initial verb use
    ("react to", "React when") is not a technology mention.

    Args:
        text: The displayed paragraph (full or short).
        card: That repository's saved card.
        vocabulary: Names to look for (`tech_vocabulary`).

    Returns:
        The unsupported technology names as written in the prose, in reading order, each once.
    """
    taken: list[tuple[int, int]] = []
    found: list[tuple[int, str, str]] = []
    for term in vocabulary:
        pattern = _term_pattern(term)
        if pattern is None:
            continue
        for m in pattern.finditer(text):
            if any(m.start() < b and a < m.end() for a, b in taken):
                continue
            shown = m.group(0)
            if shown.islower() and shown.isalpha():
                continue                                      # "react to events", "spark of interest": ordinary words, not the product
            if _PROSE_SKIP_AFTER.match(text[m.end():]) and shown.isalpha():
                continue
            if _denied_in_prose(text, m.start(), vocabulary):
                continue
            taken.append((m.start(), m.end()))
            found.append((m.start(), shown, term))
    out: list[str] = []
    for _, shown, term in sorted(found):
        if ground_stack([term], _affirmed_card(card, term))[1] and shown not in out:
            out.append(shown)
    return out


def unsupported_claims(text: str, card: dict, vocabulary: list[str] | None = None) -> list[str]:
    """Every source finding the displayed text has against its own card: achieved standards, exclusivity claims and technologies.

    Args:
        text: The displayed paragraph or short form.
        card: That repository's saved card.
        vocabulary: Technology names to look for in prose (`tech_vocabulary`); none when omitted.

    Returns:
        The offending phrases (empty when the text is supported).
    """
    return (standard_outcome_claims(text, card) + scope_claims(text, card)
            + (prose_tech_claims(text, card, vocabulary) if vocabulary else []))


def from_card(c: dict, terms: frozenset[str] | None = None, vocabulary: list[str] | None = None) -> ProjectEntry | None:
    """A project entry straight from a repo card (fallback when the model can't choose): its own title and stack, and descriptions made only of the
    card's whole sentences (`faithful_variants`).

    Returns:
        The entry, or None when the card's complete sentences cannot fill a full description (the caller tries another candidate). A short
        description the card cannot fill is left empty.
    """
    full, short = faithful_variants(c, vocabulary, terms)
    if not full:
        return None
    return ProjectEntry(repo=c["full_name"], title=card_title(c), stack=card_terms(c)[:5] or c["tech_stack"][:5] or ["GitHub"],
                        paragraph=full, short=short or "")


def entry_from_pick(pick: "ProjectPick", card: dict, vocabulary: list[str] | None = None, terms: frozenset[str] | None = None,
                    log=print) -> ProjectEntry | None:
    """The stored entry for a writer's selection of source sentence numbers.

    Each description is composed from the numbers (`compose`) and must also pass the source checks. A selection that is invalid (missing,
    repeated or out of range numbers, too short, or flagged) is replaced for that variant by the faithful ranked selection of the same card, never by
    other text. The title is the writer's only when the card's own title supports it.

    Returns:
        The entry, or None when the card cannot fill a full description at all.
    """
    full_fallback, short_fallback = faithful_variants(card, vocabulary, terms)

    def build(ids, limit, floor, minimum, fallback, label):
        """The composed text for a selection, or the faithful fallback for it."""
        try:
            text = compose(card, ids, limit, minimum) if ids else ""
            if text and (flagged := unsupported_claims(text, card, vocabulary)):
                raise ValueError(f"{flagged}")
            if text:
                return text
        except ValueError as exc:
            log(f"projects: {card['full_name']}: {label} selection unusable ({str(exc)[:100]}); using its own whole sentences")
        return fallback or ""
    full = build(pick.paragraph_sentences, FULL_LIMIT, MIN_PARAGRAPH, FULL_FLOOR, full_fallback, "full")
    if not full:
        return None
    short = build(pick.short_sentences, SHORT_LIMIT, SHORT_WANTED, SHORT_FLOOR, short_fallback, "short") if (pick.short_sentences or short_fallback) else ""
    title = pick.title if title_supported(pick.title, card) else card_title(card)
    return ProjectEntry(repo=card["full_name"], title=title, stack=list(pick.stack), paragraph=full, short=short)


def apply_edits(state: dict, edits: list, known: dict | None = None, vocabulary: list[str] | None = None) -> list[str]:
    """Apply writer edits to a saved `projects.json` state in place, keeping only edits that are bound to the project's OWN saved evidence.

    An edit carries source sentence numbers (`paragraph_sentences`, `short_sentences`), or text. Numbers are composed with `compose`; text counts
    only when `bound_text` finds it made of that card's whole sentences (a copied source sentence is fine, a rewrite is not). A variant that is
    blank, unbound, out of bounds or without saved evidence keeps its old text. A title counts only when it is the owner's or card-supported.

    Args:
        state: Parsed `projects.json`; changed in place.
        edits: Objects with `project` (1-based, within the displayed projects) and optional `paragraph`, `short`, `title`,
            `paragraph_sentences`, `short_sentences`.
        known: `known_entries` of the owner template.
        vocabulary: Technology names to look for in prose.

    Returns:
        A note for each edit part that was refused, for the log.
    """
    refused, evidence, known = [], saved_evidence(state), known or {}
    if vocabulary is None:
        vocabulary = tech_vocabulary(evidence, state.get("ranked", []))
    for edit in edits:
        if not 1 <= edit.project <= state.get("kept", 0) or edit.project > len(state["ranked"]):
            continue
        entry = state["ranked"][edit.project - 1]
        card = evidence.get(str(entry.get("repo") or "").casefold())
        for field, ids_name, limit, minimum in (("paragraph", "paragraph_sentences", FULL_LIMIT, FULL_FLOOR), ("short", "short_sentences", SHORT_LIMIT, SHORT_FLOOR)):
            ids, text = list(getattr(edit, ids_name, None) or []), (getattr(edit, field, "") or "").strip()
            if not (ids or text):
                continue
            try:
                if card is None:
                    raise ValueError("no saved source evidence for this project")
                new = compose(card, ids, limit, minimum) if ids else bound_text(text, card)
                if not new or len(new) < minimum or len(new) > limit:
                    raise ValueError("not whole source sentences within the length bounds")
                if unsupported_claims(new, card, vocabulary):
                    raise ValueError("a source finding remains")
            except ValueError as exc:
                refused.append(f"project {edit.project} {field}: {exc}")
                continue
            entry[field] = new
        if title := (getattr(edit, "title", "") or "").strip():
            owner = known.get(str(entry.get("repo") or "").casefold())
            if owner:
                entry["title"] = owner[0]
            elif card is not None and title_supported(title, card):
                entry["title"] = title
            else:
                refused.append(f"project {edit.project} title: not words of its own card title")
    return refused


def choose(r: Resume, jd: JobDescription, model, user: str, token: str | None = None, rediscover: bool = True,
           log=print, *, cache=None, model_name="", feedback="") -> Chosen:
    """Refresh the repo cards (new/changed repos only), rank them for this job, pick and describe 2-4 projects.

    The model selects projects and numbered source sentences; the descriptions are composed from those sentences (`entry_from_pick`). A project whose
    selection is unusable is described by the faithful ranked selection of the same card; one whose card cannot fill a full description from whole
    sentences is skipped and another ranked candidate is tried. Fewer than MIN_PROJECTS usable projects raises RuntimeError.
    """
    try:
        cards = repo_cards.refresh(user, token, rediscover=rediscover, log=log)
    except Exception as exc:
        # Only template-linked repos or repos already inventoried for THIS
        # account may be reused. Never scan another account's cards as fallback.
        names = set(known_entries(r.tex))
        from jobagent.config import get_settings
        inventory = get_settings().state_dir / 'github' / f'{user.lower()}.json'
        try:
            names.update(item['full_name'] for item in json.loads(inventory.read_text()).get('repos', []))
        except (OSError, ValueError, KeyError):
            pass
        cards = []
        for name in sorted(names):
            try:
                card = repo_cards.load(name)
            except (OSError, ValueError):
                continue
            if card:
                cards.append(card)
        if not cards:
            raise RuntimeError('Project sources are unavailable and no account-bound saved repo cards exist') from exc
        log(f'projects: refresh unavailable ({type(exc).__name__}); reusing {len(cards)} account-bound saved repo cards')
    job_text = " ".join([jd.title or ""] + jd.responsibilities + jd.basic_qualifications +
                        jd.preferred_qualifications + jd.technical_skills)
    cands = repo_cards.rank(cards, job_text, log=log)
    # The template's own projects (the user's chosen showcase, e.g. SolBot) are always candidates, ranked or not
    known_repos = set(known_entries(r.tex))
    have = {c["full_name"].lower() for _, c in cands}
    cands += [(s, c) for s, c in repo_cards.rank(cards, job_text, k=len(cards), min_substance=0, log=log)
              if c["full_name"].lower() in known_repos and c["full_name"].lower() not in have]
    log("projects: top candidates: " + ", ".join(f"{c['full_name']} {s:.2f}" for s, c in cands))
    known = known_entries(r.tex)
    by_name = {c["full_name"].lower(): c for _, c in cands}
    terms, vocab = job_terms(jd), tech_vocabulary([c for _, c in cands])
    # Ground repository features in BOTH strategies: descriptions are the card's own sentences, selected by number.
    messages = build_prompt(cands, jd, known, grounded=True)
    if feedback:
        messages.append(HumanMessage("The previous document draft failed these checks. Correct any project-related "
                                     "findings using each repository's own evidence:\n" + feedback))
    out, chosen, skipped = None, [], set()
    for _ in range(3):
        try:
            if cache is not None:
                from .incremental_documents import invoke
                out = invoke(cache,'project-selection',model_name,model,Projects,messages,log=log)
            else:
                out = llm.structured_invoke(model, Projects, messages, log=log)
        except llm.LLMConfigError:
            raise
        except Exception as e:  # timeout / token limit: one more try
            log(f"projects: model error {type(e).__name__}; retrying")
            continue
        picks = list({p.repo.lower(): p for p in out.projects if p.repo.lower() in by_name}.values())
        chosen, bad = [], []
        for pick in picks:
            card = by_name[pick.repo.lower()]
            try:
                compose(card, pick.paragraph_sentences, FULL_LIMIT, FULL_FLOOR)
            except ValueError as exc:
                bad.append(f"{pick.repo}: {exc}")
            if entry := entry_from_pick(pick, card, vocab, terms, log=log):
                chosen.append(entry)
            else:
                skipped.add(pick.repo.lower())
        heads = [re.split(r"[:,]", e.title)[0].strip().lower() for e in chosen]
        if len(set(heads)) < len(heads):  # one project under two titles (e.g. "SolBot: ..." and "SolBot, ...")
            dup = sorted({h for h in heads if heads.count(h) > 1})
            log(f"projects: the same project twice ({dup}); asking again")
            messages = messages + [HumanMessage(f"{dup} appears twice: every entry must be a DIFFERENT repo, and each "
                                                "selection must use its own repo's SOURCE SENTENCES.")]
            continue
        # too short only when the card's own sentences could reach the wanted length (asking again cannot add facts a card lacks)
        thin = [e.title for e in chosen if len(e.paragraph) < MIN_PARAGRAPH
                and faithful_description(by_name[e.repo.lower()], FULL_LIMIT, MIN_PARAGRAPH, vocab, terms, minimum=MIN_PARAGRAPH)]
        if MIN_PROJECTS <= len(chosen) <= MAX_PROJECTS and not bad and not thin:
            break
        if (bad or thin) and MIN_PROJECTS <= len(chosen) <= MAX_PROJECTS:
            log(f"projects: unusable source sentence selection {bad or thin}; asking again")
            messages = messages + [HumanMessage(
                "These selections do not give a usable description: " + "; ".join(bad or [f"{t} is too short" for t in thin])
                + ". Return only existing SOURCE SENTENCE numbers of that same repo, distinct, totalling 300-420 characters (200 at least, 470 at most).")]
            continue
        messages = messages + [HumanMessage(f"Use {MIN_PROJECTS}-{MAX_PROJECTS} DIFFERENT repos, each exactly one "
                                            "of the CANDIDATE full_names.")]
    chosen = chosen[:MAX_PROJECTS]
    if len(chosen) < MIN_PROJECTS:  # the model failed or picked too few: top-ranked cards, in their own sentences
        log(f"projects: model gave {len(chosen)} usable project(s); filling from the top-ranked repo cards")
        have = {e.repo.lower() for e in chosen}
        for _, c in cands:
            if len(chosen) >= MAX_PROJECTS:
                break
            if c["full_name"].lower() not in have and (entry := from_card(c, terms, vocab)):
                chosen.append(entry)
    if len(chosen) < MIN_PROJECTS:
        raise RuntimeError(f"only {len(chosen)} candidate project(s) have complete source sentences to describe them "
                           f"(at least {MIN_PROJECTS} needed); no description was written without a source")
    years = {}
    for e in chosen:
        c = by_name[e.repo.lower()]
        e.repo = c["full_name"]
        e.stack, dropped = ground_stack(e.stack, c)       # the heading prints only technologies this repository's own card establishes
        if dropped:
            log(f"projects: {e.repo}: dropped from the stack, not shown by its own card: {dropped}")
        if e.repo.lower() in known:  # the user's own title (e.g. hackathon wins)
            e.title = known[e.repo.lower()][0]
        years[e.repo] = month_year(c.get("created_at") or c.get("pushed_at") or "") or \
            known.get(e.repo.lower(), ("", ""))[1]
    return Chosen(chosen, cands, years, out.notes if out else "top-ranked repo cards (model unavailable)", "\\newcommand{\\resumeProjectText}" in r.tex)


# --- the chosen projects, saved per job so later steps (the ATS loop) can re-fit the page --------------------------

def save_state(job_dir: Path, ranked: list[ProjectEntry], chosen: "Chosen") -> None:
    """All ranked projects (best first) and how many / which in short form made it onto the page."""
    cards = {c["full_name"].lower(): {"url": c["url"], "full_name": c["full_name"]} for _, c in chosen.candidates}
    by_name = {c["full_name"].lower(): c for _, c in chosen.candidates}
    (job_dir / "projects.json").write_text(json.dumps({
        "ranked": [e.model_dump() for e in ranked], "kept": len(chosen.entries), "short": sorted(chosen.short),
        "years": chosen.years, "macro": chosen.macro, "cards": cards,
        "evidence": {e.repo.lower(): evidence_snapshot(by_name[e.repo.lower()]) for e in ranked if e.repo.lower() in by_name}}, indent=1))


def evidence_snapshot(card: dict) -> dict:
    """The saved card as evidence for this one job: every text field, without the search vectors.

    Stored in the job's `projects.json`, so the claims checks read the same evidence on every later refresh, whatever the shared card cache holds then.

    Args:
        card: A saved repository card.

    Returns:
        A copy that can be written to JSON and used by `content_findings`.
    """
    return {k: v for k, v in card.items() if k not in ("embedding", "vector", "embeddings")}


def saved_evidence(state: dict) -> dict[str, dict]:
    """The evidence cards stored in a job's `projects.json`, by lower-case full name.

    A card counts only when it is a card of the repository it is stored under (its own `full_name`), so a copied or hand-edited entry cannot
    stand in for another project.

    Args:
        state: `projects.json`.

    Returns:
        The usable cards (empty for a file saved before evidence snapshots).
    """
    stored = state.get("evidence") if isinstance(state, dict) else None
    if not isinstance(stored, dict):
        return {}
    return {name.casefold(): card for name, card in stored.items()
            if isinstance(name, str) and isinstance(card, dict) and str(card.get("full_name") or "").casefold() == name.casefold()}


def missing_evidence(state: dict, cards: dict) -> list[str]:
    """One finding per displayed project whose own card is unavailable, so its source checks cannot run (never read as "nothing wrong").

    Args:
        state: `projects.json`.
        cards: The evidence cards that are available, by lower-case full name.

    Returns:
        The findings, in project order.
    """
    return [f"project {i} ({entry.get('repo')}) has no saved source evidence, so its claims cannot be checked against its own card; regenerate "
            "this resume so the evidence is saved with it"
            for i, entry in enumerate(state.get("ranked", [])[:state.get("kept", 0)], 1)
            if (entry.get("repo") or "").casefold() not in cards]


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


def project_layout_ok(pdf: Path) -> bool:
    """A one-page candidate must also have readable project descriptions."""
    from jobagent.tools import layout

    checked = layout.check(pdf)
    return checked.pages == 1 and not checked.one_line_projects


def fit(base_tex: str, job_dir: Path, compile_pdf, start: tuple[int, int] | None = None, *, accept=None):
    """Re-fit the Projects section of `base_tex` from projects.json in `layouts` order (full paragraphs first;
    a short form only adds an extra project). Returns (tex, pages, err, kept, short) or None when the job has no
    saved projects. `start` = (kept, n_short) to begin from (no more than the current page).
    `accept` can reject a freshly compiled one-page candidate so the next layout is tried.
    Exhausting the choices still returns the last draft for the caller's normal failure checks.
    """
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
        if err or (pages == 1 and (accept is None or accept())):
            return tex, pages, err, k, short
    return tex, pages, err, MIN_PROJECTS, frozenset(e.repo for e in ranked[:MIN_PROJECTS])


def update_state(job_dir: Path, kept: int, short: frozenset[str]) -> None:
    path = job_dir / "projects.json"
    st = json.loads(path.read_text())
    st["kept"], st["short"] = kept, sorted(short)
    path.write_text(json.dumps(st, indent=1))
