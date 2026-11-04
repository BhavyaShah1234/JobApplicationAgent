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
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import yaml
from rapidfuzz import fuzz
from langchain_core.messages import HumanMessage, SystemMessage
from playwright.sync_api import Page
from pydantic import BaseModel, Field as PField

from jobagent import llm
from jobagent.clock import now
from jobagent.tools import applicant, forms
from jobagent.tools.browser import screenshot

DATA = Path("/app/data")
MAX_PAGES = 15
START = [re.compile(p, re.I) for p in (r"^apply manually$", r"^apply now$", r"^apply( for this (job|position))?( online)?$",
                                       r"^(i'?m interested|start (your |my )?application|begin application)$",
                                       r"^apply to (this )?job$")]


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
    ask_human: bool = PField(False, description="True when APPLICANT (learned answers included) doesn't answer "
                                                "this question: the candidate is asked now and the answer is kept")


class Plan(BaseModel):
    answers: list[Answer]
    add: list[int] = PField(default_factory=list, description="ADD BUTTON ids to click, one per extra entry needed "
                                                                "(e.g. a second work experience); [] when none")


PROMPT = """You fill a job application form for the candidate, from the candidate's own files only. For each field
listed (id, kind, label, options, current value) give the answer to put in it.

APPLICANT is the candidate's answer file: entries of question, seen (wordings portals use), answer, instructions,
tools; its `learned` section holds answers the candidate gave on earlier forms (with the company). An answer given
as a LIST of words (e.g. degree: ["Master of Science", "MS", "Master's degree"]) lists acceptable variants of one
answer in order of preference: use the first one the form offers (or the closest option to it); "Other" in such a
list is the last resort. A list of entries (degrees, work history) is one entry per item. A field marked
MATCHES is the APPLICANT entry for that question: use its answer, following its instructions.

Rules:
- Personal facts (name, contact, address, education, work authorization, sponsorship, citizenship,
  self-identification) ONLY from APPLICANT, exactly. Never invent an address, city, ZIP, date of birth, SSN or any
  other personal fact it doesn't hold: leave value '' with source none and ask_human true. Instructions never
  license inventing one.
- An answer that is empty in APPLICANT but has instructions: follow the instructions (compute, write, or look it up).
  Empty with no instructions: ask_human true.
- Self-identification APPLICANT leaves empty: the "I don't wish to answer" / "decline" option (source applicant).
- learned answers: the same company's answer as is; another company's only if the question isn't specific to it.
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
- Referral fields: only the REFERRAL given below, else ''.
- File fields: value 'resume' for a resume / CV upload, 'cover_letter' for a cover letter upload, '' otherwise.
- A field with a current value that is already right: repeat it with source already_filled. Wrong: correct it.
- Answer every listed field id exactly once."""


def context(job_dir: Path, company: str) -> dict:
    """The candidate's files, as the planner sees them."""
    resume = ""
    if (job_dir / "resume.pdf").exists():
        resume = subprocess.run(["pdftotext", "-layout", str(job_dir / "resume.pdf"), "-"], capture_output=True,
                                text=True).stdout
    jd = json.loads((job_dir / "jd.json").read_text()) if (job_dir / "jd.json").exists() else {}
    comp = Path(f"/app/state/companies/{company}.md")
    tex = (job_dir / "resume.tex").read_text() if (job_dir / "resume.tex").exists() else ""
    data = applicant.with_resume(applicant.load(), applicant.resume_experience(tex))
    return {
        # the answer file, its experience answers taken from this job's tailored resume
        "APPLICANT": yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=120),
        "RESUME": resume[:6000],
        "JD": ((job_dir / "jd.md").read_text() if (job_dir / "jd.md").exists() else "")[:5000],
        "COMPANY": (comp.read_text() if comp.exists() else "")[:3000],
        "REFERRAL": referral(jd.get("company") or company),
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


def referral(company: str) -> str:
    """A referral the candidate may name: a data/targets.csv contact at this company with referral_consent yes."""
    import csv
    p = DATA / "targets.csv"
    if p.exists():
        for row in csv.DictReader(p.open()):
            if (row.get("company", "").strip().lower() == company.strip().lower()
                    and row.get("referral_consent", "").strip().lower() in ("yes", "y", "true") and row.get("contact_name")):
                return f"{row['contact_name']} ({row.get('contact_email', '')}), who agreed to refer the candidate"
    return "none (no referral for this job: never answer 'Referral' / 'Employee referral')"


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


