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
- title: short project name (<= 45 characters). If a KNOWN TITLE is given, use it exactly.
- stack: up to 5 technologies from the card's tech stack, most relevant to the posting first. Write "Python", never
  a version ("Python 3.12" -> "Python").
- paragraph: ONE unbulleted paragraph of exactly 3 sentences, 300-420 characters (it fills 3 lines on the page;
  a one-line description wastes the space):
  1. the problem: who had it and why it mattered;
  2. the system built: its components and how they connect, naming the stack;
  3. the wow factor: the result, scale or what was new (a number where possible: accuracy, latency, users,
     throughput, cost, a hackathon placing).
  Past tense or noun phrases; no "I". Use the posting's keywords throughout.
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
PROMPT += "\n" + SOURCE_POLICY


def build_prompt(cands: list[tuple[float, dict]], jd: JobDescription, known: dict[str, tuple[str, str]], *, grounded=False) -> list:
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
    prompt = PROMPT.format(n_min=MIN_PROJECTS, n_max=MAX_PROJECTS)
    if grounded:
        # A concrete invented example was copied verbatim into a different
        # repository during live validation. State the shape without supplying
        # transferable fake achievements or contradicting the source reviewer.
        start, end = prompt.index('EXAMPLE of the expected depth'), prompt.index('- Never use em dashes')
        prompt = prompt[:start] + prompt[end:]
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


def _sentences(text: str, limit: int) -> str:
    out = ""
    for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
        if len(out) + len(sentence) + 1 > limit:
            break
        out = f"{out} {sentence}".strip()
    return out or text[:limit].rsplit(" ", 1)[0] + "."


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


def _generic_outcomes(text: str) -> set[str]:
    """Affirmed generic standard outcomes, excluding mapping/checking capabilities, denials and plans."""
    outcomes = set()
    for match in GENERIC_STANDARD_CLAIM.finditer(_mask(text)):
        if _affirms(text, match):
            noun = match.group(1).lower().removesuffix('s')
            outcomes.add('compliance' if noun == 'conformance' else noun)
    return outcomes


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
        if not named and _generic_outcomes(sentence) - generic_supported:
            out.append(sentence.strip()[:160])                       # an unnamed standard ("meets industry standards"); a named one was judged above
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
    """Content words of a phrase, lower case, light plural/-ing/-ed stemming, a few common abbreviations unified."""
    words = []
    for w in re.findall(r"[a-z0-9][a-z0-9+#.-]*", text.lower()):
        w = _SCOPE_ALIAS.get(w.strip(".-"), w.strip(".-"))
        if not w or w in _SCOPE_STOP:
            continue
        for suffix in ("ing", "ed", "es", "s"):
            if len(w) > len(suffix) + 3 and w.endswith(suffix):
                w = w[:-len(suffix)]
                break
        words.append(w)
    return words


def _same_word(a: str, b: str) -> bool:
    """Equal after stemming, or sharing a 5-letter stem (document/documentation)."""
    return a == b or (min(len(a), len(b)) >= 5 and a[:5] == b[:5])


