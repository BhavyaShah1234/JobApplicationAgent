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
  degree unless the question says otherwise); if PERSONAL_INFO and the RESUME disagree, use PERSONAL_INFO and flag it.
  For an option list of ranges, pick the range containing the true value.
- Open questions ("Why do you want to work here?", cover-letter text boxes, short behavioural questions): write a
  truthful answer from the RESUME and COMPANY profile, specific and plain, within the length asked (default
  80-150 words), no clichés; source written.
- Choice fields: the value must be one of the listed options, copied exactly. A combobox listed without options is a
  search box: give the full name to search for (e.g. the school's full name).
- "How did you hear": per QA_BANK. Referral fields: only the REFERRAL given below, else ''.
- Consent / acknowledgement checkboxes or options required to apply (privacy notice, data processing, terms):
  agree. Marketing, SMS / WhatsApp messages, talent-community newsletters: decline (no / unchecked) unless required.
- Location preference questions ("which locations would you work in", "open to relocation"): from PREFERENCES
  (open to relocating anywhere: choose all US locations offered). A current-address "Location (City)" field is the
  candidate's own city from PERSONAL_INFO, never the job's.
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
        "REFERRAL": "none",
        "TODAY": now().strftime("%Y-%m-%d"),
        "_jd": jd,
    }


def plan(fields: list[forms.Field], ctx: dict, model, log=print, feedback: str = "") -> dict[int, Answer]:
    files = "\n\n".join(f"== {k} ==\n{v}" for k, v in ctx.items() if not k.startswith("_"))
    msgs = [SystemMessage(PROMPT),
            HumanMessage(f"{files}\n\n== FORM FIELDS ==\n" + "\n".join(f.brief() for f in fields)
                         + (f"\n\n== FEEDBACK ==\n{feedback}" if feedback else ""))]
    out = llm.structured_invoke(model, Plan, msgs, first="function_calling", log=log)
    return {a.id: a for a in out.answers}


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


def resolve(todo: list[forms.Field], answers: dict, ctx: dict, model, env: dict, log) -> None:
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
            again = plan(need, ctx, model, log, "LOOKUP RESULTS:\n" + "\n".join(results)
                         + "\nGive the final answers now (lookup '').")
            for i, a in again.items():
                answers[i] = a.model_copy(update={"lookup": "", "source": a.source if a.source != "none" else "lookup"})
        except Exception as e:
            log(f"apply: planning after lookups failed ({type(e).__name__})")
    for f in todo:
        a = answers.get(f.id)
        if f.kind == "file" or env.get("hitl") is None:
            continue
        if (a and a.ask_human) or (f.required and not f.value and (a is None or not a.value)):
            ans = ask_human(env["hitl"], f, env["company"], env["host"], log)
            if ans:
                answers[f.id] = Answer(id=f.id, why="asked the candidate", value=ans, source="human")


def _key(f: forms.Field) -> tuple:
    return (f.frame, f.kind, f.label[:120], f.section)


def fill_page(page: Page, ctx: dict, files: dict, model, log, record: list, page_no: int,
              env: dict | None = None) -> forms.PageView:
    """Plan and fill every field on the current page, then read it all back. Repeats for fields that appear
    after answers (conditional questions), at most 3 rounds."""
    done: dict[tuple, dict] = {}
    tie = _tie_break(model, log)
    feedback = ""
    for rnd in range(3):
        view = forms.read_page(page)
        todo = [f for f in view.fields if _key(f) not in done]
        if not todo:
            break
        forms.discover_options(page, view, log)
        try:
            answers = plan(todo, ctx, model, log, feedback)
        except Exception as e:
            log(f"apply: planning failed ({type(e).__name__}: {str(e)[:120]})")
            break
        resolve(todo, answers, ctx, model, env or {}, log)
        for f in todo:
            a = answers.get(f.id)
            entry = {"page": page_no, "section": f.section, "label": f.label, "kind": f.kind, "required": f.required,
                     "options": f.options[:60], "before": f.value, "answer": a.value if a else "",
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
                log(f"  [{f.kind}] {f.label[:60]!r}: fill failed: {entry['error'][:160]}")
        page.wait_for_timeout(1200)
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
            e["verified"] = False
        elif not want:
            e["verified"] = not e["required"] or bool(f.value)
        else:
            e["verified"] = forms.same_value(want, f.value, e["kind"])
        record.append(e)
    return view


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
        hitl=None) -> dict:
    """Fill the application to its review screen. Never clicks Submit."""
    job_dir = Path(job_dir)
    ctx = context(job_dir, company)
    url = ctx["_jd"].get("apply_url") or ctx["_jd"].get("source_url")
    model = model or llm.writer(8000, timeout=300)
    files = {k: str(job_dir / f"{k}.pdf") for k in ("resume", "cover_letter") if (job_dir / f"{k}.pdf").exists()}
    out_dir = job_dir / "application"
    out_dir.mkdir(exist_ok=True)
    record: list[dict] = []
    result = {"url": url, "started": datetime.now().isoformat(timespec="seconds"), "pages": [], "state": "failed"}
    view = _start(page, url, ensure_signed_in or (lambda u: None), log)
    last_heads, same = None, 0
    for page_no in range(1, MAX_PAGES + 1):
        heads = " / ".join(view.headings[:3])
        log(f"apply: page {page_no}: {heads[:100]} ({len(view.fields)} fields)")
        view = fill_page(page, ctx, files, model, log, record, page_no,
                         {"hitl": hitl, "company": company, "host": urlparse(url).netloc})
        shot = out_dir / f"page-{page_no}.png"
        screenshot(page, str(shot))
        result["pages"].append({"page": page_no, "headings": view.headings[:4], "screenshot": shot.name,
                                "errors": view.errors})
        if forms.click_button(page, view, forms.NEXT) is None:
            if any(forms.SUBMIT.search(b) for b in view.buttons):
                result["state"] = "review"  # only Submit is left: the review screen; the user submits (step 13)
                log("apply: reached the review screen (Submit not clicked)")
            else:
                result["state"] = "stuck"
                log(f"apply: no Next or Submit button; buttons: {view.buttons[:15]}")
            break
        page.wait_for_timeout(5000)
        view = forms.read_page(page)
        if view.errors and view.headings == last_heads:
            same += 1
            if same >= 2:
                result["state"] = "blocked"
                log(f"apply: the page keeps refusing Next: {view.errors[:5]}")
                break
        last_heads = view.headings
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
