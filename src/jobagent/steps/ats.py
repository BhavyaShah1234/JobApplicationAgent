"""Step 10: the ATS loop. Score the tailored resume on online ATS checkers (plus any scores the user recorded by hand),
and while the score is below the target, work the missing keywords in (keyword pass), re-render under the same
one-page and bullet rules, and rescore. Every score is recorded in ats_runs; the cover letter is rewritten at the
end if the resume changed.

    jobagent ats-loop outputs/<company>/<job-id> <company-slug>
"""

import json
import re
import subprocess
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from jobagent import llm
from jobagent.config import get_settings
from jobagent.state import JobDescription
from jobagent.tools import ats_sites, layout
from jobagent.tools import projects as P
from jobagent.tools import tailor as T

TARGET, MAX_ROUNDS, MAX_KEYWORDS = 90.0, 5, 10
# Run-to-run spread of the averaged unlimited-site score on an unchanged resume; a change that adds several of the
# budget sites' keywords is kept unless the average falls by more than this
NOISE = 2.0

def render_keeping_projects(r: T.Resume, f: T.Filled, current_tex: str, job_dir: Path) -> tuple[str, list[str], int, str]:
    """The resume for `f`. With projects.json (saved by `jobagent tailor`) the Projects section is re-fitted: the
    current projects first, then shorter or fewer ones if the new content needs the room; without it, the current
    Projects section is kept verbatim. Compiled (squeeze-aware)."""
    tex, problems = T.apply_fill(r, f)
    path = job_dir / "projects.json"
    if path.exists():
        st = json.loads(path.read_text())
        fitted = P.fit(tex, job_dir, lambda t: T.compile_pdf(t, job_dir), start=(st["kept"], len(st["short"])))
        if fitted:
            tex, pages, err, kept, short = fitted
            if pages == 1 and not err and (kept, sorted(short)) != (st["kept"], st["short"]):
                P.update_state(job_dir, kept, short)
            return tex, problems, pages, err
    old, new = P.projects_span(current_tex), P.projects_span(tex)
    if old and new:
        tex = tex[:new[0]] + current_tex[old[0]:old[1]] + tex[new[1]:]
    return (tex, problems, *T.compile_pdf(tex, job_dir))


class SkillWords(BaseModel):
    keep: list[str] = Field(default_factory=list, max_length=15,
                            description="The subset of WORDS that are skills, copied exactly; nothing else")


SKILL_FILTER = """ATS checkers list "missing keywords" by word overlap, so most are plain English or job-page boilerplate
(salary, location, posted, employment, people, world, the company's name) or generic verbs and nouns (build, product,
tools, information, value, problems, design, works, management). Return ONLY the words that a resume's
skills or bullets could truthfully contain as a skill, tool, language, framework, platform, method or technical
concept for this job (e.g. aws, rust, vue, excel, ux, ci/cd, compliance, security). `keep` must be a subset of
WORDS, copied exactly; never add words that are not in WORDS. /no_think"""


def skill_words(words: list[str], jd: JobDescription, log=print) -> list[str]:
    """The site's missing keywords that are real skills (fast model); [] if the model is unavailable."""
    if not words:
        return []
    try:
        out = llm.structured(SkillWords, role="fast", method="json_schema", timeout=120, max_tokens=1500).invoke(
            [SystemMessage(SKILL_FILTER),
             HumanMessage(f"Job title: {jd.title}\nWORDS: {', '.join(words)}")])
    except Exception as e:
        log(f"ats: skill filter unavailable ({type(e).__name__}); site keywords skipped")
        return []
    given = {w.lower() for w in words}
    return [w for w in out.keep if w.lower() in given and not T.is_generic(w)]


# Never worked into a resume: credentials and status the candidate can't take on per posting, and location (the user
# is open to relocating and doesn't want proximity screening). Years and domain experience ARE worked in, up to the
# declared total (data/master_profile.yaml `claims`).
ELIGIBILITY = re.compile(r"(?i)\b(authori[sz]\w*|sponsor\w*|citizen\w*|clearance|relocat\w*|gpa|ph\.?d|doctor\w*|"
                         r"certifi\w*|licen[cs]\w*|located|city|zip|address|on-?site|hybrid|commut\w*)\b")