def _restrictions(sentence: str) -> list[tuple[str, list[str], list[str]]]:
    """Affirmed restrictions in a sentence: (printed phrase, words the restriction covers after the marker, words before a trailing marker).

    "not only X", "the only X" and "only three X" are not restrictions. A trailing marker ("... official documentation only") covers the words just
    before it, back to the last preposition or clause break.
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
        out.append((shown.rsplit(' ', 1)[0] if hi < len(sentence) and ' ' in shown else shown, _scope_words(after)[:6], _scope_words(before)[-4:]))
    return out


def scope_claims(text: str, card: dict) -> list[str]:
    """Generated restrictions (only/solely/exclusively) that the project's OWN card does not state.

    A restriction is supported when one card value states a restriction over the same content words, in either position ("only from official
    documentation", "official documentation only"). Documented fallbacks or alternatives, a named primary corpus and "not only X but Y" never support
    an exclusivity claim. Faithful rewording of a card's restriction (plurals, "docs" for "documentation") still matches. Ordinary qualitative
    benefits, numbers and sentences with no restriction word are not touched.

    Args:
        text: The displayed paragraph.
        card: The project's saved card.

    Returns:
        The unsupported phrases, in reading order.
    """
    supported = []
    for s in _card_strings(card):
        for _, after, before in _restrictions(s):
            supported.append(after[:10] + before)          # the card's own restricted words, either position
    claims = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        for phrase, after, before in _restrictions(sentence):
            wanted = after or before
            if wanted and not any(all(any(_same_word(w, c) for c in known) for w in wanted) for known in supported):
                claims.append(phrase)
    return claims


def faithful_description(card: dict, limit: int, floor: int) -> str | None:
    """A description made only of complete sentences of the card's own summary and results (never cut, never rejoined with new punctuation).

    Candidate sentences must end in terminal punctuation, have balanced brackets and quotes, not end in an abbreviation, hold no punctuation dash, and
    pass the same achieved-standard and exclusivity checks as generated text. They are taken in card order until `floor` characters are reached.

    Args:
        card: The project's saved card.
        limit: Longest allowed description.
        floor: Length at which the description is long enough.

    Returns:
        The description, or None when no qualifying complete sentence fits (the caller keeps the finding rather than printing a false text).
    """
    from jobagent.tools.tailor import DASH
    pool = [card.get("summary") or ""] + [x for x in card.get("results") or [] if isinstance(x, str)]
    chosen: list[str] = []
    for sentence in (x.strip() for block in pool for x in re.split(r"(?<=[.!?])\s+", block)):
        text = " ".join(chosen + [sentence])
        if (len(sentence) < 30 or len(text) > limit or DASH.search(sentence) or not re.search(r'[.!?]["\')\]]*$', sentence)
                or re.search(r"(?i)\b(?:e\.g|i\.e|etc|vs|approx)\.$", sentence)
                or sentence.count("(") != sentence.count(")") or sentence.count('"') % 2 or sentence.count("'") % 2
                or sentence.lower().startswith("the readme")
                or standard_outcome_claims(sentence, card) or scope_claims(sentence, card)):
            continue
        chosen.append(sentence)
        if len(text) >= floor:
            break
    text = " ".join(chosen)
    return text if len(text) >= max(60, floor // 2) else None


def recover_scope(entry: dict, card: dict) -> list[str]:
    """Replace any displayed variant (full or short) of a saved project entry that claims an unsupported restriction with the card's own sentences.

    Args:
        entry: One `projects.json` ranked entry; changed in place.
        card: The project's saved card.

    Returns:
        The variants replaced (empty when none had an unsupported restriction or no faithful text exists).
    """
    fixed = []
    for field, limit, floor in (("paragraph", MAX_PARAGRAPH - 10, MIN_PARAGRAPH), ("short", 230, 120)):
        if scope_claims(entry.get(field) or "", card) and (text := faithful_description(card, limit, floor)):
            entry[field] = text
            fixed.append(field)
    return fixed


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


def from_card(c: dict) -> ProjectEntry:
    """A project entry straight from a repo card (fallback when the model can't choose)."""
    title = re.split(r"\s+[—:|-]\s+", c["title"])[0][:45]
    return ProjectEntry(repo=c["full_name"], title=title, stack=card_terms(c)[:5] or c["tech_stack"][:5] or ["GitHub"],
                        paragraph=_sentences(c["summary"], 320), short=_sentences(c["summary"], 170))


def choose(r: Resume, jd: JobDescription, model, user: str, token: str | None = None, rediscover: bool = True,
           log=print, *, cache=None, model_name="", feedback="") -> Chosen:
    """Refresh the repo cards (new/changed repos only), rank them for this job, pick and write 2-4 projects."""
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
    # Ground repository features in BOTH strategies. The ungrounded instruction
    # to invent features contradicts the source validator and wastes repairs.
    messages = build_prompt(cands, jd, known, grounded=True)
    if feedback:
        messages.append(HumanMessage("The previous document draft failed these checks. Correct any project-related "
                                     "findings using each repository's own evidence:\n" + feedback))
    out, chosen = None, []
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
