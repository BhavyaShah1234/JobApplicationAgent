"""Step 12: fill a job application up to its review screen, never submitting it.

`jobagent apply <job_dir> <company>` opens the posting's application in the agent's browser (signing in to the
portal in the same session when it needs an account), then page by page:

1. reads every visible field (forms.read_page) and opens each combobox to learn its options;
2. one model call plans an answer for every field from the candidate's own files (data/applicant.yaml, the
   tailored resume, the JD, the company profile), with the source of each answer and a flag for anything the user
   should look at; a second call checks each answer really comes from the file it cites;
3. code fills each field (option fields matched to the page's own options) and reads every value back;
4. new fields that appeared (conditional questions) get another round; then Next / Continue / Save and Continue;
   a page that only offers Submit is the review screen: stop there.

The record (`application.json`, `application.md`, a screenshot per page) goes to the job folder for review
(step 13). Nothing invents personal facts: an answer the files don't hold is left empty and flagged.
"""

import json
import time
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import yaml
from rapidfuzz import fuzz
from langchain_core.messages import HumanMessage, SystemMessage
from playwright.sync_api import Page
from pydantic import BaseModel, Field as PField, create_model

from jobagent import llm
from jobagent.clock import now
from jobagent.tools import applicant, availability, application_address, forms, learned, referrals
from jobagent.tools import challenge, wizard
from jobagent.tools.browser import screenshot

DATA = Path("/app/data")
MAX_PAGES = 15
START = [re.compile(p, re.I) for p in (r"^apply manually$", r"^apply now$", r"^apply( for this (job|position|role))?( online)?$",
                                       r"^(i'?m interested|start (your |my )?application|begin application)$",
                                       r"^apply to (this )?job$",
                                       # a posting already started (signed in): Workday offers to continue it
                                       r"^(continue|resume)( my| your)?( job)? application$")]


class Answer(BaseModel):
    id: int
    why: str = PField("", max_length=200, description="One short line of reasoning first: which file / fact gives the "
                                                    "answer (for dates and ranges, compare the actual values)")
    value: str = PField(description="Text to type; for select/radio/combobox the option text exactly as listed; for "
                                    "checkboxes the chosen options joined with '; '; for a single checkbox 'checked' "
                                    "or ''; '' leaves the field as it is")
    source: Literal["applicant", "resume", "jd", "company", "written", "learned", "human", "lookup",
                    "already_filled", "none"]
    flag: str = PField("", max_length=240, description="Why the user should check this at review; '' when the "
                                                       "answer is plainly right")
    lookup: str = PField("", description="When the answer needs a lookup first (an APPLICANT entry's tools, e.g. "
                                         "a salary median or an office address): 'web: <query>', 'place: <address>' or "
                                         "'maps: <origin> | <destination>'; '' otherwise")
    ask_human: bool = PField(False, description="True when APPLICANT and the LEARNED entries shown don't answer "
                                                "this question: the candidate is asked now and the answer is kept")


class Plan(BaseModel):
    answers: list[Answer]
    add: list[int] = PField(default_factory=list, description="ADD BUTTON ids to click, one per extra entry needed "
                                                                "(e.g. a second work experience); [] when none")


class PlannerAudit(llm.BaseCallbackHandler):
    """Record real model calls without prompts, answers or credentials."""
    def __init__(self, path, model):
        self.path, self.model = path, model

    def event(self, event, run_id, **detail):
        with self.path.open('a') as handle:
            handle.write(json.dumps({'event': event, 'model': self.model, 'run_id': str(run_id),
                                     'at': datetime.now().isoformat(),**detail})+'\n')

    def on_chat_model_start(self, serialized, messages, **kwargs):
        params=kwargs.get('invocation_params') or {}
        method='function_calling' if params.get('tools') else (params.get('response_format') or {}).get('type','unknown') if isinstance(params.get('response_format'),dict) else 'unknown'
        self.event('start', kwargs.get('run_id'),method=method)

    def on_llm_end(self, response, **kwargs):
        self.event('complete', kwargs.get('run_id'))

    def on_llm_error(self, error, **kwargs):
        self.event('error', kwargs.get('run_id'),kind=llm.classify(error))


PROMPT = """You fill a job application form for the candidate, from the candidate's own files only. For each field
listed (id, kind, label, options, current value) give the answer to put in it.

APPLICANT is the candidate's answer file: entries of question, seen (wordings portals use), answer, instructions,
tools. LEARNED, when given, holds the few things the candidate taught on earlier forms that are closest to this
page's unanswered questions: a rule (instructions, company any) says how to answer questions like that one, apply
it to this form's options; a plain answer from the same company may be reused, one from another company only if the
question isn't specific to that company. An answer given
as a LIST of words (e.g. degree: ["Master of Science", "MS", "Master's degree"]) lists acceptable variants of one
answer in order of preference: use the first one the form offers (or the closest option to it); "Other" in such a
list is the last resort. A list of entries (degrees, work history) is one entry per item. A field marked
MATCHES is the APPLICANT entry for that question: use its answer, following its instructions. A field marked
RELATED lists APPLICANT entries close in meaning: use one when it asks the same thing in other words, ignore it when
it doesn't.

ANSWERS_FOR_THIS_APPLICATION, when given, is the candidate's own answer sheet for this job (they may have edited
it): its answer for a question wins over APPLICANT and everything else (source applicant). Where it leaves a choice
to the candidate or marks something to check, follow what it says; when it doesn't decide, use APPLICANT.

Think like the candidate filling in their own form, with common sense:
- Every portal rewords its questions. Match a field to the APPLICANT entry that asks the same thing in MEANING, not
  wording: "What is your desired compensation range?" is "Desired salary"; "Are you able to work 5 days on-site in
  Tempe, Arizona?" is "Able to work on site in the job's location?"; "Do you have a portfolio or website?" is yes when
  APPLICANT has a portfolio URL. Then use that entry's answer or follow its instructions and tools.
- Read the label together with its options and the job: the options often say what is asked, and a yes/no question
  follows from the candidate's facts (the candidate lives in the job's city or is willing to relocate -> can work
  on site there; holds a GPA of 4.0 -> the '3.8 - 4.0' range; never worked for the company -> 'No, never').
- An entry's answer true/false means Yes/No (pick the matching option). A question whose answer follows from
  APPLICANT, RESUME, JOB or COMPANY is never asked: ask_human only when none of them gives or implies it.
- Preferences and opinions require an explicit owner answer or an applicable owner instruction; otherwise ask.
- Each answer must answer ITS question: a person's name is never a salary, a URL is never a yes/no.

Rules:
- Application address fields and current location: use APPLICANT's job-specific
  "Current address" and "Current location (city)" entries consistently. These
  were prepared before opening the application. Correct prefilled address
  components that differ. Do not request additional address lookups.
- Other personal facts (name, contact, education, work authorization,
  sponsorship, citizenship, self-identification) ONLY from APPLICANT, exactly.
  Never invent a date of birth, SSN or another personal fact it doesn't hold:
  leave value '' with source none and ask_human true.
- An answer that is empty in APPLICANT but has instructions: follow the instructions (compute, write, or look it up).
  Empty with no instructions: ask_human true.
- Self-identification APPLICANT leaves empty: the "I don't wish to answer" / "decline" option (source applicant).
- A question APPLICANT doesn't cover (company-specific: teams, offices, essay prompts about the candidate's own
  choices) that you can't answer truthfully from the RESUME: ask_human true.
- An entry with `tools`, or a fact only a lookup can give (an office address, a salary median): set lookup; the
  result comes back to you as LOOKUP RESULTS.
- Email: APPLICANT's, unless the form already shows the portal account's email (keep it). Phone: its digits (the
  country / country-code option separately). Names: legal names; a "preferred name" field gets the preferred one.
- Sponsorship / authorization: as APPLICANT says. If the question's own wording counts the candidate's visa status
  (e.g. F-1 OPT / STEM OPT) as needing sponsorship, still answer from APPLICANT but flag "sponsorship wording
  includes OPT; you hold <visa_status>".
- Dates: DATE_FACTS says whether a degree has ended; the posting's own target dates never change the candidate's
  facts. For an option list of ranges, pick the range containing the true value.
- Work history: one entry per role in APPLICANT's "Work history" (taken from the tailored resume), all of them, in
  that order (title, company, location, from, to, description; "currently work here" only where current is true).
  Current company, skills, years of experience and internships: as APPLICANT's experience answers say. Education: one
  entry per degree in APPLICANT. When more entries are needed than the form shows, put the matching ADD BUTTON id in
  `add` once per missing entry (3 roles, 1 entry shown -> [id, id]) and leave those entries for the next round.
  Never add an entry APPLICANT has nothing for (no certifications -> add none).
- A field asking for a date (a start date, a date picker "Pick date..."): a date in the form's format (default
  MM/DD/YYYY), computed from DATE_FACTS (e.g. "the coming Monday"), never words like "immediately".
- The job's own location is JOB_LOCATION (the posting's words resolved to a city): use it for "which location
  are you applying for" questions; it is never the candidate's own address.
- Location / office choices (checkboxes): APPLICANT's "Which locations would you work in?" (every US location
  offered), not only the job's own office.
- date_parts fields: the value in the field's format (MM/YYYY -> '08/2024', YYYY -> '2024'). A role shown as Present:
  tick its "I currently work here" box ('checked') and leave its To date ''.
- Open questions (why this company, behavioural, technical): a truthful answer from the RESUME and COMPANY profile,
  specific and plain, within the length asked (default 80-150 words), no cliches; source written.
- Choice fields: the value must be one of the listed options, copied exactly; when the true value isn't offered (a
  field of study, a degree name), the closest option, flagged "closest option to <true value>". A combobox whose
  options are categories ("Job Board", "<Company> Source"): 'Category > what you mean'. A combobox listed without
  options (or with a cut-off list) is a search box: give the answer's variants in preference order joined with
  ' | ' (e.g. 'Artificial Intelligence and Robotics | Robotics | Computer Science'); the first the site offers is
  chosen. A skills search box: '; '-separated values.
- Sourcing questions ("How did you hear about us/this position?" and equivalents):
  choose the offered employee-referral option, including an equivalent such as
  "Employee Referral" or "Referred by an employee". A plain text response is
  "Referred by employee".
- If the sourcing question requires employee details, use REFERRAL's matching
  CSV contact. Separate employee-name and employee-email fields receive those
  individual values. A combined response includes the name and email.
  Never invent missing names, emails, or employee IDs.
- A "Cover letter" text box (not an upload): the COVER_LETTER text, as it is (from its greeting to the signature).
- File fields: value 'resume' for a resume / CV upload, 'cover_letter' for a cover letter upload, '' otherwise.
- A field with a current value that is already right: repeat it with source already_filled. Wrong: correct it.
- Answer every listed field id exactly once."""


JD_FULL_LIMIT = 40_000        # a posting up to this size is given to the planner and the checker WHOLE
JD_HEAD = 15_000


def posting_text(job_dir: Path) -> tuple[str, str]:
    """(the authoritative posting text, where it came from). The scraped source (`jd.txt`) is preferred: the generated summary
    (`jd.md`) and the structured `jd.json` are lossy, and a requirement can sit in a header line or deep in a long posting (F30)."""
    for name in ("jd.txt", "jd.md"):
        p = job_dir / name
        if p.exists() and p.read_text().strip():
            return p.read_text(), name
    return "", "none"


