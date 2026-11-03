"""Step 12: fill a job application up to its review screen, never submitting it.

`jobagent apply <job_dir> <company>` opens the posting's application in the agent's browser (signing in to the
portal in the same session when it needs an account), then page by page:

1. reads every visible field (forms.read_page) and opens each combobox to learn its options;
2. one model call plans an answer for every field from the candidate's own files (personal_info, qa_bank,
   preferences, the tailored resume, the JD, the company profile), with the source of each answer and a flag for
   anything the user should look at;
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
from jobagent.tools import forms
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
    source: Literal["personal_info", "qa_bank", "preferences", "resume", "jd", "company", "written", "declared",
                    "learned", "human", "lookup", "already_filled", "none"]
    flag: str = PField("", max_length=240, description="Why the user should check this at review; '' when the "
                                                       "answer is plainly right")
    lookup: str = PField("", description="When the answer needs a lookup first (a QA_BANK entry's tools, e.g. a "
                                         "commute or an office address): 'web: <query>', 'place: <address>' or "
                                         "'maps: <origin> | <destination>'; '' otherwise")
    ask_human: bool = PField(False, description="True when the candidate's files and LEARNED answers don't answer "
                                                "this question: the candidate is asked now and the answer is kept")


class Plan(BaseModel):
    answers: list[Answer]
    add: list[int] = PField(default_factory=list, description="ADD BUTTON ids to click, one per extra entry needed "
                                                                "(e.g. a second work experience); [] when none")


PROMPT = """You fill a job application form for the candidate, from the candidate's own files only. For each field
listed (id, kind, label, options, current value) give the answer to put in it.

Rules:
- Identity, contact, links, education, work authorization, sponsorship, citizenship, EEO / self-identification:
  ONLY from PERSONAL_INFO, exactly. Never invent an address, city, ZIP, date of birth, SSN or any other personal
  fact the files don't hold: leave value '' with source none and ask_human true.
- A self-identification question PERSONAL_INFO doesn't cover (e.g. transgender, sexual orientation): choose the
  "I don't wish to answer" / "decline" option if there is one (source personal_info), else ask_human.
- A PERSONAL_INFO section may hold `agent_instructions` (how to answer that section's questions, e.g. how to state a
  start date) and `tools` (lookups allowed for it): follow them, use lookup for the tools. They never license
  inventing a personal fact the file doesn't hold (an empty address stays ask_human).