# Sites whose "missing keywords" are word overlap with the posting: only the real skills among them are used
WORD_OVERLAP_SITES = {"autoapplymax"}
# Words a posting uses for whoever applies: never written into a resume, whatever a checker says
APPLICANT_WORDS = re.compile(r"(?i)^(applicants?|candidates?|people|persons?|individuals?|you|your|yourself|we|us|"
                             r"our|employees?|hires?|talent|job ?seekers?|employment|equal|opportunity)$")


def evaluate(job_dir: Path, jd: JobDescription, jd_text: str, rnd: int, sites, log=print) -> dict:
    """Score the resume on `sites` and record each in ats_runs. Returns {"scores", "missing" (per site: keywords and
    phrases to work in), "advice" (the sites' suggestions), "template" (advice about locked template parts)}."""
    out = {"scores": {}, "missing": {}, "advice": [], "template": [], "keep": []}
    for s in ats_sites.score_all(job_dir / "resume.pdf", jd_text, sites, jd.title or "", log=log) if sites else []:
        ats_sites.record(job_dir, s.site, rnd, s.score, s.missing, s.to_dict())
        if s.score is None:
            continue
        out["scores"][s.site] = s.score
        out["advice"] += [a for a in s.advice if not ELIGIBILITY.search(a)]
        out["template"] += s.breakdown.get("template", [])
        out["keep"] += [k for k in s.breakdown.get("matched", []) if not T.is_generic(k)]
        if s.job_specific:
            new = [m for m in s.missing if not ELIGIBILITY.search(m)]
            if s.site in WORD_OVERLAP_SITES:
                # Every word but the ones naming applicants: work phrases first, then skills, then other non-generic
                # words (finance, compliance, product) for the Summary, bullets or projects
                new = [m for m in new if not APPLICANT_WORDS.match(m.strip())]
                phrases = [m for m in new if len(m.split()) >= 3]
                rest = [m for m in new if m not in phrases]
                skills = skill_words(rest, jd, log)
                new = phrases + skills + [m for m in rest if m not in skills]
            out["missing"][s.site] = new
    return out


def average(scores: dict) -> float:
    return round(sum(scores.values()) / len(scores), 1) if scores else 0.0


def merged_missing(missing: dict) -> list[str]:
    """Every site's missing items once, phrases from the curated lists first (they name the posting's work)."""
    seen, out = set(), []
    for site in sorted(missing, key=lambda x: x in WORD_OVERLAP_SITES):
        for m in missing[site]:
            if m.lower() not in seen:
                seen.add(m.lower())
                out.append(m)
    return out


def project_paragraphs(state: str | None) -> list[str]:
    """The projects on the page (projects.json `kept`), as "title: paragraph" for the advice pass."""
    if not state:
        return []
    st = json.loads(state)
    return [f"{e['title']}: {e['paragraph']}" for e in st["ranked"][:st["kept"]]]


def apply_project_fixes(state: str | None, fixes: list) -> str | None:
    """projects.json text with the advice pass's rewritten paragraphs (only for projects on the page; an empty or
    one-line rewrite is ignored)."""
    if not state or not fixes:
        return state
    st = json.loads(state)
    for fx in fixes:
        if 1 <= fx.project <= st["kept"] and len(fx.paragraph.strip()) >= 200:
            st["ranked"][fx.project - 1]["paragraph"] = T.no_dashes(fx.paragraph.strip())
    return json.dumps(st, indent=1)


def resume_text(f: T.Filled) -> str:
    """Everything the advice pass can change, for spotting an answer that changes nothing."""
    return "\n".join([f.summary] + [b.text for e in f.roles for b in e.bullets] +
                     [t for line in f.skills for t in line.render()])


def present(job_dir: Path, phrases: list[str]) -> int:
    """How many of `phrases` the resume's text contains (case-insensitive), the stand-in score for budget sites
    between their scans."""
    text = " ".join(subprocess.run(["pdftotext", str(job_dir / "resume.pdf"), "-"], capture_output=True,
                                   text=True).stdout.lower().split())
    return sum(p.lower() in text for p in phrases)