def jd_for_planning(text: str, source: str) -> str:
    """The posting as the planner sees it, with its provenance. Whole when it fits; otherwise the beginning plus an explicit
    marker (never a silent prefix) - the passages relevant to a question are added by `jd_passages`."""
    if not text:
        return "(no posting text was saved)"
    if len(text) <= JD_FULL_LIMIT:
        return f"[source: {source}, {len(text)} characters, complete]\n{text}"
    return (f"[source: {source}, {len(text)} characters; the first {JD_HEAD} are shown, the rest is searched for each question "
            f"under JD_PASSAGES]\n{text[:JD_HEAD]}")


def jd_passages(text: str, fields: list[forms.Field], limit: int = 6000) -> str:
    """For a posting too long to show whole: the lines that share meaningful words with the form's questions (line numbers kept)."""
    if len(text) <= JD_FULL_LIMIT:
        return ""
    words = {w for f in fields for w in re.findall(r"[a-z]{4,}", f"{f.label} {f.help}".lower())} - {"please", "select", "your", "with", "that", "this", "have", "will"}
    out, size = [], 0
    for n, line in enumerate(text.splitlines(), 1):
        if len(line) > 3 and words & set(re.findall(r"[a-z]{4,}", line.lower())):
            row = f"line {n}: {line.strip()[:300]}"
            if size + len(row) > limit:
                break
            out.append(row)
            size += len(row)
    return "\n".join(out)


def context(job_dir: Path, company: str) -> dict:
    """The candidate's files, as the planner sees them."""
    resume = ""
    if (job_dir / "resume.pdf").exists():
        resume = subprocess.run(["pdftotext", "-layout", str(job_dir / "resume.pdf"), "-"], capture_output=True,
                                text=True).stdout
    jd = json.loads((job_dir / "jd.json").read_text()) if (job_dir / "jd.json").exists() else {}
    if jd and "locations_resolved" not in jd:  # "Corporate Headquarters" -> the city it means (once per job)
        from jobagent.tools import places
        try:
            places.resolve_job(job_dir, company, log=lambda m: None)
            jd = json.loads((job_dir / "jd.json").read_text())
        except Exception:
            pass
    comp = Path(f"/app/state/companies/{company}.md")
    tex = (job_dir / "resume.tex").read_text() if (job_dir / "resume.tex").exists() else ""
    data = applicant.with_resume(applicant.load(), applicant.resume_experience(tex))
    forms.set_approved_aliases(applicant.identity_aliases(data))        # the only forms of a school / employer treated as the same one (F29)
    return {
        # the answer file, its experience answers taken from this job's tailored resume
        "APPLICANT": yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=120),
        "RESUME": resume[:6000],
        "JD": jd_for_planning(*posting_text(job_dir)),
        "_jd_text": posting_text(job_dir)[0],
        "COMPANY": (comp.read_text() if comp.exists() else "")[:3000],
        "COVER_LETTER": cover_letter_text(job_dir),
        # the candidate's own answers for THIS application (application/answers.md, written or edited by them)
        "ANSWERS_FOR_THIS_APPLICATION": (job_dir / "application" / "answers.md").read_text()[:6000]
        if (job_dir / "application" / "answers.md").exists() else "(none)",
        "REFERRAL": referrals.contact_for_company(
            jd.get("company") or company
        ),
        "JOB_LOCATION": "; ".join(f"{x['as_written']} = " + (", ".join(y for y in (x["city"], x["state"], x["country"])
                                                                        if y) or "unresolved")
                                  for x in jd.get("locations_resolved") or []) or "(not given)",
        "TODAY": now().strftime("%Y-%m-%d"),
        "DATE_FACTS": date_facts(applicant.degrees()),
        "_jd": jd,
        "_applicant": data,
    }


def _has_data(ctx: dict, section: str) -> bool:
    """APPLICANT holds entries for this form section (certifications, languages, ...)."""
    key = re.sub(r"[^a-z]", "", section.lower())
    for _, e in applicant.entries(yaml.safe_load(ctx.get("APPLICANT") or "") or {}):
        q = re.sub(r"[^a-z]", "", e["question"].lower())
        if q[:6] and q[:6] in key and e.get("answer"):
            return True
    return False


SECTION_ENTRY = [(re.compile(r"(?i)work experience|employment history|experience \d"), "Work history"),
                 (re.compile(r"(?i)^education"), "Degrees")]


def _match(label: str, wording: str) -> float:
    """How well a form label asks an entry's question: a short wording ("City", "Company") must be the label itself;
    a long one may be worded a little differently."""
    if len(wording.split()) <= 2:
        return fuzz.ratio(label, wording)
    ratio = len(wording.split()) / max(1, len(label.split()))
    whole = fuzz.token_set_ratio(label, wording) if 0.5 <= ratio <= 2 else fuzz.token_sort_ratio(label, wording)
    # a long label that contains the wording ("..., can you provide documentation establishing your identity ...")
    part = fuzz.partial_ratio(label, wording) if len(wording.split()) >= 4 and len(label) > len(wording) else 0
    return max(whole, part)


def cover_letter_text(job_dir: Path) -> str:
    """The job's cover letter as plain text (for a form's "Cover letter" text box)."""
    pdf = job_dir / "cover_letter.pdf"
    if not pdf.exists():
        return "(none)"
    text = subprocess.run(["pdftotext", str(pdf), "-"], capture_output=True, text=True).stdout
    return re.sub(r"\n{3,}", "\n\n", text).strip()[:4000]


def match_entries(fields: list[forms.Field], company: str = "", data: dict | None = None) -> dict[int, tuple]:
    """For each field, (section, entry) of the APPLICANT entry whose question it asks: fuzzy match of the label on the
    entry's question and the wordings seen on portals. Fields of a work-experience / education entry belong to
    "Work history" / "Degrees"."""
    pairs = applicant.entries(data)
    bank = [(sec, e, [e["question"], *(e.get("seen") or [])]) for sec, e in pairs]
    by_q = {e["question"]: (sec, e) for sec, e in pairs}
    out = {}
    for f in fields:
        label = re.sub(r"\((mm|dd|yyyy)[/a-z]*\)|[*✱]", "", f.label.lower())[:200].strip(" :?")
        if company:
            label = label.replace(company.lower(), "<company>")
        best, best_len = (0, "", {}), -10**6
        for pat, q in SECTION_ENTRY:
            if pat.search(f.section) and q in by_q:
                best = (100, *by_q[q])
                break
        else:
            for sec, e, qs in bank:
                for q in qs:
                    sc = _match(label, q.lower().strip(" :?"))
                    # on a tie the wording closest to the label itself wins ("First Name": legal, not preferred)
                    if (sc, -abs(len(q) - len(label))) > (best[0], best_len):
                        best, best_len = (sc, sec, e), -abs(len(q) - len(label))
        if best[0] >= 85:
            out[f.id] = (best[1], best[2])
    return out


def _entry_text(sec: str, e: dict) -> str:
    return (f"{sec}: {e['question']!r} answer={e.get('answer')!r}"
            + (f" instructions={e.get('instructions')!r}" if e.get("instructions") else "")
            + (f" tools={e.get('tools')}" if e.get("tools") else "")
            + (f" company={e.get('company')}" if e.get("company") else ""))


RELATED_MIN, RELATED_K = 0.70, 2


def related_entries(fields: list[forms.Field], data: dict | None, skip: set[int] = frozenset(),
                    log=None) -> dict[int, list[tuple[float, str, dict]]]:
    """APPLICANT entries close in MEANING to each field's question (embeddings of the entry's question and seen
    wordings): "What is your desired compensation range?" -> "Desired salary" (0.85). Portals reword every question,
    so wording alone misses these. Only hints for the planner (it decides whether the entry asks the same thing):
    code never fills a value from a match this loose."""
    todo = [f for f in fields if f.id not in skip and f.kind != "file" and len(f.label.split()) >= 3]
    if not todo:
        return {}
    pairs = applicant.entries(data)
    bank = [(sec, e, t) for sec, e in pairs for t in [e["question"], *(e.get("seen") or [])]]
    try:
        vecs = learned._vectors([learned._clean(f.label) for f in todo] + [learned._clean(t) for _, _, t in bank])
    except Exception as ex:
        if log:
            log(f"apply: related-entry lookup failed ({type(ex).__name__})")
        return {}
    if not vecs:
        return {}
    qv, bv = vecs[:len(todo)], vecs[len(todo):]
    out = {}
    for f, v in zip(todo, qv):
        best: dict[str, tuple[float, str, dict]] = {}
        for (sec, e, _), w in zip(bank, bv):
            s = learned._cos(v, w)
            if s > best.get(e["question"], (0,))[0]:
                best[e["question"]] = (s, sec, e)
        top = sorted((x for x in best.values() if x[0] >= RELATED_MIN), key=lambda x: -x[0])[:RELATED_K]
        if top:
            out[f.id] = top
    return out


def sheet_rows(text: str) -> list[tuple[str, str]]:
    """(question, answer) rows of an answers.md table: | # | Question | Answer |."""
    rows = []
    for line in (text or "").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 3 and cells[0].isdigit() and cells[1] and cells[2]:
            rows.append((re.sub(r"[⚠*]", "", cells[1]).strip(), cells[2]))
    return rows


def sheet_hints(fields: list[forms.Field], text: str) -> dict[int, str]:
    """Each field's row in the candidate's answer sheet for this job (wording match on the question)."""
    rows, out = sheet_rows(text), {}
    for f in fields:
        label = re.sub(r"[*✱]", "", f.label).strip()
        best = max(((fuzz.token_set_ratio(label.lower(), q.lower()), q, a) for q, a in rows), default=(0, "", ""))
        if best[0] >= 80:
            out[f.id] = f"YOUR SHEET (wins over APPLICANT) {best[1]!r}: {best[2]}"
    return out


def hints(fields: list[forms.Field], company: str = "", data: dict | None = None, log=None,
          related: bool = True) -> dict[int, str]:
    """The APPLICANT entry of each field, as the planner sees it next to the field: MATCHES (same wording) or
    RELATED (close in meaning, the planner judges; left out with related=False)."""
    out = {}
    matched = match_entries(fields, company, data)
    for fid, (sec, e) in matched.items():
        out[fid] = "MATCHES " + _entry_text(sec, e)
    for fid, top in (related_entries(fields, data, set(matched), log) if related else {}).items():
        out[fid] = "\n    ".join(f"RELATED ({s:.2f}, by meaning; may not be the same question) {_entry_text(sec, e)}"
                                  for s, sec, e in top)
    return out


EDU_PART = [("school", re.compile(r"(?i)school|university|college|institution")),
            ("degree", re.compile(r"(?i)\bdegree\b(?!.*major)")),
            ("field", re.compile(r"(?i)field of study|major|discipline|area of study"))]


def _as_list(v) -> list[str]:
    return [str(x) for x in v] if isinstance(v, list) else ([str(v)] if v not in (None, "") else [])