- LEARNED answers were given by the candidate on earlier forms: use one for the same question (same company: as
  is; another company: only if the question isn't specific to that company); source learned.
- A question none of the files answer (company-specific: teams, offices, "have you interviewed with us", essay
  prompts about the candidate's own choices) and that you can't write truthfully from the RESUME: ask_human true.
- A QA_BANK entry with `tools`, or a fact only a lookup can give (distance / commute to the office, an office
  address, a public fact about the company): set lookup; the result comes back to you as LOOKUP RESULTS.
- Contact email: PERSONAL_INFO contact.email, unless the form already shows the portal account's email (keep it).
  Phone: digits as in PERSONAL_INFO (choose the country / country-code option for its country code separately).
- First / last name: legal names from PERSONAL_INFO; a "preferred name" field gets preferred_first.
- Sponsorship / authorization questions: answer as PERSONAL_INFO says. If the question's own wording counts the
  candidate's visa status (e.g. F-1 OPT / STEM OPT) as needing sponsorship, still answer from PERSONAL_INFO but flag
  "sponsorship wording includes OPT; you hold <visa_status>".
- Screening questions: QA_BANK first (follow its agent_instructions), then PREFERENCES, then the RESUME and
  DECLARED facts (years of experience up to the declared total). "Have you worked at <company> before?": no unless
  the resume lists that employer. Graduation date, GPA, degree, school: from PERSONAL_INFO education (the latest
  degree unless the question says otherwise; DATE_FACTS says whether it is already past); if PERSONAL_INFO and the
  RESUME disagree, use PERSONAL_INFO and flag it. The posting's own target dates never change the candidate's facts.
  For an option list of ranges, pick the range containing the true value.
- Open questions ("Why do you want to work here?", cover-letter text boxes, short behavioural questions): write a
  truthful answer from the RESUME and COMPANY profile, specific and plain, within the length asked (default
  80-150 words), no clichés; source written.
- Choice fields: the value must be one of the listed options, copied exactly; when the true value isn't offered
  (a field of study, a degree name), choose the closest option and flag "closest option to <true value>". A combobox whose options are
  categories (Workday "How did you hear": "Job Board", "Social Media", "<Company> Source"): answer
  'Category > what you mean' (e.g. '<Company> Source > company careers website'); the entry is picked inside it. A combobox listed without options is a
  search box: give the full name to search for (e.g. the school's full name).
- "How did you hear": per QA_BANK. Referral fields: only the REFERRAL given below, else ''.
- Consent / acknowledgement checkboxes or options required to apply (privacy notice, data processing, terms):
  agree. Marketing, SMS / WhatsApp messages, talent-community newsletters: decline (no / unchecked) unless required.
- Location preference questions ("which locations would you work in", "open to relocation"): from PREFERENCES
  (open to relocating anywhere: choose all US locations offered). A current-address "Location (City)" field is the
  candidate's own city from PERSONAL_INFO, never the job's.
- A field marked MATCHES has a QA_BANK / LEARNED entry for that question: follow it (answer and instructions).
- Work history / education sections: one entry per role in RESUME_ROLES, all of them, in that order (job title, company, location, dates, the
  role's bullets as its description; "currently work here" only for a role the resume shows as Present) and per
  degree in PERSONAL_INFO education. When more entries are needed than the form shows, put the matching ADD BUTTON
  id in `add` once per missing entry (3 roles, 1 entry shown -> [id, id]) and leave those entries for the next
  round (their fields appear then). Never add an entry for something the files don't hold (no certifications,
  languages or awards in PERSONAL_INFO -> add nothing there).
- date_parts fields: the value in the field's format (MM/YYYY -> '08/2024', YYYY -> '2024'). A role the resume
  shows as Present: tick its "I currently work here" box ('checked') and leave its To date ''.
- A skills search box: the RESUME's skills most relevant to the JD, '; '-separated (at most 15).
- File fields: value 'resume' for a resume / CV upload, 'cover_letter' for a cover letter upload, '' otherwise.
- A field with a current value that is already right: repeat it with source already_filled. Wrong: correct it.
- Answer every listed field id exactly once."""


def _load(name: str) -> str:
    p = DATA / name
    return p.read_text() if p.exists() else ""


def context(job_dir: Path, company: str) -> dict:
    """The candidate's files, as the planner sees them."""
    resume = ""
    if (job_dir / "resume.pdf").exists():
        resume = subprocess.run(["pdftotext", "-layout", str(job_dir / "resume.pdf"), "-"], capture_output=True,
                                text=True).stdout
    profile = yaml.safe_load(_load("master_profile.yaml")) or {}
    declared = {k: v for k, v in (profile.get("claims") or {}).items()} if isinstance(profile.get("claims"), dict) else {}
    jd = json.loads((job_dir / "jd.json").read_text()) if (job_dir / "jd.json").exists() else {}
    comp = Path(f"/app/state/companies/{company}.md")
    return {
        "PERSONAL_INFO": _load("personal_info.yaml"),
        "QA_BANK": _load("qa_bank.yaml"),
        "PREFERENCES": _load("preferences.yaml"),
        "DECLARED": json.dumps(declared) or "{}",
        "RESUME": resume[:6000],
        "JD": ((job_dir / "jd.md").read_text() if (job_dir / "jd.md").exists() else "")[:5000],
        "COMPANY": (comp.read_text() if comp.exists() else "")[:3000],
        "LEARNED": _load("learned_answers.yaml") or "(none yet)",
        "REFERRAL": "none (no referral for this job: never answer 'Referral' / 'Employee referral')",
        "RESUME_ROLES": resume_roles(job_dir),
        "TODAY": now().strftime("%Y-%m-%d"),
        "DATE_FACTS": date_facts(yaml.safe_load(_load("personal_info.yaml")) or {}),
        "_jd": jd,
    }


def _has_data(ctx: dict, section: str) -> bool:
    """personal_info holds entries for this section (certifications, languages, ...)."""
    info = yaml.safe_load(ctx.get("PERSONAL_INFO") or "") or {}
    key = re.sub(r"[^a-z]", "", section.lower())
    return any(v for k, v in info.items() if isinstance(v, list) and re.sub(r"[^a-z]", "", k.lower())[:6] in key)


def resume_roles(job_dir: Path) -> str:
    """The resume's Experience entries (title, dates, company, location), one per line: a form's work history gets
    exactly these."""
    tex = (job_dir / "resume.tex").read_text() if (job_dir / "resume.tex").exists() else ""
    sec = re.search(r"\\section\{Experience\}(.*?)(\\section\{|\\end\{document\})", tex, re.S)
    if not sec:
        return "(none)"
    clean = lambda t: re.sub(r"\\underline\{(.*?)\}", r"\1", t).replace("\\&", "&").replace("--", "-").strip()
    roles = re.findall(r"\\resumeSubheading\s*\{(.*?)\}\{(.*?)\}\s*\{(.*?)\}\{(.*?)\}", sec.group(1), re.S)
    return "\n".join(f"{i}. {clean(t)} | {clean(c)} | {clean(l)} | {clean(d)}" for i, (t, d, c, l) in enumerate(roles, 1)) \
        or "(none)"


def hints(fields: list[forms.Field]) -> dict[int, str]:
    """For each field, the QA_BANK entry or LEARNED answer whose question it asks (fuzzy match on the wording), so
    the planner sees it next to the field instead of having to find it."""
    bank = []
    for name in ("qa_bank.yaml", "learned_answers.yaml"):
        for e in (yaml.safe_load(_load(name)) or []) if _load(name) else []:
            if isinstance(e, dict) and e.get("question"):
                bank.append((name.split(".")[0], e, [e["question"], *(e.get("aliases") or [])]))
    out = {}
    for f in fields:
        label = re.sub(r"[*✱]", "", f.label)[:200]
        best = max(((fuzz.token_set_ratio(label.lower(), q.lower()), src, e) for src, e, qs in bank for q in qs),
                   default=(0, "", {}), key=lambda x: x[0])
        if best[0] >= 85:
            e = best[2]
            out[f.id] = (f"{best[1]}: answer={e.get('answer')!r}"
                         + (f" instructions={e.get('agent_instructions')!r}" if e.get("agent_instructions") else "")
                         + (f" company={e.get('company')}" if e.get("company") else ""))
    return out


def date_facts(info: dict) -> str:
    """Dates worked out by code, so the model compares facts instead of guessing (a graduation already past)."""
    lines, today = [], now().date()
    for ed in info.get("education") or []:
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
    h = hints(fields)
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
gave, a "how did you hear" that differs from QA_BANK, an option chosen while QA_BANK names another that is offered).
For unsupported answers give the value the files do support (QA_BANK 'Company careers page' means the company's own
website option), or ask_human when none does. Written answers (source written) are supported when true to the
RESUME. Rules are support too: agreeing to a consent / terms / acknowledgement needed to apply, declining marketing
and SMS, choosing "I don't wish to answer" for self-identification PERSONAL_INFO doesn't cover, and QA_BANK
instructions. PERSONAL_INFO wins over the RESUME where they disagree (never swap one for the other: that is flagged, not
corrected). A corrected value for a choice field must be one of its listed options, copied exactly. Return one check
per planned answer."""


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
    for c in out.checks:
        a = answers.get(c.id)
        if a is None or c.supported:
            continue
        f = next((x for x in fields if x.id == c.id), None)
        if a.source == "personal_info":
            # credited to the candidate's own file but not in it (or in conflict with the resume): theirs to say
            log(f"  check: [{c.id}] {a.value[:40]!r} isn't in personal_info as planned; asking the candidate")
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
ADDRESS = re.compile(r"(?i)\baddress\b|\bstreet\b|^\s*city\b|\bstate\b|province|postal|\bzip\b|county|"
                     r"current location|location \(city\)")
LEARNED = DATA / "learned_answers.yaml"
LEARNED_HEAD = """# Answers you gave the agent on the spot (step 12), kept so the same question isn't asked again.
# The agent appends here; edit or delete entries freely. `company: any` makes an answer apply everywhere.
"""


def learn(question: str, options: list[str], answer: str, company: str, host: str) -> None:
    """Keep the candidate's answer to a question for the next form that asks it."""
    entries = (yaml.safe_load(LEARNED.read_text()) if LEARNED.exists() else None) or []
    entries = [e for e in entries if not (e.get("question") == question and e.get("company") == company)]
    entries.append({"question": question, "options": options[:30] or None, "answer": answer, "company": company,
                    "portal": host, "date": now().strftime("%Y-%m-%d")})
    LEARNED.write_text(LEARNED_HEAD + yaml.safe_dump(entries, sort_keys=False, allow_unicode=True, width=120))


def ask_human(hitl, f: forms.Field, company: str, host: str, log, timeout_s: float = 1800) -> str:
    """Ask the candidate one question now (answer in `jobagent hitl`); the answer is kept in learned_answers.yaml."""
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
        # the address belongs in personal_info.yaml (never in learned answers): one request for all its fields
        from jobagent.tools.hitl import HitlTimeout
        try:
            ans = env["hitl"].ask(f"{env['company']} application needs your address ({', '.join(f.label[:30] for f in addr)}). "
                                  "Fill `address` in data/personal_info.yaml, then answer 'done' ('skip' leaves it "
                                  "for review).", timeout_s=env.get("ask_s", 1800)).strip().lower()
        except HitlTimeout:
            ans = ""
        if ans == "done":
            ctx["PERSONAL_INFO"] = _load("personal_info.yaml")
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
        for i in add[:8]:  # more entries (a second job, a second degree): their blank fields come next round
            a = next((x for x in view.adds if x["id"] == i), None)
            if a and not ENTRY_OK.search(a["section"] + " " + a["text"]) and not _has_data(ctx, a["section"]):
                log(f"  not adding an entry under {a['section'][:40]!r}: nothing for it in personal_info")
                continue
            if a:
                view.frames[a["frame"]].locator(f'[data-jf-add="{i}"]').first.click(timeout=8000)
                page.wait_for_timeout(1500)
                log(f"  added an entry: {a['text']} ({a['section'][:40]})")
        check(todo, answers, ctx, model, log)
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
            if not a or not a.value or a.source == "already_filled" and forms.same_value(a.value, f.value, f.kind):
                continue
            if f.kind == "file" and a.value not in files:
                entry["flag"] = entry["flag"] or f"no {a.value} file in the job folder"
                continue
            try:
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
    for key, e in done.items():
        f = shown.get(key)
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
    signed = False
    for _ in range(8):
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