def hints(fields: list[forms.Field], company: str = "", data: dict | None = None) -> dict[int, str]:
    """The matched APPLICANT entry of each field, as the planner sees it next to the field."""
    out = {}
    for fid, (sec, e) in match_entries(fields, company, data).items():
        out[fid] = (f"{sec}: {e['question']!r} answer={e.get('answer')!r}"
                    + (f" instructions={e.get('instructions')!r}" if e.get("instructions") else "")
                    + (f" tools={e.get('tools')}" if e.get("tools") else "")
                    + (f" company={e.get('company')}" if e.get("company") else ""))
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
    return "\n".join(lines) or "(none)"


def plan(fields: list[forms.Field], ctx: dict, model, log=print, feedback: str = "", adds: list[dict] = (),
         filled: list[forms.Field] = ()) -> tuple[dict[int, Answer], list[int]]:
    files = "\n\n".join(f"== {k} ==\n{v}" for k, v in ctx.items() if not k.startswith("_"))
    h = hints(fields, ctx.get("_jd", {}).get("company", ""), ctx.get("_applicant"))
    msgs = [SystemMessage(PROMPT),
            HumanMessage(f"{files}\n\n"
                         + ("== ALREADY ON THIS PAGE (planned earlier) ==\n" + "\n".join(f.brief() for f in filled)
                            + "\n\n" if filled else "")
                         + "== FORM FIELDS ==\n" + "\n".join(f.brief() + (f"\n    MATCHES {h[f.id]}" if f.id in h else "")
                                                             for f in fields)
                         + ("\n\n== ADD BUTTONS ==\n" + "\n".join(f"[{a['id']}] {a['text']} (section: {a['section']})"
                                                                for a in adds) if adds else "")
                         + (f"\n\n== FEEDBACK ==\n{feedback}" if feedback else ""))]
    out = llm.structured_invoke(model, Plan, msgs, first="function_calling", log=log)
    return {a.id: a for a in out.answers}, out.add


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
supported when true to the RESUME. Following an APPLICANT entry's instructions is support, and so are these rules:
agreeing to a consent needed to apply, choosing "I don't wish to answer" for self-identification APPLICANT leaves
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
    rows = [f"{f.brief()}\n    PLANNED value={a.value!r} source={a.source} why={a.why!r}"
            for f in fields if (a := answers.get(f.id)) and a.value and a.source not in ("human", "learned", "lookup")
            and f.kind != "file"]
    if not rows:
        return
    files = "\n\n".join(f"== {k} ==\n{v}" for k, v in ctx.items() if not k.startswith("_") and k != "JD")
    try:
        out = llm.structured_invoke(model, Checks, [SystemMessage(CHECK_PROMPT), HumanMessage(
            files + "\n\n== PLANNED ANSWERS ==\n" + "\n".join(rows))], first="function_calling", log=log)
    except Exception as e:
        log(f"apply: plan check failed ({type(e).__name__}); keeping the plan")
        return
    data = ctx.get("_applicant") or {}
    h = hints(fields, ctx.get("_jd", {}).get("company", ""), data)
    for c in out.checks:
        a = answers.get(c.id)
        if a is None or c.supported:
            continue
        f = next((x for x in fields if x.id == c.id), None)
        if a.source == "applicant" and (in_applicant(a.value, data) or "instructions=" in h.get(c.id, "")):
            continue  # the file does say it (one of an answer's variants), or its instructions give it
        if a.source == "applicant":
            # credited to the candidate's own file but not in it (or in conflict with the resume): theirs to say
            log(f"  check: [{c.id}] {a.value[:40]!r} isn't in applicant.yaml as planned; asking the candidate")
            answers[c.id] = a.model_copy(update={"ask_human": True, "value": "",
                                                 "flag": (a.flag + f" planned {a.value[:40]!r}").strip()})
            continue
        if c.value and f and f.options and forms.match_option(c.value, f.options)[0] is None:
            # a "correction" that isn't an offered option is no fix
            answers[c.id] = a.model_copy(update={"flag": (a.flag + f" check suggests {c.value[:60]!r}").strip()})
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
ADDRESS = re.compile(r"(?i)\baddress\b|\bstreet\b|^\s*city\b|\bstate\b|^\s*region\b|province|postal|\bzip\b|county|"
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


def enforce(todo: list[forms.Field], answers: dict, ctx: dict, log) -> None:
    """Two facts only the candidate can state, whatever an instruction says: the current address (from the address
    entry, never looked up or made up) and a referral (only a data/targets.csv contact who agreed to refer)."""
    addr = applicant.answer("Current address", ctx.get("_applicant")) or {}
    has_addr = isinstance(addr, dict) and any(addr.get(k) for k in ("line1", "city", "postal_code"))
    for f in todo:
        a = answers.get(f.id)
        if a is None:
            continue
        if ADDRESS.search(f.label) and not has_addr and (a.value or a.lookup) and not f.value:
            log(f"  [{f.id}] {f.label[:40]!r}: no address on file; not using {(a.value or a.lookup)[:40]!r}")
            answers[f.id] = a.model_copy(update={"value": "", "source": "none", "ask_human": True, "lookup": "",
                                                 "flag": "needs your real address (applicant.yaml Current address)"})
        elif HEARD.search(f.label) and ctx.get("REFERRAL", "none").startswith("none") and \
                (site := own_site_option(f.options, ctx.get("_jd", {}).get("company", ""))) and a.value != site:
            # no referral to name: the company's own website / careers site, which is where the agent found the job
            log(f"  [{f.id}] {f.label[:40]!r}: {a.value[:40] or 'no answer'!r} -> {site!r} (no consenting referral)")
            answers[f.id] = a.model_copy(update={"value": site or "", "ask_human": not site, "source": "applicant",
                                                 "flag": "referral only with a data/targets.csv contact who agreed"})
        elif REFERRAL_OPTION.search(a.value) and ctx.get("REFERRAL", "none").startswith("none"):
            log(f"  [{f.id}] {f.label[:40]!r}: {a.value[:40]!r} claims a referral; no consenting contact on file")
            answers[f.id] = a.model_copy(update={"value": "", "ask_human": True,
                                                 "flag": "referral only with a data/targets.csv contact who agreed"})


def learn(question: str, options: list[str], answer: str, company: str, host: str) -> None:
    """Keep the candidate's answer to a question for the next form that asks it (applicant.yaml, `learned`)."""
    applicant.learn(question, options, answer, company, host)


def ask_human(hitl, f: forms.Field, company: str, host: str, log, timeout_s: float = 1800) -> str:
    """Ask the candidate one question now (answer in `jobagent hitl`); the answer is kept in applicant.yaml."""
    from jobagent.tools.hitl import HitlTimeout
    opts = f" Options: {' | '.join(f.options[:25])}." if f.options else ""
    try:
        ans = hitl.ask(f"{company} application asks: \"{f.label[:250]}\"{opts} Your answer? ('skip' leaves it "
                       "empty; it's saved for next time)", timeout_s=timeout_s).strip()
    except HitlTimeout:
        log(f"apply: no answer to \"{f.label[:60]}\" within {timeout_s / 60:.0f} min; left for review")
        return ""
    if not ans or ans.lower() == "skip":
        return ""
    learn(f.label, f.options, ans, company, host)
    return ans


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
        enforce(todo, answers, ctx, log)  # a lookup never supplies an address or a referral
    if env.get("hitl") is None:
        return
    unanswered = [f for f in todo if f.kind != "file" and not f.value
                  and not (answers.get(f.id) and answers[f.id].value)]
    # ask only what matters: a required field, or a question (choice field) the planner says only the candidate
    # can answer; an optional text box with no data (Address Line 2) stays empty
    flagged = [f for f in unanswered if answers.get(f.id) and answers[f.id].ask_human
               and (f.required or f.kind not in forms.TEXT_KINDS)]
    # a required field the planner left empty without asking may vanish once the page is filled (the To date of a
    # job marked "I currently work here"): asked after filling, if it's still there
    later = [f for f in unanswered if f.required and f not in flagged and not ADDRESS.search(f.label)]
    if deferred is not None:
        deferred += later
        ask = flagged + [f for f in unanswered if f.required and ADDRESS.search(f.label) and f not in flagged]
    else:
        ask = flagged + later
    addr = [f for f in ask if ADDRESS.search(f.label)]
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
            ctx["APPLICANT"] = applicant.text()
            try:
                answers.update(plan(addr, ctx, model, log)[0])
            except Exception as e:
                log(f"apply: re-planning the address failed ({type(e).__name__})")
        else:
            log("apply: address left for review")
    for f in ask:
        if f in addr:
            continue
        ans = ask_human(env["hitl"], f, env["company"], env["host"], log, env.get("ask_s", 1800))
        if ans:
            answers[f.id] = Answer(id=f.id, why="asked the candidate", value=ans, source="human")


def _key(f: forms.Field) -> tuple:
    if f.kind == "file":  # an upload box's caption turns into the file's name: its element id is what stays
        return (f.frame, f.kind, f.label.rsplit(" / ", 1)[-1])
    return (f.frame, f.kind, f.label[:120], f.section)


def fill_page(page: Page, ctx: dict, files: dict, model, log, record: list, page_no: int,
              env: dict | None = None, known: dict | None = None, errors: list[str] = ()) -> tuple:
    """Plan and fill every field on the current page, then read it all back. Repeats for fields that appear
    after answers (conditional questions), at most 3 rounds. `known`: this page's fields from an attempt the site
    refused; only those its errors name (or that didn't read back) are planned again, nothing is asked twice."""
    err = " ".join(errors).lower()
    done: dict[tuple, dict] = {k: e for k, e in (known or {}).items()
                               if not ((e["filled"] or e["answer"]) and (not e.get("verified")
                                                                        or e["label"][:25].lower().strip(" *") in err))}
    tie = _tie_break(model, log)
    feedback = ""
    for rnd in range(4):
        view = forms.read_page(page)
        todo = [f for f in view.fields if _key(f) not in done]
        if not todo:
            break
        forms.discover_options(page, view, log)
        try:
            answers, add = plan(todo, ctx, model, log, feedback, view.adds if rnd == 0 and not known else (),
                                [f for f in view.fields if _key(f) in done])
        except Exception as e:
            log(f"apply: planning failed ({type(e).__name__}: {str(e)[:120]})")
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
        check(todo, answers, ctx, model, log)
        apply_variants(todo, answers, ctx, log)
        enforce(todo, answers, ctx, log)
        for f in todo:  # uploads are decided by their caption, not by the model
            if f.kind == "file":
                kind = "cover_letter" if re.search(r"(?i)cover", f.label) else \
                    "resume" if re.search(r"(?i)resume|\bcv\b|upload a file", f.label) else ""
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
                     "filled": "", "error": ""}
            done[_key(f)] = entry
            if not a or not a.value or (f.value and forms.same_value(a.value, f.value, f.kind)):
                # nothing to put in, or the page already shows it (Workday's prefilled Country Phone Code)
                continue
            if f.kind == "file" and a.value not in files:
                entry["flag"] = entry["flag"] or f"no {a.value} file in the job folder"
                continue
            try:
                try:
                    entry["filled"] = forms.fill_field(page, view, f, a.value, files, tie)
                except forms.FillError:
                    raise
                except Exception:  # a click that timed out (an option list closed under it): once more
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(800)
                    entry["filled"] = forms.fill_field(page, view, f, a.value, files, tie)
                log(f"  [{f.kind}] {f.label[:60]!r} <- {entry['filled'][:60]!r} ({a.source})")
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
                ans = ask_human(env["hitl"], g, env["company"], env["host"], log, env.get("ask_s", 1800))
                entry = done.get(_key(f))
                if not ans or entry is None:
                    continue
                entry.update(answer=ans, source="human", flag="")
                try:
                    entry["filled"] = forms.fill_field(page, now_view, g, ans, files, tie)
                except Exception as e:
                    entry["error"] = f"{type(e).__name__}: {str(e)[:200]}"
                    page.keyboard.press("Escape")
    # read-back: every field as the page shows it now
    view = forms.read_page(page)
    shown = {_key(f): f for f in view.fields}
    by_label: dict[tuple, list] = {}
    for g in view.fields:
        by_label.setdefault(_key(g)[:3], []).append(g)
    for key, e in done.items():
        f = shown.get(key)
        if f is None and len(by_label.get(key[:3], [])) == 1:
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
            e["verified"] = not want  # an empty field the page then hid (To date of a current job) is fine
        elif not want:
            e["verified"] = not e["required"] or bool(f.value)
        else:
            e["verified"] = forms.same_value(want, f.value, e["kind"])
        record.append(e)
    return view, done