def apply_variants(todo: list[forms.Field], answers: dict, ctx: dict, log) -> None:
    """An answer written as a list in applicant.yaml is that answer's accepted wordings, in the candidate's order:
    code puts them in the field (a text box gets the first; an option list or search box tries each in turn, the
    first the form offers wins). Not where the entry gives instructions: then the planner's answer stands."""
    data = ctx.get("_applicant") or {}
    degrees = applicant.degrees(data)
    matched = match_entries(todo, ctx.get("_jd", {}).get("company", ""), data)
    # which degree each "Education N" block is: by the school the plan put there, else by position
    by_section = {}
    for f in todo:
        m = re.search(r"(?i)education\s*(\d+)", f.section)
        if m and EDU_PART[0][1].search(f.label):
            val = (answers.get(f.id).value if answers.get(f.id) else "") or f.value
            hit = next((i for i, d in enumerate(degrees)
                        if any(forms.close_option(val, [s]) or forms._norm(s) in forms._norm(val)
                               for s in _as_list(d.get("school")) if val)), None)
            by_section[f.section] = hit if hit is not None else int(m.group(1)) - 1
    for f in todo:
        variants, entry = [], matched.get(f.id, ("", {}))[1]
        if entry.get("question") == "Degrees" and degrees:
            part = next((name for name, pat in EDU_PART if pat.search(f.label)), None)
            idx = by_section.get(f.section, 0)
            d = degrees[idx] if 0 <= idx < len(degrees) else None
            if d and part and not (part == "field" and d.get("instructions")):
                variants = _as_list(d.get(part))
        elif isinstance(entry.get("answer"), list) and all(isinstance(x, str) for x in entry["answer"]) \
                and not entry.get("instructions"):
            variants = entry["answer"]
        if len(variants) < 2 or f.kind == "file":
            continue
        value = variants[0] if f.kind in ("text", "textarea", "email", "url") else " | ".join(variants)
        a = answers.get(f.id)
        if a is None or a.value != value:
            log(f"  [{f.id}] {f.label[:40]!r}: from applicant.yaml's list -> {value[:80]!r}")
            answers[f.id] = Answer(id=f.id, why="the answer's accepted wordings in applicant.yaml, in order",
                                   value=value, source="applicant", flag=a.flag if a else "")


def date_facts(degrees: list[dict]) -> str:
    """Dates worked out by code, so the model compares facts instead of guessing (a graduation already past)."""
    lines, today = [], now().date()
    for ed in degrees:
        try:
            end = datetime.strptime(str(ed.get("end", "")).strip(), "%B %Y").date()
        except ValueError:
            continue
        state = "graduated" if end <= today else "expected"
        lines.append(f"{ed.get('degree')} ({ed.get('school')}): ends {end:%B %Y} = {end:%Y-%m}, {state} "
                     f"({'before' if end <= today else 'after'} today {today:%Y-%m-%d})")
    monday = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
    lines.append(f"today: {today:%Y-%m-%d} ({today:%m/%d/%Y}, {today:%A}); coming Monday: {monday:%Y-%m-%d} "
                 f"({monday:%m/%d/%Y})")
    return "\n".join(lines)


def plan(fields: list[forms.Field], ctx: dict, model, log=print, feedback: str = "", adds: list[dict] = (),
         filled: list[forms.Field] = ()) -> tuple[dict[int, Answer], list[int]]:
    files = "\n\n".join(f"== {k} ==\n{v}" for k, v in ctx.items() if not k.startswith("_"))
    h = hints(fields, ctx.get("_jd", {}).get("company", ""), ctx.get("_applicant"), log)
    for fid, s in sheet_hints(fields, ctx.get("ANSWERS_FOR_THIS_APPLICATION", "")).items():
        h[fid] = s + ("\n    " + h[fid] if fid in h else "")
    from jobagent.filling.routing import route,word_limits
    from jobagent.filling.facts import compile_facts
    indexed=compile_facts(ctx.get('_applicant') or {},ctx.get('_jd_text',''),ctx.get('TODAY',''))
    for field in fields:
        r=route(field,indexed);limits=word_limits(field,[x for x in indexed if x.id in r.fact_ids])
        if limits:h[field.id]=h.get(field.id,'')+f' REQUIRED WORD LIMITS: {limits[0]}-{limits[1]} words.'
    msgs = [SystemMessage(PROMPT),
            HumanMessage(f"{files}\n\n"
                         + ("== ALREADY ON THIS PAGE (planned earlier) ==\n" + "\n".join(f.brief() for f in filled)
                            + "\n\n" if filled else "")
                         + "== FORM FIELDS ==\n" + "\n".join(f.brief() + (f"\n    {h[f.id]}" if f.id in h else "")
                                                             for f in fields)
                         + ("\n\n== ADD BUTTONS ==\n" + "\n".join(f"[{a['id']}] {a['text']} (section: {a['section']})"
                                                                for a in adds) if adds else "")
                         + (f"\n\n== FEEDBACK ==\n{feedback}" if feedback else ""))]
    schema = create_model('ApplicationFieldPlan',
        add=(list[int], PField(default_factory=list)),
        **{f'field_{f.id}': (Answer, PField(..., description=f'Answer ONLY field [{f.id}]: {f.label}; kind={f.kind}'))
           for f in fields})
    out = llm.structured_invoke(model, schema, msgs, first="function_calling", log=log)
    answers = {}
    for f in fields:
        answer = getattr(out, f'field_{f.id}').model_copy(update={'id':f.id})
        if answer.source == 'already_filled' and not (f.value and forms.same_value(answer.value, f.value, f.kind,f.label)):
            answer.source = 'applicant' if in_applicant(answer.value, ctx.get('_applicant') or {}) else 'written'
        answers[f.id] = answer
    from jobagent.filling.routing import route,word_limits
    from jobagent.filling.facts import compile_facts
    facts=compile_facts(ctx.get('_applicant') or {},ctx.get('_jd_text',''),ctx.get('TODAY',''))
    repairs=[]
    for field in fields:
        a=answers[field.id];r=route(field,facts);bound=[x for x in facts if x.id in r.fact_ids]
        limits=word_limits(field,bound)
        if field.kind=='textarea' and a.value and limits and not limits[0]<=len(a.value.split())<=limits[1]:
            repairs.append({'field_id':field.id,'draft':a.value,'word_limits':limits})
    if repairs:
        from jobagent.filling.planned import Drafts
        fixed=llm.structured_invoke(model,Drafts,[SystemMessage(
            'Repair each supplied draft to its exact word_limits using only the supplied resume/company evidence. '
            'Do not invent personal history. Return one draft per field, source written, with an evidence quote.'),
            HumanMessage(json.dumps({'repairs':repairs,'resume':ctx.get('RESUME',''),'company':ctx.get('COMPANY','')}))],first='function_calling',log=log)
        for item in repairs:
            value=next((x.value for x in fixed.drafts if x.field_id==item['field_id']),None)
            low,high=item['word_limits'];a=answers[item['field_id']]
            if value and low<=len(value.split())<=high:answers[item['field_id']]=a.model_copy(update={'value':value})
            else:answers[item['field_id']]=a.model_copy(update={'value':'','flag':'writer did not satisfy word limits'})
    return answers, out.add


class Check(BaseModel):
    id: int
    supported: bool = PField(description="The cited file really states this (or the written text is truthful to the "
                                         "RESUME); false if it was assumed")
    value: str = PField("", description="Corrected value when not supported and the files do give the answer "
                                        "(an option copied exactly); '' otherwise")
    ask_human: bool = PField(False, description="Not supported and no file gives it: ask the candidate")


class Checks(BaseModel):
    checks: list[Check]


CHECK_PROMPT = """You check a planned job application against the candidate's files. For each planned answer
(field, value, cited source, reasoning) decide whether the cited file REALLY states it. An answer is unsupported when
the file is silent and the planner assumed it (a language not listed, an eligibility, a consent the candidate never
gave, an option chosen while the APPLICANT entry for that question says another that is offered). For unsupported
answers give the value the files do support, or ask_human when none does. Written answers (source written) are
supported when true to the RESUME. Following an APPLICANT entry's instructions is support, EXCEPT that an instruction which
points at the posting ("use the JD's percentage", "match the location") is satisfied only when the JD text given to you states
that fact: check the answer against the JD, and where the JD states a value the answer contradicts, correct it to the option that
means the JD's value (a numeric range by meaning: "30% or less" is not "50% or more"); where the JD is silent the instruction's
own fallback applies. A school, employer or person must be the actual one the files name, never a similar-sounding neighbour.
These rules are also support:
choosing "I don't wish to answer" for self-identification APPLICANT leaves
empty. An answer listed as a list of words gives variants of one answer: any of
them is supported. APPLICANT wins over the RESUME where they disagree (never swap one for the other: that is flagged, not
corrected). A corrected value for a choice field must be one of its listed options, copied exactly. Return one check
per planned answer."""


def _leaves(x) -> list[str]:
    if isinstance(x, dict):
        return [v for k, y in x.items() if k not in ("instructions", "seen", "question") for v in _leaves(y)]
    if isinstance(x, list):
        return [v for y in x for v in _leaves(y)]
    if isinstance(x, bool):
        return ["Yes" if x else "No"]
    return [str(x)] if x not in (None, "") else []


def in_applicant(value: str, data: dict) -> bool:
    """The answer file holds this value: as an answer or one of an answer's listed variants (option wording aside:
    "Masters" for "Master's degree", "Yes" for true)."""
    vals = [v for _, e in applicant.entries(data) for v in _leaves(e.get("answer"))]
    want = value.rsplit(" > ", 1)[-1]
    nums = [float(v) for v in vals if re.fullmatch(r"\d+(\.\d+)?", v)]
    return any(forms.match_option(v, [want], cutoff=88)[0] is not None for v in vals) or \
        any(forms.in_range(want, n) for n in nums)


