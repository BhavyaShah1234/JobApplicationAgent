"""Step 9: the resume's Projects section from the saved repo cards (tools/repo_cards).

Every repo of the resume's GitHub account (own + contributed) has a card written from its code. For a job, the
cards are ranked by embedding similarity to the JD, and the model picks 2-4 projects and writes each as one
unbulleted paragraph: the problem, the solution, the system built — about the whole project, also for shared repos.
Entries already in the template keep the template's title and year (the user's wording, e.g. a hackathon win).
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


def _single_quotes_balanced(sentence: str) -> bool:
    """Every opening single quote has a closing one. An apostrophe inside a word ("company's", "don't") or ending a plural possessive
    ("customers' data") is not a quote, so a normal possessive no longer makes a complete sentence look unfinished."""
    text = re.sub(r"(?<=\w)'(?=\w)", "", sentence)
    opens = len(re.findall(r"(?:^|[\s(\[\u201c\"])'(?=\w)", text))
    closes = len(re.findall(r"(?<=[\w.,!?)])'(?!\w)", text))
    return closes >= opens


def faithful_description(card: dict, limit: int, floor: int, vocabulary: list[str] | None = None) -> str | None:
    """A description made only of complete sentences of the card's own summary and results (never cut, never rejoined with new punctuation).

    Candidate sentences must end in terminal punctuation, have balanced brackets and quotes, not end in an abbreviation, hold no punctuation dash, and
    pass the same achieved-standard and exclusivity checks as generated text. They are taken in card order until `floor` characters are reached.

    Args:
        card: The project's saved card.
        limit: Longest allowed description.
        floor: Length at which the description is long enough.
        vocabulary: Technology names that the description must not name unless the card establishes them (`tech_vocabulary`).

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
                or sentence.count("(") != sentence.count(")") or sentence.count('"') % 2 or not _single_quotes_balanced(sentence)
                or sentence.lower().startswith("the readme")
                or unsupported_claims(sentence, card, vocabulary)):
            continue
        chosen.append(sentence)
        if len(text) >= floor:
            break
    text = " ".join(chosen)
    return text if len(text) >= max(60, floor // 2) else None


def recover_scope(entry: dict, card: dict, vocabulary: list[str] | None = None) -> list[str]:
    """Replace any displayed variant (full or short) of a saved project entry that has a source finding (an unsupported restriction, achieved
    standard or technology) with the card's own sentences, (`recover_stack` handles the printed stack).

    Args:
        entry: One `projects.json` ranked entry; changed in place.
        card: The project's saved card.
        vocabulary: Technology names to look for in prose (`tech_vocabulary`).

    Returns:
        The fields changed (empty when nothing was unsupported or no faithful text exists).
    """
    fixed = []
    for field, limit, floor in (("paragraph", MAX_PARAGRAPH - 10, MIN_PARAGRAPH), ("short", 230, 120)):
        if unsupported_claims(entry.get(field) or "", card, vocabulary) and (text := faithful_description(card, limit, floor, vocabulary)):
            entry[field] = text
            fixed.append(field)
    return fixed


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