def _start(page: Page, url: str, ensure_signed_in, log) -> forms.PageView:
    """Open the posting and get to the application form: click Apply / Apply Manually, sign in when asked."""
    page.goto(url, wait_until="domcontentloaded")
    signed, empty = False, 0
    for _ in range(12):
        page.wait_for_timeout(4000)
        view = forms.read_page(page)
        pw = any(f.kind == "password" for f in view.fields)
        if pw and not signed:
            log("apply: the portal asks to sign in")
            ensure_signed_in(page.url)
            signed = True
            page.goto(url, wait_until="domcontentloaded")  # back to the posting, signed in
            continue
        if len([f for f in view.fields if f.kind != "file"]) >= 3 and not pw:
            return view
        for pat in START:
            if (t := forms.click_button(page, view, pat, allow_submit=True)):
                log(f"apply: clicked {t!r}")
                break
        else:
            if len(view.fields) >= 1:
                return view
            empty += 1  # still loading (clicks don't wait for the next page), or really nothing here
            if empty >= 4:
                raise RuntimeError(f"no application form and no Apply button on {page.url}")
    return forms.read_page(page)


def run(page: Page, job_dir: str | Path, company: str, ensure_signed_in=None, log=print, model=None,
        hitl=None, ask_s: float = 1800, model_name: str | None = None) -> dict:
    """Fill the application to its review screen. Never clicks Submit."""
    job_dir = Path(job_dir)
    ctx = context(job_dir, company)
    url = ctx["_jd"].get("apply_url") or ctx["_jd"].get("source_url")
    model = model or llm.writer(8000, timeout=300, model=model_name)
    files = {k: str(job_dir / f"{k}.pdf") for k in ("resume", "cover_letter") if (job_dir / f"{k}.pdf").exists()}
    out_dir = job_dir / "application"
    out_dir.mkdir(exist_ok=True)
    record: list[dict] = []
    result = {"url": url, "started": datetime.now().isoformat(timespec="seconds"), "pages": [], "state": "failed"}
    view = _start(page, url, ensure_signed_in or (lambda u: None), log)
    same = 0
    env = {"hitl": hitl, "company": company, "host": urlparse(url).netloc, "ask_s": ask_s}
    known, errors, page_no, start = None, [], 0, 0
    for _ in range(MAX_PAGES + 3):
        if known is None:  # a new page (not a retry of a refused one)
            page_no += 1
            start = len(record)
        else:
            del record[start:]  # the retry records this page's fields again
        heads = " / ".join(view.headings[:3])
        log(f"apply: page {page_no}{' (again: ' + '; '.join(errors)[:120] + ')' if known else ''}: {heads[:100]} "
            f"({len(view.fields)} fields)")
        view, done = fill_page(page, ctx, files, model, log, record, page_no, env, known, errors)
        before = [h for h in view.headings if not ERRORS_HEAD.search(h)]
        shot = out_dir / f"page-{page_no}.png"
        screenshot(page, str(shot))
        result["pages"].append({"page": page_no, "headings": view.headings[:4], "screenshot": shot.name,
                                "errors": view.errors, "buttons": view.buttons})
        if forms.click_button(page, view, forms.NEXT) is None:
            if any(forms.SUBMIT.search(b) for b in view.buttons):
                result["state"] = "review"  # only Submit is left: the review screen; the user submits (step 13)
                log("apply: reached the review screen (Submit not clicked)")
            else:
                result["state"] = "stuck"
                log(f"apply: no Next or Submit button; buttons: {view.buttons[:15]}")
            break
        for _ in range(10):  # the next page loads in steps (Workday shows a placeholder, then the new page)
            page.wait_for_timeout(3000)
            view = forms.read_page(page)
            moved = [h for h in view.headings if not ERRORS_HEAD.search(h)] != before
            if view.errors or (moved and (view.fields or any(forms.NEXT.search(b) or forms.SUBMIT.search(b)
                                                              for b in view.buttons))):
                break
        page.wait_for_timeout(1500)
        view = forms.read_page(page)
        if view.errors and [h for h in view.headings if not ERRORS_HEAD.search(h)] == before:
            # the site refused Next on this page
            same += 1
            if same >= 2:
                result["state"] = "blocked"
                log(f"apply: the page keeps refusing Next: {view.errors[:5]}")
                break
            known, errors = done, view.errors
        else:
            known, errors, same = None, [], 0
    result["fields"] = record
    result["verified"] = sum(1 for e in record if e.get("verified"))
    result["flags"] = [e for e in record if e["flag"] or e["error"] or not e.get("verified")]
    (out_dir / "application.json").write_text(json.dumps(result, indent=2))
    (out_dir / "application.md").write_text(report(result, ctx["_jd"]))
    return result


def report(result: dict, jd: dict) -> str:
    lines = [f"# Application: {jd.get('company', '')} - {jd.get('title', '')}", "",
             f"- URL: {result['url']}", f"- State: **{result['state']}** (never submitted)",
             f"- Fields: {len(result['fields'])}, read back and verified: {result['verified']}",
             f"- Flags for review: {len(result['flags'])}", "", "| Page | Field | Answer (as the page shows it) | Source | OK | Flag |",
             "|---|---|---|---|---|---|"]
    for e in result["fields"]:
        cell = lambda s: str(s).replace("|", "/").replace("\n", " ")[:120]
        lines.append(f"| {e['page']} | {cell(e['label'][:80])} | {cell(e.get('readback', ''))} | {e['source']} | "
                     f"{'yes' if e.get('verified') else '**no**'} | {cell(e['flag'] or e['error'])} |")
    return "\n".join(lines) + "\n"