def check(fields: list[forms.Field], answers: dict[int, Answer], ctx: dict, model, log) -> None:
    """A second look at the plan: answers a file doesn't really support are corrected or go to the candidate."""
    from jobagent.filling.facts import owner_only,compile_facts,resolve as resolve_fact
    for f in fields:
        a=answers.get(f.id)
        if a and owner_only(f) and a.source in ('resume','jd','company','written','already_filled','none'):
            answers[f.id]=a.model_copy(update={'value':'','ask_human':True,
                'flag':(a.flag+' explicit owner answer required for preference, consent or sensitive history').strip()})
    rows = [f"{f.brief()}\n    PLANNED value={a.value!r} source={a.source} why={a.why!r}"
            for f in fields if (a := answers.get(f.id)) and a.value and a.source not in ("human", "learned", "lookup")
            and f.kind != "file"]
    if not rows:
        return
    # the checker sees the posting too (F30): an instruction like "use the JD's percentage" cannot be validated without it
    extra = jd_passages(ctx.get("_jd_text", ""), fields)
    files = "\n\n".join(f"== {k} ==\n{v}" for k, v in ctx.items() if not k.startswith("_")) + \
        (f"\n\n== JD_PASSAGES ==\n{extra}" if extra else "")
    try:
        checked = [f for f in fields if (a := answers.get(f.id)) and a.value
                   and a.source not in ('human','learned','lookup') and f.kind != 'file']
        schema = create_model('ApplicationFieldChecks', **{
            f'field_{f.id}': (Check, PField(..., description=f'Check ONLY field [{f.id}]: {f.label}')) for f in checked})
        out = llm.structured_invoke(model, schema, [SystemMessage(CHECK_PROMPT), HumanMessage(
            files + "\n\n== PLANNED ANSWERS ==\n" + "\n".join(rows))], first="function_calling", log=log)
    except Exception as e:
        log(f"apply: plan check failed ({type(e).__name__}); unvalidated answers require review")
        for f in checked:
            a=answers[f.id]
            answers[f.id]=a.model_copy(update={'value':'','ask_human':True,
                'flag':(a.flag+' source checker unavailable').strip()})
        return
    data = ctx.get("_applicant") or {}
    source_facts=compile_facts(data,ctx.get('_jd_text',''),ctx.get('TODAY',''))
    h = hints(fields, ctx.get("_jd", {}).get("company", ""), data, related=False)  # the file must really say it
    for c in [getattr(out, f'field_{f.id}').model_copy(update={'id':f.id}) for f in checked]:
        a = answers.get(c.id)
        if a is None or c.supported:
            continue
        f = next((x for x in fields if x.id == c.id), None)
        instr = h.get(c.id, "")
        # instructions are support only for choices that do not depend on facts outside the applicant's file: an instruction that
        # points at the posting ("use the JD's percentage"), or a question about WHICH institution, must be validated against that
        # source, not waved through because an instruction exists (F29/F30)
        bound=resolve_fact(f,source_facts) if f else None
        if a.source == "applicant" and bound and bound.value is not None and forms.same_value(bound.value,a.value,f.kind,f.label):
            continue  # the answer to THIS question is explicit, unconditional and rendered correctly
        jd_rule = "instructions=" in instr and re.search(r"(?i)\b(jd|job description|posting|listing)\b", instr) \
            and not (f and forms.is_identity(f.label))
        if a.source == "applicant" and jd_rule and c.value and not c.ask_human \
                and f and f.options and forms.strict_option(c.value, f.options) is not None:
            # an instruction that points at the posting was followed with a value the posting contradicts: the checker read the
            # posting and names the option that means its value
            log(f"  check: [{c.id}] {a.value[:40]!r} -> {c.value[:40]!r} (the posting says otherwise)")
            answers[c.id] = a.model_copy(update={"value": c.value, "flag": (a.flag + " corrected by the check against the posting").strip()})
            continue
        if a.source == "applicant":
            # credited to the candidate's own file but not in it (or in conflict with the resume): theirs to say
            log(f"  check: [{c.id}] {a.value[:40]!r} isn't in applicant.yaml as planned; asking the candidate")
            answers[c.id] = a.model_copy(update={"ask_human": True, "value": "",
                                                 "flag": (a.flag + f" planned {a.value[:40]!r}").strip()})
            continue
        if c.value and f and f.options and forms.strict_option(c.value, f.options) is None:
            # a "correction" that isn't an offered option is no fix
            answers[c.id] = a.model_copy(update={"value":"", "ask_human":True,
                "flag": (a.flag + f" check suggests unavailable {c.value[:60]!r}").strip()})
            continue
        if c.value and not c.ask_human and c.value.strip().lower() not in ("ask_human", "ask human", "none"):
            log(f"  check: [{c.id}] {a.value[:40]!r} -> {c.value[:40]!r}")
            answers[c.id] = a.model_copy(update={"value": c.value, "flag": (a.flag + " corrected by the check").strip()})
        else:
            log(f"  check: [{c.id}] {a.value[:40]!r} is assumed; asking the candidate")
            answers[c.id] = a.model_copy(update={"ask_human": True, "value": ""})


def _tie_break(model, log):
    class Pick(BaseModel):
        index: int = PField(description="Index of the option that means the answer, -1 if none does")

    def pick(question: str, answer: str, options: list[str]) -> str | None:
        listing = "\n".join(f"{i}: {o}" for i, o in enumerate(options[:150]))
        try:
            p = llm.structured_invoke(model, Pick, [HumanMessage(
                f"Form question: {question}\nIntended answer: {answer}\nOptions:\n{listing}\n"
                "Which option means the intended answer? -1 if none.")], first="function_calling", log=log)
        except Exception:
            return None
        return options[p.index] if 0 <= p.index < len(options) else None
    return pick


ENTRY_OK = re.compile(r"(?i)work experience|employment|education|^experience")
ERRORS_HEAD = re.compile(r"(?i)^errors? found|^\d+ errors?|^error")
COVER_WORD = re.compile(r"(?i)cover")
RESUME_WORD = re.compile(r"(?i)resume|r\u00e9sum\u00e9|\bcv\b|curriculum")


def upload_role(label: str) -> str:
    """'resume', 'cover_letter' or '' for an upload, from its OWN caption: the label's parts in order (the explicit caption first,
    then a fallback container text, then the input's id or name). A part that names exactly one role decides; a part naming both
    ("Resume and Cover Letter") or neither is passed over; if no part decides the role is left to the planner, never guessed. Only a
    generic "upload a file" is taken as the resume. Surrounding text can no longer turn a Resume caption into a cover letter."""
    parts = [p for p in re.split(r"\s/\s", label or "") if p.strip()]
    for part in parts:
        cover, resume = bool(COVER_WORD.search(part)), bool(RESUME_WORD.search(part))
        if cover and not resume:
            return "cover_letter"
        if resume and not cover:
            return "resume"
    return "resume" if re.search(r"(?i)upload a file", label or "") else ""


NOT_POSTAL = re.compile(r"(?i)e-?mail|\burl\b|web\s?site|\bip address\b|\bmac address\b|\blinkedin\b|\bgithub\b|\bportfolio\b")
POSTAL_CODE_Q = re.compile(r"(?i)\b(zip|postal|post\s?code|pin\s?code)\b")
PHONE_Q = re.compile(r"(?i)\b(phone|telephone|mobile|cell|fax|tel)\b")


def not_postal(label: str, kind: str = "") -> bool:
    """Is this field clearly NOT a postal-address part? The ONE classifier behind the address helpers, the selected-address
    enforcement and the address question to the candidate, so they cannot disagree. Intent comes from the question, with the input type as
    evidence: an email or link is never postal; a `tel` input is a phone UNLESS its question is explicitly a postal code (sites use
    type=tel for the numeric keyboard of a ZIP), and a question that names a phone is a phone whatever its type."""
    label = label or ""
    if kind in ("email", "url") or NOT_POSTAL.search(label):
        return True
    if kind == "tel":
        return not POSTAL_CODE_Q.search(label) or bool(PHONE_Q.search(label))
    return False


def is_address(label: str, kind: str = "") -> bool:
    """An address part ("City", "State", "Postal Code", "Address Line 1"), not a question that mentions one
    ("Are you a current Federal, State or local government employee?")."""
    if not_postal(label, kind):
        return False                              # "Your email address" asks for an email, not a postal address; a ZIP typed tel still is one
    return bool(ADDRESS.search(label)) and (len(label) <= 40 or re.search(r"(?i)\baddress\b", label) is not None)


ADDRESS = re.compile(r"(?i)\baddress\b|\bstreet\b|^\s*city\b|\bstate\b|^\s*region\b|province|postal|\bzip\b|post\s?code|pin\s?code|county|"
                     r"current location|location \(city\)")


ENTRY_KINDS = {"role": re.compile(r"(?i)work experience|employment"), "degree": re.compile(r"(?i)education")}


def entry_room(view: forms.PageView, ctx: dict) -> dict:
    """How many more entries each repeated section may get: roles on the resume / degrees on file, minus the
    entries the form shows ("Work Experience 1", "Education 2", ...)."""
    data = ctx.get("_applicant") or {}
    need = {"role": len(applicant.answer("Work history", data) or []), "degree": len(applicant.degrees(data))}
    shown = {k: len({f.section for f in view.fields if pat.search(f.section)}) for k, pat in ENTRY_KINDS.items()}
    return {k: need[k] - shown[k] for k in need}


REFERRAL_OPTION = re.compile(r"(?i)referr")
HEARD = re.compile(r"(?i)hear about|heard about|how did you (find|learn)|^\s*source\b")


def own_site_option(options: list[str], company: str) -> str:
    """The option meaning the company's own website / careers site ("Adobe Source > Adobe.com", "Palantir Website",
    "Company careers page")."""
    core = re.sub(r"(?i)\b(inc|llc|corp(oration)?|technologies|ltd|co)\b\.?|[^a-z0-9 ]", "", company.lower()).strip()
    leaf = lambda o: o.rsplit(" > ", 1)[-1].lower()
    for test in (lambda o: core and core in leaf(o) and re.search(r"website|careers?|\.com|site", leaf(o)),
                 lambda o: re.search(r"(company|corporate) (website|careers?|site)|careers? (site|page|website)", leaf(o)),
                 lambda o: core and core in leaf(o) and not re.search(r"employee|referr|event|recruiter|academy|max", leaf(o)),
                 lambda o: re.search(r"\bwebsite\b|\bcareers?\b", leaf(o)) and not REFERRAL_OPTION.search(o)):
        hit = next((o for o in options if test(o)), None)
        if hit:
            return hit
    return ""


def enforce_selected_address(todo: list[forms.Field], answers: dict, ctx: dict) -> None:
    selected = ctx.get("_application_address")
    if not selected:
        return

    address = selected["address"]
    matched = match_entries(todo, ctx.get("_jd", {}).get("company", ""), ctx["_applicant"])

    for f in todo:
        if not_postal(f.label, f.kind):
            continue                      # an email / link / phone field is never filled from the postal address (a tel-typed ZIP is not skipped)
        question = matched.get(f.id, ("", {}))[1].get("question")

        if question == "Current location (city)":
            variants = [f"{address['city']}, {address['state']}"]
        elif question == "Current address":
            label = f.label.lower()

            if re.search(r"line\s*2|address\s*2|apartment|suite|\bunit\b", label):
                variants = [address.get("line2", "")]
            elif re.search(r"country", label):
                variants = [address["country"], "United States of America", "United States", "USA", "US"]
            elif re.search(r"county", label):
                variants = [address.get("county", "")]
            elif re.search(r"postal|\bzip", label):
                variants = [address["postal_code"]]
            elif re.search(r"state|province|region", label):
                variants = [address["state"], selected.get("state_code", "")]
            elif re.search(r"city|town", label):
                variants = [address["city"]]
            elif re.search(r"full|complete", label):
                variants = [
                    f"{address['line1']}, {address['city']}, "
                    f"{address['state']} {address['postal_code']}"
                ]
            else:
                variants = [address["line1"]]
        else:
            continue

        variants = list(dict.fromkeys(v for v in variants if v))
        value = variants[0] if variants else ""

        if f.options:
            normalized = {forms._norm(v) for v in variants}
            value = next(
                (
                    option
                    for option in f.options
                    if forms._norm(option) in normalized
                ),
                value,
            )
        elif f.kind == "combobox":
            value = " | ".join(variants)

        answers[f.id] = Answer(
            id=f.id,
            value=value,
            source="lookup",
            why="job-specific address selected before filling and confirmed by geocoding",
            flag="web-selected application address" if value else "",
            ask_human=f.required and not value,
        )