def run(job_dir: str | Path, company_slug: str, target: float = TARGET, rounds: int = MAX_ROUNDS,
        sites=ats_sites.DEFAULT_SITES, rewrite_letter: bool = True, log=print) -> dict:
    """Round 0 scores every site; rounds 1..n rescore only the unlimited sites (budget sites keep their round-0
    missing keywords and advice), and the budget sites are scanned once more on the final resume if it changed."""
    s = get_settings()
    job_dir = Path(job_dir) if Path(job_dir).is_absolute() else Path("/app") / job_dir
    jd = JobDescription.model_validate_json((job_dir / "jd.json").read_text())
    jd_text = (job_dir / "jd.txt").read_text() if (job_dir / "jd.txt").exists() else ""
    r = T.parse((s.data_dir / "templates" / "resume.tex").read_text())
    f = T.Filled.model_validate_json((job_dir / "filled.json").read_text())
    # The writing model in function-calling mode: it places skills in the right category and works domain words into
    # bullets. (In json_schema mode it spends its whole token budget deliberating; the fast model mis-places skills.)
    keyword_model = llm.writer(16000, timeout=400)
    every = tuple(x for x in sites if x not in ats_sites.BUDGET_SITES)
    budget = tuple(x for x in sites if x in ats_sites.BUDGET_SITES)
    ev = evaluate(job_dir, jd, jd_text, 0, sites, log)
    scores = {**ev["scores"], **ats_sites.manual_scores(job_dir)}
    budget_missing = [m for site in budget for m in ev["missing"].get(site, [])]
    budget_advice = [a for a in ev["advice"] if a.startswith("Show these soft skills")] if budget else []
    missing, advice, template = merged_missing(ev["missing"]), ev["advice"], ev["template"]
    keep = list(dict.fromkeys(ev["keep"]))
    loop_avg = average({k: v for k, v in scores.items() if k in every})
    covered = present(job_dir, budget_missing)
    history, changed = [average(scores)], False
    log(f"ats round 0: average {history[0]} {scores}; missing {missing[:MAX_KEYWORDS]}; advice {len(advice)}")
    for t in dict.fromkeys(template):
        log(f"ats: template advice (not applied; locked or eligibility): {t}")
    n_keywords, last = MAX_KEYWORDS, ""
    base_layout = len(layout.check(job_dir / "resume.pdf").issues)  # the change must not make the page read worse
    for rnd in range(1, rounds + 1):
        if average(scores) >= target or not (missing or advice):
            break
        proj_path = job_dir / "projects.json"
        proj_before = proj_path.read_text() if proj_path.exists() else None
        try:
            prompt = T.build_keyword_prompt(r, jd, f, missing[:n_keywords], advice, keep, last,
                                            project_paragraphs(proj_before))
            kp = llm.structured_invoke(keyword_model, T.KeywordPass, prompt, first="function_calling", log=log)
        except Exception as e:
            n_keywords = max(3, n_keywords // 2)  # e.g. token limit: fewer keywords next time
            log(f"ats round {rnd}: model error {type(e).__name__}; next round with {n_keywords} keywords")
            continue
        if not kp.skills and not kp.bullets and not kp.summary and not kp.projects:
            log(f"ats round {rnd}: nothing worth changing (remaining words aren't skills)")
            break
        g = T.apply_keyword_pass(f, kp, jd_text)
        proposed = [t for a in kp.skills for t in a.terms]
        proj_after = apply_project_fixes(proj_before, kp.projects)
        if resume_text(g) == resume_text(f) and proj_after == proj_before:  # nothing new: don't spend a scoring round
            last = (f"your answer changed nothing ({', '.join(proposed) or 'no new text'} already on the resume); "
                    "put the MISSING items into bullets and the Summary instead")
            log(f"ats round {rnd}: no change ({', '.join(proposed)} already present); not scored")
            continue
        base_issues = T.duplicates(r, f) + T.quality(r, jd, f)
        base_uncovered = set(T.fill_checks(r, jd, f)[0])
        issues = [i for i in T.duplicates(r, g) + T.quality(r, jd, g) if i not in base_issues]  # new ones only
        uncovered = sorted(set(T.fill_checks(r, jd, g)[0]) - base_uncovered)
        current = (job_dir / "resume.tex").read_text()
        backup = (current, (job_dir / "resume.pdf").read_bytes())
        tried = missing[:n_keywords]
        missing = missing[n_keywords:] + tried  # a rejected round tries the other keywords first next time
        if proj_after != proj_before:
            proj_path.write_text(proj_after)
        undo = lambda: ((job_dir / "resume.tex").write_text(backup[0]), (job_dir / "resume.pdf").write_bytes(backup[1]),
                        proj_before is not None and proj_path.write_text(proj_before))
        tex, problems, pages, err = render_keeping_projects(r, g, current, job_dir)
        new_layout = layout.check(job_dir / "resume.pdf").issues if pages == 1 and not err else []
        if len(new_layout) > base_layout:
            issues = issues + [f"the page reads worse: {new_layout[-1]}"]
        if not (pages == 1 and not err and not issues and not uncovered and not any("LOCKED" in p for p in problems)):
            undo()
            log(f"ats round {rnd}: rejected (pages={pages} {err} new issues={issues[:2]} newly uncovered={uncovered})")
            last = (f"undone before scoring: " + ("the page overflowed; make lines shorter, not longer" if pages != 1
                    else f"it broke resume rules: {'; '.join((issues + uncovered)[:3])}"))
            continue
        ev = evaluate(job_dir, jd, jd_text, rnd, every, log)
        new_scores = {**scores, **ev["scores"]}
        new_loop = average({k: v for k, v in new_scores.items() if k in every})
        new_covered = present(job_dir, budget_missing)
        added = [t for a in kp.skills for t in a.terms]
        log(f"ats round {rnd}: unlimited-site average {loop_avg} -> {new_loop} {ev['scores']}; budget keywords "
            f"{covered} -> {new_covered}/{len(budget_missing)}; added {added} + {len(kp.bullets)} bullet edit(s)"
            f"{' + summary' if kp.summary else ''}")
        # Keep a change if the unlimited sites rose, or if it works in at least 2 more of the budget sites' keywords
        # without the unlimited sites falling beyond their run-to-run noise
        change = (f"added skills {added}, {len(kp.bullets)} bullet edit(s), {len(kp.projects)} project edit(s)" +
                  (", a new Summary" if kp.summary else ""))
        if not (new_loop > loop_avg or (new_loop >= loop_avg - NOISE and new_covered >= covered + 2)):  # undo
            undo()
            last = f"UNDONE: {change} moved the scores {ev['scores']} (average {loop_avg} -> {new_loop})"
            continue
        last = f"KEPT: {change} raised the average {loop_avg} -> {new_loop} {ev['scores']}"
        f, scores, loop_avg, covered, changed = g, new_scores, new_loop, new_covered, True
        base_layout = len(new_layout)
        missing = [m for m in merged_missing(ev["missing"]) + budget_missing if m.lower() not in
                   {x.lower() for x in added}]
        missing = list(dict.fromkeys(missing))
        advice = ev["advice"] + budget_advice
        keep = list(dict.fromkeys(keep + ev["keep"]))
        history.append(average(scores))
        (job_dir / "filled.json").write_text(f.model_dump_json(indent=1))
    if changed and budget:  # the budget sites' score for the final resume (if scans are left)
        ev = evaluate(job_dir, jd, jd_text, rounds + 1, budget, log)
        stale = [x for x in budget if x not in ev["scores"]]
        scores = {**{k: v for k, v in scores.items() if k not in stale}, **ev["scores"]}
        if stale:
            log(f"ats: {stale} could not rescore the final resume; left out of the final average")
        history.append(average(scores))
    if changed and rewrite_letter:
        from jobagent.steps.tailor import cover_letter
        cover_letter(job_dir, company_slug, log=log)
    avg = average(scores)
    result = {"average": avg, "scores": scores, "history": history, "missing": missing,
              "template_advice": list(dict.fromkeys(template)), "reached": avg >= target}
    log(f"ats: {' -> '.join(map(str, history))} {scores} "
        f"({'target reached' if result['reached'] else 'gap ' + str(round(target - avg, 1))})")
    return result