def enforce(todo: list[forms.Field], answers: dict, ctx: dict, log) -> None:
    """Apply the prepared address and the user's referral-answer policy."""
    enforce_selected_address(todo, answers, ctx)

    addr = applicant.answer("Current address", ctx.get("_applicant")) or {}
    has_addr = isinstance(addr, dict) and any(
        addr.get(key) for key in ("line1", "city", "postal_code")
    )

    contact = ctx.get("REFERRAL")
    contact = contact if isinstance(contact, dict) else {}
    company = ctx.get("_jd", {}).get("company", "")
    from jobagent.filling.facts import compile_facts,resolve as resolve_fact
    evidence=compile_facts(ctx.get('_applicant') or {},ctx.get('_jd_text',''),ctx.get('TODAY',''))
    from jobagent.filling.routing import route,resolve_routed,personal_facts,answer_sheet_facts
    evidence+=personal_facts(ctx)+answer_sheet_facts(ctx.get('ANSWERS_FOR_THIS_APPLICATION',''))
    for field in todo:
        answer=answers.get(field.id)
        if answer is None or answer.source in ('human','learned'):continue
        routing=route(field,evidence)
        if routing.family not in ('start_date','preferred_name','portfolio_link','external_sharing','residency','clearance_eligibility','clearance_held','arbitration','read_ack','event_history','account_status','privacy_consent','future_contact','sms_consent','ai_consent'):continue
        decision=resolve_routed(field,evidence,routing)
        if decision is None:continue
        if decision.value is not None:
            answers[field.id]=answer.model_copy(update={'value':decision.value,'source':'applicant','why':decision.reason or decision.source,'ask_human':False,'flag':''})
        elif decision.reason.startswith('explicit'):
            answers[field.id]=answer.model_copy(update={'value':'','ask_human':True,'flag':decision.reason})


    for field in todo:
        bound=resolve_fact(field,evidence)
        previous=answers.get(field.id)
        if bound.reason=='current OR future sponsorship' and bound.value is not None and not (
                previous and previous.source in ('human','learned')):
            answers[field.id]=Answer(id=field.id,value=bound.value,source='applicant',why=bound.reason)
        decision = referrals.answer_for(field, contact, company)

        if decision is not None:
            answers[field.id] = Answer(id=field.id, **decision)
            continue

        answer = answers.get(field.id)
        if answer is None:
            continue

        if (
            is_address(field.label, field.kind)
            and not has_addr
            and (answer.value or answer.lookup)
            and not field.value
        ):
            answers[field.id] = answer.model_copy(update={
                "value": "",
                "source": "none",
                "ask_human": True,
                "lookup": "",
                "flag": "address data is unavailable",
            })


RULE = re.compile(r"(?i)^\s*rule\s*:\s*")


def ask_human(hitl, f: forms.Field, company: str, host: str, log, timeout_s: float = 1800) -> tuple[str, str]:
    """Ask the candidate about one field now (`jobagent hitl`). Returns ("answer", text) for this application or
    ("rule", text): how to answer questions like this one, kept in applicant.yaml for every company."""
    from jobagent.tools.hitl import HitlTimeout
    opts = f" Options: {' | '.join(f.options[:25])}." if f.options else ""
    try:
        ans = hitl.ask(f"{company} application asks: \"{f.label[:250]}\"{opts} Reply with the answer for this "
                       "application, or 'rule: <how to answer questions like this>' to teach the agent for every "
                       "company ('skip' leaves it empty).", timeout_s=timeout_s).strip()
    except HitlTimeout:
        log(f"apply: no answer to \"{f.label[:60]}\" within {timeout_s / 60:.0f} min; left for review")
        return "answer", ""
    if not ans or ans.lower() == "skip":
        return "answer", ""
    if RULE.match(ans):
        rule = RULE.sub("", ans).strip()
        learned.add(f.label, f.options, "", company, host, rule=rule)
        log(f"apply: learned a rule for \"{f.label[:60]}\": {rule[:100]}")
        return "rule", rule
    why = misfit(f, ans)
    if why:  # e.g. a referrer's name typed into the compensation prompt: ask once more before using or saving it
        log(f"apply: the reply to \"{f.label[:60]}\" looks like it answers another question ({why}); asking again")
        try:
            again = hitl.ask(f"Your reply {ans[:80]!r} to \"{f.label[:200]}\" looks like the answer to a different "
                             f"question ({why}). Reply again with the answer to THIS question, 'keep' to use your reply "
                             "as it is, or 'skip'.", timeout_s=timeout_s).strip()
        except HitlTimeout:
            log("apply: no second reply; the field is left for review")
            return "answer", ""
        if not again or again.lower() == "skip":
            return "answer", ""
        if again.lower() != "keep":
            if RULE.match(again):
                rule = RULE.sub("", again).strip()
                learned.add(f.label, f.options, "", company, host, rule=rule)
                return "rule", rule
            ans = again
    learned.add(f.label, f.options, ans, company, host)
    return "answer", ans


class Fit(BaseModel):
    fits: bool = PField(description="The reply is a plausible answer to this question (any value, right or wrong)")
    why: str = PField("", description="When it doesn't fit: what the reply looks like instead, in a few words")


def misfit(f: forms.Field, ans: str) -> str:
    """Why a reply plainly doesn't answer the field's question ('' when it does, or when unsure): the fast model."""
    if f.options and any(forms._norm(ans) == forms._norm(o) for o in f.options):
        return ""
    try:
        out = llm.structured(Fit, "fast", timeout=60).invoke([HumanMessage(
            f"Form question: {f.label[:300]!r}" + (f" Options: {' | '.join(f.options[:20])}." if f.options else "")
            + f"\nThe candidate replied: {ans[:300]!r}\nIs this a plausible answer to this question? Only a reply that "
              "plainly answers something else (a person's name for a salary, a date for a yes/no) does not fit.")])
    except Exception:
        return ""
    return "" if out.fits else (out.why or "it doesn't match the question")


def consult_learned(todo: list[forms.Field], answers: dict, ctx: dict, model, log) -> None:
    """Questions applicant.yaml didn't answer: look up what the candidate taught on earlier forms (data/learned.yaml,
    only the few entries closest to each question) and plan those fields again with them."""
    stuck = [f for f in todo if f.kind != "file" and not is_address(f.label, f.kind)
             and ((a := answers.get(f.id)) is None or a.ask_human or (f.required and not a.value and not forms.has_value(f)))]
    if not stuck:
        return
    company = ctx.get("_jd", {}).get("company", "")
    found, lines = [], []
    for f in stuck:
        try:
            near = learned.relevant(f.label, company)
        except Exception as e:
            log(f"apply: learned lookup failed ({type(e).__name__})")
            return
        if near:
            found.append(f)
            lines += [f"[{f.id}] {f.label[:120]!r}:"] + [
                f"    - {e['question']!r}: " + (f"rule: {e['instructions']}" if e.get("instructions")
                                                 else f"answer {e.get('answer')!r} (given for {e.get('company')})")
                for e in near]
    if not found:
        return
    log(f"  learned.yaml has something close for {len(found)} question(s); planning them again with it")
    try:
        again, _ = plan(found, {**ctx, "LEARNED": "\n".join(lines)}, model, log,
                        "Answer these with the LEARNED entries shown for each (follow a rule for this form's options);"
                        " if they don't fit the question, ask_human true.")
    except Exception as e:
        log(f"apply: planning with learned entries failed ({type(e).__name__})")
        return
    for i, a in again.items():
        if a.value and not a.ask_human:
            answers[i] = a.model_copy(update={"source": "learned"})


def resolve(todo: list[forms.Field], answers: dict, ctx: dict, model, env: dict, log,
            deferred: list | None = None) -> None:
    """Lookups the planner asked for, then the questions only the candidate can answer."""
    from jobagent.tools import lookup
    need = [f for f in todo if (a := answers.get(f.id)) and a.lookup]
    if need:
        results = []
        for f in need:
            r = lookup.run(answers[f.id].lookup)
            log(f"  lookup {answers[f.id].lookup[:80]!r} -> {r[:120]!r}")
            results.append(f"[{f.id}] {answers[f.id].lookup} => {r}")
        try:
            again, _ = plan(need, ctx, model, log, "LOOKUP RESULTS:\n" + "\n".join(results)
                         + "\nGive the final answers now (lookup '').")
            for i, a in again.items():
                answers[i] = a.model_copy(update={"lookup": "", "source": a.source if a.source != "none" else "lookup"})
        except Exception as e:
            log(f"apply: planning after lookups failed ({type(e).__name__})")
        enforce(todo, answers, ctx, log)  # restore selected address and referral rules
    consult_learned(todo, answers, ctx, model, log)
    if env.get("hitl") is None:
        return
    unanswered = [f for f in todo if f.kind != "file" and not forms.has_value(f)
                  and not (answers.get(f.id) and answers[f.id].value)]
    # ask only what matters: a required field, or a question (choice field) the planner says only the candidate
    # can answer; an optional text box with no data (Address Line 2) stays empty
    flagged = [f for f in unanswered if answers.get(f.id) and answers[f.id].ask_human
               and (f.required or f.kind not in forms.TEXT_KINDS or HEARD.search(f.label))]
    # a required field the planner left empty without asking may vanish once the page is filled (the To date of a
    # job marked "I currently work here"): asked after filling, if it's still there
    later = [f for f in unanswered if f.required and f not in flagged and not is_address(f.label, f.kind)]
    if deferred is not None:
        deferred += later
        ask = flagged + [f for f in unanswered if f.required and is_address(f.label, f.kind) and f not in flagged]
    else:
        ask = flagged + later
    addr = [f for f in ask if is_address(f.label, f.kind)]
    if addr:
        # the address belongs in applicant.yaml's address entry (not a learned answer): one request for all its fields
        from jobagent.tools.hitl import HitlTimeout
        try:
            ans = env["hitl"].ask(f"{env['company']} application needs your address ({', '.join(f.label[:30] for f in addr)}). "
                                  "Fill 'Current address' in data/applicant.yaml, then answer 'done' ('skip' leaves it "
                                  "for review).", timeout_s=env.get("ask_s", 1800)).strip().lower()
        except HitlTimeout:
            ans = ""
        if ans == "done":
            if not ctx.get("_application_address"):
                ctx["APPLICANT"] = applicant.text()
            try:
                answers.update(plan(addr, ctx, model, log)[0])
                enforce(addr, answers, ctx, log)
            except Exception as e:
                log(f"apply: re-planning the address failed ({type(e).__name__})")
        else:
            log("apply: address left for review")
    for f in ask:
        if f in addr:
            continue
        kind, text = ask_human(env["hitl"], f, env["company"], env["host"], log, env.get("ask_s", 1800))
        if kind == "rule":  # plan this field again with the candidate's new rule
            try:
                again, _ = plan([f], {**ctx, "LEARNED": f"[{f.id}] rule: {text}"}, model, log,
                                f"RULE FROM THE CANDIDATE for this question: {text}")
                if again.get(f.id) and again[f.id].value:
                    answers[f.id] = again[f.id].model_copy(update={"source": "learned", "ask_human": False})
            except Exception as e:
                log(f"apply: planning with the new rule failed ({type(e).__name__})")
        elif text:
            answers[f.id] = Answer(id=f.id, why="asked the candidate", value=text, source="human")


def _key(f: forms.Field) -> tuple:
    if f.kind == "file":  # native ID or preserved lazy caption stays stable after mounting/uploading
        return (f.frame, f.kind, f.label.rsplit(" / ", 1)[-1], f.slot)
    return (f.frame, f.kind, f.label[:120], (f.frame_identity+':'+f.identity) if f.identity else f.section)


class PlannerFailed(RuntimeError):
    """The answer planner raised: nothing on the page could be answered. The fields found are still recorded (outcome
    `unplanned`), and the run ends `blocked`, never in review."""


# Exit codes of `jobagent apply` / `submit` (what shell chains and the queue see). 3 is "the posting is unavailable"
# (tools/availability.EXIT_UNAVAILABLE).
EXIT_OK, EXIT_FAILED, EXIT_BLOCKED, EXIT_NOT_READY = 0, 1, 4, 5
# 6: a submission may have gone out and is not proven either way (F04): look before anything is sent again
EXIT_UNCERTAIN = 6


def exit_code(result: dict) -> int:
    """0: on the review screen and ready; 5: on the review screen but not ready (blockers listed); 4: blocked (a bot
    check, the planner, a page that keeps refusing Next) and a human is needed; 1: failed or stuck."""
    state = result.get("state")
    if state == "review":
        return EXIT_OK if result.get("ready_to_submit") else EXIT_NOT_READY
    if state == "blocked":
        return EXIT_BLOCKED
    return EXIT_FAILED


def outcome_of(e: dict, f) -> str:
    """What happened to one field, as the page shows it now (`f`, None if it is gone): verified | mismatch | failed |
    gone | unplanned | unplanned (optional) | unanswered (required) | left empty (optional)."""
    want = e["filled"] or e["answer"]
    if e.get("planner_failed"):
        return "unplanned"
    if want:
        if e["verified"]:
            return "verified"
        if e.get("error"):
            return "failed"
        return "gone" if f is None else "mismatch"
    if f is None:
        return "gone"
    if e.get("clear"):
        return "verified" if e["verified"] else "failed"
    if forms.has_value(f):
        return "verified" if e.get('verified') else "unverified prefill"
    if not e.get("answered", True):
        return "unplanned" if e["required"] else "unplanned (optional)"
    return "unanswered (required)" if e["required"] else "left empty (optional)"


BLOCKING = {"unplanned": "no answer was planned", "unanswered (required)": "left empty", "failed": "could not be filled",
            "mismatch": "the page shows a different value", "unverified prefill": "prefilled value has no verification"}


def blockers_of(record: list[dict]) -> list[str]:
    """Why the application is not ready to send: required fields without a verified answer."""
    out = []
    for e in record:
        reason = BLOCKING.get(e.get("outcome", ""))
        if reason and e.get("required"):
            detail = f": {e['error']}" if e.get("error") else ""
            out.append(f"{e['label'][:60]} (page {e['page']}): required, {reason}{detail}")
    return out


def coverage_blockers(pages: list[dict]) -> list[str]:
    """F17: a page whose frames or regions could not all be read is not known to be fully filled, whatever the fields that
    WERE found say. Each unread part is a reason the application is not ready to send."""
    out = []
    for p in pages:
        for problem in (p.get("extraction") or {}).get("problems", []):
            out.append(f"page {p['page']}: the form was not fully read: {problem}")
    return out


def _blank_entry(f, page_no: int, flag: str) -> dict:
    """A record for a field nobody answered (the planner failed)."""
    return {"page": page_no, "section": f.section, "label": f.label, "kind": f.kind, "required": f.required,
            "options": f.options[:60], "before": f.value, "answer": "", "why": "", "source": "none", "flag": flag,
            "filled": "", "error": "", "answered": False, "planner_failed": True, "frame": f.frame, "slot": f.slot}



def _reconcile_unlisted_school(page,view,ctx,done,page_no,files,log):
    """Evaluate this dependency after all school choices, including added rows."""
    from jobagent.filling.facts import compile_facts
    from jobagent.filling.routing import unlisted_school
    conditional=[f for f in view.fields if re.search(r'(?i)did not see.*(?:university|school)|school (?:is )?not listed',f.label)]
    if not conditional:return view
    facts=compile_facts(ctx.get('_applicant') or {},ctx.get('_jd_text',''),ctx.get('TODAY',''))
    changed=False
    for f in conditional:
        decision=unlisted_school(f,view,facts)
        if decision is None or decision.value is None:continue
        entry=done.setdefault(_key(f),_blank_entry(f,page_no,''))
        entry.update(answer=decision.value,source='applicant',why=decision.reason,identity=f.identity,
                     clear=decision.value=='' and not f.required,answered=True,planner_failed=False,flag='')
        try:
            if decision.value!=f.value:
                entry['filled']=forms.fill_field(page,view,f,decision.value,files)
                changed=True
                log(f"  [{f.kind}] {f.label[:60]!r} <- {decision.value[:60]!r} (school condition)")
            else:entry['filled']=decision.value
            entry['error']=''
        except challenge.Blocked:raise
        except Exception as exc:
            entry.update(error=f'{type(exc).__name__}: {str(exc)[:160]}',answered=False)
    return forms.read_page(page) if changed else view


def fill_page(page: Page, ctx: dict, files: dict, model, log, record: list, page_no: int, env: dict | None = None, known: dict | None = None, errors: list[str] = ()) -> tuple:
    """Plan and fill every field on the current page, then read it all back. Repeats for fields that appear
    after answers (conditional questions), at most 3 rounds. `known`: this page's fields from an attempt the site
    refused; only those its errors name (or that didn't read back) are planned again, nothing is asked twice."""
    err = " ".join(errors).lower()
    done: dict[tuple, dict] = {k: e for k, e in (known or {}).items() if not ((e["filled"] or e["answer"] or e.get("clear")) and (not e.get("verified") or e["label"][:25].lower().strip(" *") in err))}
    tie = _tie_break(model, log)
    feedback = ""
    planner_error = None

    def unplanned(todo_now: list, why: str) -> None:
        """The model behind the answers is unusable: every control found still gets a record (outcome unplanned)."""
        nonlocal planner_error
        planner_error = why
        for g in todo_now:
            done[_key(g)] = _blank_entry(g, page_no, f"the planner failed ({why}); nothing was answered")

    for rnd in range(4):
        view = forms.read_page(page)
        todo = [f for f in view.fields if _key(f) not in done]
        if not todo:
            break
        if env and env.get('out_dir') and not ctx.get('_application_address'):
            matched = match_entries(todo, ctx.get('_jd', {}).get('company', ''), ctx.get('_applicant') or {})
            if any(entry.get('question') in {'Current address', 'Current location (city)'}
                   for _, entry in matched.values()):
                try:
                    application_address.prepare(ctx, env['out_dir'], model, log)
                except Exception as e:  # the model / search behind the address choice is down: the planner cannot answer either
                    log(f"apply: address preparation failed ({type(e).__name__}: {str(e)[:120]})")
                    unplanned(todo, f"address preparation failed: {type(e).__name__}: {str(e)[:140]}")
                    break
        forms.discover_options(page, view, log)
        try:
            answers, add = plan(todo, ctx, model, log, feedback, view.adds if rnd == 0 and not known else (),
                                [f for f in view.fields if _key(f) in done])
        except Exception as e:
            log(f"apply: planning failed ({type(e).__name__}: {str(e)[:120]})")
            unplanned(todo, f"{type(e).__name__}: {str(e)[:160]}")  # every control found gets a record
            break
        missed = [f for f in todo if f.id not in answers and f.kind != "file"]
        if missed:  # long forms: the model sometimes skips a few fields; ask again for just those
            log(f"  planning again for {len(missed)} field(s) the plan skipped")
            try:
                answers.update(plan(missed, ctx, model, log, feedback,
                                    filled=[f for f in view.fields if f not in missed])[0])
            except Exception as e:
                log(f"apply: second planning failed ({type(e).__name__})")
        room = entry_room(view, ctx)
        if rnd == 0 and not known:
            # one entry per resume role / degree on file, whatever the plan asked for: code clicks "Add" for them
            for kind, pat in ENTRY_KINDS.items():
                btn = next((a for a in view.adds if pat.search(a["section"] + " " + a["text"])), None)
                for _ in range(max(0, room.get(kind, 0))):
                    if btn is None:
                        break
                    view.frames[btn["frame"]].locator(f'[data-jf-add="{btn["id"]}"]').first.click(timeout=8000)
                    page.wait_for_timeout(1500)
                    log(f"  added a {kind} entry ({btn['section'][:40]})")
                room[kind] = 0
        for i in add[:8]:  # more entries (other sections the plan asked for): their blank fields come next round
            a = next((x for x in view.adds if x["id"] == i), None)
            kind = next((k for k, pat in ENTRY_KINDS.items() if a and pat.search(a["section"] + " " + a["text"])), None)
            if a and kind and room.get(kind, 0) <= 0:
                log(f"  not adding another {kind} entry: the form already has one per {kind} on file")
                continue
            if kind:
                room[kind] -= 1
            if a and not ENTRY_OK.search(a["section"] + " " + a["text"]) and not _has_data(ctx, a["section"]):
                log(f"  not adding an entry under {a['section'][:40]!r}: nothing for it in applicant.yaml")
                continue
            if a:
                view.frames[a["frame"]].locator(f'[data-jf-add="{i}"]').first.click(timeout=8000)
                page.wait_for_timeout(1500)
                log(f"  added an entry: {a['text']} ({a['section'][:40]})")
        enforce(todo, answers, ctx, log)
        check(todo, answers, ctx, model, log)
        apply_variants(todo, answers, ctx, log)
        enforce(todo, answers, ctx, log)
        for f in todo:  # uploads are decided by their caption, not by the model
            if f.kind == "file" and re.search(r"(?i)autofill|auto-fill|parse your resume", f.label):
                answers[f.id] = Answer(id=f.id, why="an autofill box would overwrite filled answers", value="",
                                       source="applicant")
            elif f.kind == "file":
                kind = upload_role(f.label)
                if kind:
                    answers[f.id] = Answer(id=f.id, why="upload by its caption", value=kind, source="applicant")
        deferred: list[forms.Field] = []
        resolve(todo, answers, ctx, model, env or {}, log, deferred)
        for f in todo:
            a = answers.get(f.id)
            entry = {"page": page_no, "section": f.section, "label": f.label, "kind": f.kind, "required": f.required,
                     "options": f.options[:60], "before": f.value, "answer": a.value if a else "",
                     "why": a.why if a else "",
                     "source": a.source if a else "none", "flag": a.flag if a else "not answered by the planner",
                     "filled": "", "error": "", "answered": a is not None, "frame": f.frame, "slot": f.slot,"identity":f.identity}
            clear_line2 = bool(
                a
                and ctx.get("_application_address")
                and a.source == "lookup"
                and not a.value
                and f.kind in ("text", "textarea")
                and re.search(
                    r"line\s*2|address\s*2|apartment|suite|\bunit\b",
                    f.label,
                    re.I,
                )
                and match_entries(
                    [f],
                    ctx.get("_jd", {}).get("company", ""),
                    ctx["_applicant"],
                ).get(f.id, ("", {}))[1].get("question") == "Current address"
            )

            entry["clear"] = clear_line2
            done[_key(f)] = entry

            if not a or (not a.value and not clear_line2) or (f.value and forms.already_shows(a.value, f.value, f.kind)):
                continue
            if f.kind == "file" and a.value not in files:
                entry["flag"] = entry["flag"] or f"no {a.value} file in the job folder"
                continue
            try:
                if env and env.get('checkpoint'):
                    env['checkpoint'](list(done.values()))
                try:
                    entry["filled"] = forms.fill_field(page, view, f, a.value, files, tie)
                except (forms.FillError, challenge.Blocked):
                    raise
                except Exception:  # a click that timed out (an option list closed under it): once more
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(800)
                    entry["filled"] = forms.fill_field(page, view, f, a.value, files, tie)
                log(f"  [{f.kind}] {f.label[:60]!r} <- {entry['filled'][:60]!r} ({a.source})")
            except challenge.Blocked:
                raise
            except Exception as e:
                entry["error"] = f"{type(e).__name__}: {str(e)[:200]}"
                page.keyboard.press("Escape")  # leave no option list open over the next field
                log(f"  [{f.kind}] {f.label[:60]!r}: fill failed: {entry['error'][:160]}")
        page.wait_for_timeout(1200)
        if deferred and env and env.get("hitl"):
            now_view = forms.read_page(page)
            now_shown = {_key(g): g for g in now_view.fields}
            for f in deferred:
                g = now_shown.get(_key(f))
                if g is None or g.value:
                    continue  # gone or filled by the page itself once the rest was answered
                kind, ans = ask_human(env["hitl"], g, env["company"], env["host"], log, env.get("ask_s", 1800))
                entry = done.get(_key(f))
                if kind == "rule" and ans:
                    try:
                        again, _ = plan([g], ctx, model, log, f"RULE FROM THE CANDIDATE for this question: {ans}")
                        ans = again[g.id].value if again.get(g.id) else ""
                    except Exception:
                        ans = ""
                if not ans or entry is None:
                    continue
                entry.update(answer=ans, source="human", flag="")
                try:
                    entry["filled"] = forms.fill_field(page, now_view, g, ans, files, tie)
                except challenge.Blocked:
                    raise
                except Exception as e:
                    entry["error"] = f"{type(e).__name__}: {str(e)[:200]}"
                    page.keyboard.press("Escape")
    # one repair pass: a field that doesn't show its answer now (a section the page rebuilt after an earlier answer,
    # e.g. the address after the country changed) is filled once more
    view = forms.read_page(page)
    now_shown = {_key(g): g for g in view.fields}
    for key, e in done.items():
        g = now_shown.get(key)
        want = "" if e.get("clear") else e["filled"] or e["answer"]
        if g is None or e["kind"] == "file":
            continue
        if e.get("clear"):
            if not g.value:
                continue
        elif not want or forms.already_shows(want, g.value, g.kind):
            continue
        try:
            e["filled"] = forms.fill_field(page, view, g, want, files, tie)
            log(f"  [{g.kind}] {g.label[:60]!r} <- {str(e['filled'])[:60]!r} (repair pass)")
        except challenge.Blocked:
            raise
        except Exception as err:
            e["error"] = f"{type(err).__name__}: {str(err)[:200]}"
            page.keyboard.press("Escape")
    # read-back: every field as the page shows it now
    view = _reconcile_unlisted_school(page,forms.read_page(page),ctx,done,page_no,files,log)
    shown = {_key(f): f for f in view.fields}
    by_label: dict[tuple, list] = {}
    for g in view.fields:
        by_label.setdefault(_key(g)[:3], []).append(g)
    for key, e in done.items():
        f = shown.get(key)
        if f is None and not e.get('identity') and len(by_label.get(key[:3], [])) == 1:
            f = by_label[key[:3]][0]  # the same field; only the heading above it changed
        e["readback"] = f.value if f else "(field gone)"
        if e["kind"] == "file" and e["filled"] and not (f and f.value and Path(e["filled"]).name in f.value):
            # the upload box is replaced by the file's name once attached
            name = Path(e["filled"]).name
            e["readback"] = name if any(name in fr.locator("body").inner_text() for fr in view.frames.values()) \
                else "(no file shown)"
            f = f or forms.Field(id=-1, kind="file", label=e["label"], value=e["readback"])
            f.value = e["readback"]
        want = e["filled"] or e["answer"]
        if f is None:
            e["verified"] = False
        elif e.get("clear"):
            e["verified"] = not f.value and not e["required"]
        elif not want:
            e["verified"] = False  # a nonempty prefill alone is not source verification
        else:
            e["verified"] = forms.same_value(want, f.value, e["kind"],f.label)
        if e['kind']=='file' and e.get('filled'):
            proofs=[proof for proof in getattr(page,'_jobagent_upload_proofs',[]) if proof.identity==e.get('identity')]
            proof=next((proof for proof in reversed(proofs) if proof.verify()),None)
            e['verified']=bool(proof)
            if proof:e.update(readback=proof.name,held_file_sha256=proof.expected,upload_verification=proof.method)
        e["outcome"] = outcome_of(e, f)
        record.append(e)
    if planner_error:
        raise PlannerFailed(planner_error)
    return view, done


def _start(page: Page, url: str, ensure_signed_in, log, fallback: str | None = None, hitl=None,
           already_open: bool = False) -> forms.PageView:
    """Open the posting and get to the application form: click Apply / Apply Manually, sign in when asked.

    A page saying the job is closed, expired, gone or already applied to raises `availability.Unavailable`. When
    that page is the scraped apply link, the posting itself (`fallback`) is tried first: an apply link can be stale
    while the posting's own Apply button still works (GoDaddy, 2026-11-07)."""
    response = None if already_open else page.goto(url, wait_until="domcontentloaded")
    signed, empty = False, 0
    for _ in range(18):
        page.wait_for_timeout(4000)
        view = forms.read_page(page)
        policy = getattr(page.context, 'jobagent_policy', None)
        if policy:
            policy(page)
        if block := challenge.detect(page):
            # a bot check (Cloudflare "Verify you are human", a CAPTCHA) is the candidate's to solve in noVNC
            if hitl is None:
                raise challenge.Blocked(f"a {block.kind} check blocks {challenge.safe_url(page.url)}: solve it in noVNC "
                                   "(http://localhost:6080) and run again")
            challenge.wait_for_user(page, block, hitl, log, timeout_s=900)
            empty = 0
            page.wait_for_timeout(5000)  # the page reloads after a passed check
            continue
        if len([f for f in view.fields if f.kind != "file"]) < 3 and (
                gone := availability.check_page(page, response)):
            if fallback and fallback != url:
                log(f"apply: {page.url} says '{gone.reason}'; trying the posting's own Apply button at {fallback}")
                url, fallback, empty = fallback, None, 0
                response = page.goto(url, wait_until="domcontentloaded")
                continue
            raise gone
        response = None  # the status only describes the first load of a URL
        pw = any(f.kind == "password" for f in view.fields)
        if pw and not signed:
            log("apply: the portal asks to sign in")
            auth = ensure_signed_in(page.url)
            if getattr(auth, "signed_in", True) is False:
                raise RuntimeError("Portal sign-in did not complete; application filling stopped")
            signed = True
            response = page.goto(url, wait_until="domcontentloaded")  # back to the posting, signed in
            continue
        if len([f for f in view.fields if f.kind != "file"]) >= 3 and not pw:
            return view
        for pat in START:
            # navigation only: a bare "Apply" that is the final button of a form with fields is never clicked here
            if (t := forms.click_open_application(page, view, pat, expected_url=url)):
                log(f"apply: clicked {t!r}")
                break
        else:
            if len(view.fields) >= 1:
                return view
            empty += 1  # still loading (clicks don't wait for the next page), or really nothing here
            if empty >= 10:  # a slow portal can show its form's spinner for half a minute
                screenshot(page, "/app/state/logs/apply-start.png")
                raise RuntimeError(f"no application form and no Apply button on {page.url} (buttons: "
                                   f"{view.buttons[:12]}; an Apply control that belongs to a form with fields is never "
                                   "clicked at startup; screenshot state/logs/apply-start.png)")
    return forms.read_page(page)


def run(page: Page, job_dir: str | Path, company: str, *args, **kwargs) -> dict:
    from jobagent.filling.metrics import FillingMetrics,RecordedHitl
    metrics=FillingMetrics();kwargs['_filling_metrics']=metrics
    if kwargs.get('hitl') is not None:kwargs['hitl']=RecordedHitl(kwargs['hitl'],metrics)
    try:
        result=_guarded_run(page,job_dir,company,*args,**kwargs)
    except Exception:
        path=Path(job_dir)/'application'/'application.json'
        if path.exists():
            result=json.loads(path.read_text());result['filling_metrics']=metrics.snapshot()
            path.write_text(json.dumps(result,indent=2))
        raise
    result['filling_metrics']=metrics.snapshot()
    (Path(job_dir)/'application'/'application.json').write_text(json.dumps(result,indent=2))
    return result


def _guarded_run(page: Page, job_dir: str | Path, company: str, *args, **kwargs) -> dict:
    out = Path(job_dir) / "application"
    out.mkdir(parents=True, exist_ok=True)

    def ready(p):
        view = forms.read_page(p)
        return bool(view.fields or any(START_PATTERN.search(b) for START_PATTERN in START for b in view.buttons))

    with challenge.session(page, kwargs.get('hitl'), ready, out, kwargs.get('log', print),
                           kwargs.pop('browser_policy', None), kwargs.pop('desktop_fallback', False)):
        try:
            from jobagent.filling import select_strategy
            from jobagent.filling.discovery import Discovery
            discovery=Discovery(page) if select_strategy(kwargs.get('filling_strategy'),resume_job=job_dir)=='experimental' else None
            if discovery:page._jobagent_discovery=discovery
            try:
                return _run(page, job_dir, company, *args, **kwargs)
            finally:
                for proof in getattr(page,'_jobagent_upload_proofs',[]):proof.close()
                if hasattr(page,'_jobagent_upload_proofs'):del page._jobagent_upload_proofs
                if discovery:
                    discovery.close()
                    del page._jobagent_discovery
        except availability.Unavailable as exc:
            checkpoint = out / "checkpoint.json"
            result = json.loads(checkpoint.read_text()) if checkpoint.exists() else {"fields": [], "pages": []}
            result.update(state=exc.status, unavailable_kind=exc.kind, reason=str(exc),
                          url=challenge.safe_url(page.url), resumable=False,
                          browser_mode='desktop' if kwargs.get('already_open') else 'docker',
                          verified=sum(bool(f.get('verified')) for f in result['fields']))
            result['flags'] = [f for f in result['fields'] if not f.get('verified')]
            (out / "application.json").write_text(json.dumps(result, indent=2))
            (out / "application.md").write_text(f"# Application unavailable\n\nState: {exc.status}\n\n"
                                                f"{exc}\n\nNothing was submitted.\n")
            raise
        except challenge.Blocked as exc:
            checkpoint = out / "checkpoint.json"
            result = json.loads(checkpoint.read_text()) if checkpoint.exists() else {"fields": [], "pages": []}
            result.update(state="blocked", blocked_by="challenge", reason=str(exc), url=challenge.safe_url(page.url),
                          ready_to_submit=False, blockers=[str(exc)],
                          resumable=True, desktop_fallback_requested=isinstance(exc, challenge.DesktopFallback),
                          verified=sum(bool(f.get('verified')) for f in result['fields']))
            result['flags'] = [f for f in result['fields'] if not f.get('verified')]
            (out / "application.json").write_text(json.dumps(result, indent=2))
            return result
        except Exception as exc:  # unexpected: leave a durable, honest result (never a clean-looking absence), then fail loudly
            checkpoint = out / "checkpoint.json"
            result = json.loads(checkpoint.read_text()) if checkpoint.exists() else {"fields": [], "pages": []}
            result.update(state="failed", reason=f"{type(exc).__name__}: {str(exc)[:300]}", url=challenge.safe_url(page.url),
                          ready_to_submit=False, blockers=[f"the run failed: {type(exc).__name__}"], resumable=True,
                          verified=sum(bool(f.get('verified')) for f in result['fields']))
            result['flags'] = [f for f in result['fields'] if not f.get('verified')]
            (out / "application.json").write_text(json.dumps(result, indent=2))
            (out / "application.md").write_text(f"# Application failed\n\nState: failed\n\n{result['reason']}\n\n"
                                                "Nothing was submitted.\n")
            raise


def recover_desktop_form(page, log=print):
    """Wait for startup, then reload one incomplete careers screen; never retry a challenge."""
    for _ in range(6):
        block = challenge.detect(page)
        if forms.read_page(page).fields or (block and block.kind != 'unknown'):
            return
        page.wait_for_timeout(500)
    if challenge.detect(page) is not None:
        return
    text = page.locator('body').inner_text().casefold()
    incomplete = 'apply for this job' in text and 'submit application' in text
    if 'page not found' not in text and not incomplete:
        return
    log('apply: desktop incomplete-page recovery: one cache-bypassing reload')
    connection = page.context.new_cdp_session(page)
    try:
        connection.send('Page.reload', {'ignoreCache': True})
    finally:
        connection.detach()
    for _ in range(20):
        page.wait_for_timeout(1000)
        policy = getattr(page.context, 'jobagent_policy', None)
        if policy:
            policy(page)
        block = challenge.detect(page)
        if forms.read_page(page).fields or (block and block.kind != 'unknown'):
            break


def _run(page: Page, job_dir: str | Path, company: str, ensure_signed_in=None, log=print, model=None,
        hitl=None, ask_s: float = 1800, model_name: str | None = None, at_review=None,
        already_open: bool = False, observe_s: float = 0, filling_strategy: str | None = None,
        _filling_metrics=None) -> dict:
    """Fill the application to its review screen. Never clicks Submit itself: `at_review(page, view, result)` (step 13's
    approval and submission) is called there, in the same browser session, when given."""
    job_dir = Path(job_dir)
    from jobagent.filling import select_strategy
    strategy=select_strategy(filling_strategy,resume_job=job_dir)
    ctx = context(job_dir, company)
    url = ctx["_jd"].get("apply_url") or ctx["_jd"].get("source_url")
    model = model or llm.writer(8000, timeout=300, model=model_name)
    files = {k: str(job_dir / f"{k}.pdf") for k in ("resume", "cover_letter") if (job_dir / f"{k}.pdf").exists()}
    out_dir = job_dir / "application"
    out_dir.mkdir(exist_ok=True)
    if hasattr(model, 'callbacks'):
        audit = out_dir / 'planner-calls.jsonl'
        audit.write_text('')
        model.callbacks = list(model.callbacks or []) + [PlannerAudit(audit, getattr(model, 'model_name', 'configured writer'))]
        if _filling_metrics is not None:model.callbacks.append(_filling_metrics)
    if already_open:
        recover_desktop_form(page, log)
        challenge.guard(page)
        if gone := availability.check_page(page):
            raise gone
        # Listing pages can have utility controls (for example a language picker).
        # Use the same guarded navigation as Docker, even when those fields exist.
        _start(page, url, ensure_signed_in or (lambda u: None), log,
               fallback=ctx['_jd'].get('source_url'), hitl=hitl, already_open=True)
        if not forms.read_page(page).fields:
            challenge.wait_for_user(page, challenge.Challenge('unknown', challenge.safe_url(page.url),
                                    'Desktop application has not loaded'), hitl, log, timeout_s=900)
        if not forms.read_page(page).fields:
            raise challenge.Blocked('The desktop application form is not usable yet')
    record: list[dict] = []
    result = {"url": url, "started": datetime.now().isoformat(timespec="seconds"), "pages": [], "state": "failed"}
    result['planner'] = {'provider': 'Voyager', 'model': getattr(model, 'model_name', model_name or 'configured writer')}
    result['browser_mode'] = 'desktop' if already_open else 'docker'
    result['filling_strategy']=strategy
    result["address_lookup"] = None  # prepared before planning if an address field is actually encountered
    def checkpoint(current=None):
        result['address_lookup'] = ctx.get('_application_address')
        (out_dir / "checkpoint.json").write_text(json.dumps({**result, "fields": record + (current or [])}, indent=2))
    checkpoint()
    if already_open:
        challenge.guard(page)
        view = forms.read_page(page)
    else:
        view = _start(page, url, ensure_signed_in or (lambda u: None), log, fallback=ctx["_jd"].get("source_url"), hitl=hitl)
    same = 0
    env = {"hitl": hitl, "company": company, "host": urlparse(url).netloc, "ask_s": ask_s,
           "checkpoint": checkpoint, 'out_dir': out_dir}
    known, errors, page_no, start = None, [], 0, 0
    if _filling_metrics is not None:_filling_metrics.begin()
    for _ in range(MAX_PAGES + 3):
        challenge.guard(page)
        view = forms.read_page(page)
        if known is None:  # a new page (not a retry of a refused one)
            page_no += 1
            start = len(record)
        else:
            del record[start:]  # the retry records this page's fields again
        heads = " / ".join(view.headings[:3])
        log(f"apply: page {page_no}{' (again: ' + '; '.join(errors)[:120] + ')' if known else ''}: {heads[:100]} "
            f"({len(view.fields)} fields)")
        try:
            if strategy=='experimental':
                from jobagent.filling.adapter import fill_page as planned_page
                view,done=planned_page(page,ctx,files,model,log,record,page_no,env,known,errors)
                if env.get('_experimental_blockers'):
                    result.update(state='blocked',blocked_by='filling',reason='; '.join(env['_experimental_blockers']),resumable=True)
                    result['pages'].append({'page':page_no,'headings':view.headings[:4],'errors':view.errors,
                                            'buttons':view.buttons,'extraction':view.coverage.summary()})
                    checkpoint();break
            else:
                view, done = fill_page(page, ctx, files, model, log, record, page_no, env, known, errors)
        except PlannerFailed as exc:
            # the fields found are recorded (outcome unplanned); the form is not advanced, and no review is offered
            result.update(state="blocked", blocked_by="planner", reason=f"the answer planner failed on page {page_no}: {exc}",
                          resumable=True)
            log(f"apply: blocked: {result['reason']}")
            view = forms.read_page(page)
            checkpoint()
            break
        checkpoint()
        before = wizard.state_of(page, view)          # F16: address, step marker, field schema - not just headings
        shot = out_dir / f"page-{page_no}.png"
        screenshot(page, str(shot))
        result["pages"].append({"page": page_no, "headings": view.headings[:4], "screenshot": shot.name,
                                "errors": view.errors, "buttons": view.buttons,
                                "extraction": view.coverage.summary()})           # F17: what the reader could and could not see
        if forms.click_button(page, view, forms.NEXT) is None:
            human = forms.human_check(page)
            if human:
                result["human_check"] = human  # the candidate ticks it in noVNC; then Submit shows
            if any(forms.SUBMIT.search(b) for b in view.buttons) or human or forms.has_submit_input(page):
                result["state"] = "review"  # only Submit is left: the review screen; the user submits (step 13)
                log("apply: reached the review screen (Submit not clicked)")
            else:
                result["state"] = "stuck"
                log(f"apply: no Next or Submit button; buttons: {view.buttons[:15]}")
            break
        verdict, view, _after, why = wizard.wait_for_progress(page, before)       # the next page loads in steps
        if verdict == "same":
            # the site refused Next on this page (its error banner may be out of view: the page just didn't move)
            same += 1
            if same >= 2:
                result["state"] = "blocked"
                result["blocked_by"] = "page"
                result["reason"] = f"the page keeps refusing Next: {view.errors[:5]}"
                log(f"apply: the page keeps refusing Next: {view.errors[:5]}")
                break
            known, errors = done, view.errors or ["the page did not move on after Next / Save and Continue"]
        else:
            log(f"apply: moved on ({why})")
            known, errors, same = None, [], 0
    if _filling_metrics is not None:_filling_metrics.finish()
    if observe_s:
        deadline = time.monotonic() + observe_s
        while time.monotonic() < deadline:
            challenge.guard(page)
            page.wait_for_timeout(2000)
        result['observed_seconds'] = observe_s
    result["fields"] = record
    if _filling_metrics is not None:
        _filling_metrics.finish();result['filling_metrics']=_filling_metrics.snapshot()
    if strategy=='experimental':
        result['experimental_pages']=ctx.get('_experimental_pages',[])
        filler=ctx.get('_experimental_filler')
        result['experimental_questions']=filler.questions if filler else 0
        result['experimental_question_batches']=filler.batches if filler else 0
    # "verified" means read back as the answer we put there (or a value the page already had); an optional field nobody
    # answered is not "verified", it is counted under its own outcome
    result["verified"] = sum(1 for e in record if e.get("outcome", "verified" if e.get("verified") else "") == "verified")
    result["outcomes"] = {k: sum(1 for e in record if e.get("outcome") == k) for k in sorted({e.get("outcome") for e in record} - {None})}
    result["flags"] = [e for e in record if e["flag"] or e["error"] or not e.get("verified")]
    # reaching the review screen is not being ready to send: required fields, uploads and page errors decide that
    blockers = blockers_of(record) + coverage_blockers(result["pages"])
    result["extraction_complete"] = all(p.get("extraction", {}).get("complete", True) for p in result["pages"])
    if result["state"] == "blocked":
        blockers.insert(0, result.get("reason", "the run is blocked"))
    if result["state"] == "review" and view.errors:
        blockers.append("the page shows errors: " + "; ".join(view.errors[:3])[:200])
    result["blockers"] = blockers
    result["ready_to_submit"] = result["state"] == "review" and not blockers
    (out_dir / "application.json").write_text(json.dumps(result, indent=2))
    (out_dir / "application.md").write_text(report(result, ctx["_jd"]))
    if at_review is not None and result["state"] == "review":
        result["submission"] = at_review(page, view, result)
        (out_dir / "application.json").write_text(json.dumps(result, indent=2))
    return result


def report(result: dict, jd: dict) -> str:
    lines = [f"# Application: {jd.get('company', '')} - {jd.get('title', '')}", "",
             f"- URL: {result['url']}", f"- State: **{result['state']}** (never submitted)"]
    if result["state"] == "blocked":
        lines.append(f"- **Blocked** ({result.get('blocked_by', '')}): {result.get('reason', '')}")
    lines.append("- Ready to submit: **yes**" if result.get("ready_to_submit") else "- Ready to submit: **NOT ready**")
    lines += [f"  - {b}" for b in result.get("blockers", [])]
    others = ", ".join(f"{n} {k}" for k, n in result.get("outcomes", {}).items() if k != "verified")
    lines += [f"- Fields: {len(result['fields'])}, read back and verified: {result['verified']}" + (f" ({others})" if others else ""),
              f"- Flags for review: {len(result['flags'])}", "", "| Page | Field | Answer (as the page shows it) | Source | Outcome | OK | Flag |",
              "|---|---|---|---|---|---|---|"]
    for e in result["fields"]:
        cell = lambda s: str(s).replace("|", "/").replace("\n", " ")[:120]
        lines.append(f"| {e['page']} | {cell(e['label'][:80])} | {cell(e.get('readback', ''))} | {e['source']} | "
                     f"{e.get('outcome', '')} | {'yes' if e.get('verified') else '**no**'} | {cell(e['flag'] or e['error'])} |")
    return "\n".join(lines) + "\n"
