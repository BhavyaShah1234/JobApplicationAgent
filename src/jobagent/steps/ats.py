"""Step 10: the ATS loop. Score the tailored resume on online ATS checkers (plus any scores the user recorded by hand),
and while the score is below the target, work the missing keywords in (keyword pass), re-render under the same
one-page and bullet rules, and rescore. Every score is recorded in ats_runs; the cover letter is rewritten at the
end if the resume changed.

    jobagent ats-loop outputs/<company>/<job-id> <company-slug>
"""

import json
import re
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from jobagent.config import get_settings
from jobagent.state import JobDescription
from jobagent.tools import ats_sites
from jobagent.tools import projects as P
from jobagent.tools import tailor as T

TARGET, MAX_ROUNDS, MAX_KEYWORDS = 90.0, 5, 10

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
    from jobagent import llm
    try:
        out = llm.structured(SkillWords, role="fast", method="json_schema", timeout=120, max_tokens=1500).invoke(
            [SystemMessage(SKILL_FILTER),
             HumanMessage(f"Job title: {jd.title}\nWORDS: {', '.join(words)}")])
    except Exception as e:
        log(f"ats: skill filter unavailable ({type(e).__name__}); site keywords skipped")
        return []
    given = {w.lower() for w in words}
    return [w for w in out.keep if w.lower() in given and not T.is_generic(w)]


# Eligibility requirements are facts about the candidate, never something to work into a resume
ELIGIBILITY = re.compile(r"(?i)\b(months?|years?|degree|bachelor|master|ph\.?d|graduat\w*|authori[sz]\w*|sponsor\w*|"
                         r"citizen\w*|clearance|relocat\w*|gpa)\b")


def evaluate(job_dir: Path, jd: JobDescription, jd_text: str, rnd: int, sites, log=print) -> tuple[float, list[str], dict]:
    """Score the resume on every enabled online site (and include the user's manual scores for this exact resume);
    record each in ats_runs. Returns (average, missing skills from the job-specific sites, info)."""
    scores, missing = {}, []
    for s in ats_sites.score_all(job_dir / "resume.pdf", jd_text, sites, jd.title or "", log=log) if sites else []:
        ats_sites.record(job_dir, s.site, rnd, s.score, s.missing, s.to_dict())
        if s.score is not None:
            scores[s.site] = s.score
            if s.job_specific:
                new = [m for m in s.missing if m.lower() not in {x.lower() for x in missing}
                       and not ELIGIBILITY.search(m)]
                phrases = [m for m in new if len(m.split()) >= 3]   # work phrases: bullet material, not skills
                missing += phrases + skill_words([m for m in new if m not in phrases], jd, log)
    scores.update(ats_sites.manual_scores(job_dir))
    avg = round(sum(scores.values()) / len(scores), 1) if scores else 0.0
    return avg, missing, {"scores": scores}


def run(job_dir: str | Path, company_slug: str, target: float = TARGET, rounds: int = MAX_ROUNDS,
        sites=ats_sites.DEFAULT_SITES, rewrite_letter: bool = True, log=print) -> dict:
    s = get_settings()
    job_dir = Path(job_dir) if Path(job_dir).is_absolute() else Path("/app") / job_dir
    jd = JobDescription.model_validate_json((job_dir / "jd.json").read_text())
    jd_text = (job_dir / "jd.txt").read_text() if (job_dir / "jd.txt").exists() else ""
    r = T.parse((s.data_dir / "templates" / "resume.tex").read_text())
    f = T.Filled.model_validate_json((job_dir / "filled.json").read_text())
    # The writing model in function-calling mode: it places skills in the right category and works domain words into
    # bullets. (In json_schema mode it spends its whole token budget deliberating; the fast model mis-places skills.)
    keyword_model = ChatOpenAI(model="glm-5-3", base_url=s.openai_base_url, api_key=s.openai_api_key, max_retries=1,
                               timeout=400, temperature=0, max_tokens=16000).with_structured_output(
        T.KeywordPass, method="function_calling")
    history, changed = [], False
    avg, missing, info = evaluate(job_dir, jd, jd_text, 0, sites, log)
    history.append(avg)
    log(f"ats round 0: average {avg} {info['scores']}; missing {missing[:MAX_KEYWORDS]}")
    n_keywords = MAX_KEYWORDS
    for rnd in range(1, rounds + 1):
        if avg >= target or not missing:
            break
        try:
            kp = keyword_model.invoke(T.build_keyword_prompt(r, jd, f, missing[:n_keywords]))
        except Exception as e:
            n_keywords = max(3, n_keywords // 2)  # e.g. token limit: fewer keywords next time
            log(f"ats round {rnd}: model error {type(e).__name__}; next round with {n_keywords} keywords")
            continue
        if not kp.skills and not kp.bullets:
            log(f"ats round {rnd}: nothing worth adding (remaining words aren't skills)")
            break
        g = T.apply_keyword_pass(f, kp, jd_text)
        base_issues = T.duplicates(r, f) + T.quality(r, jd, f)
        base_uncovered = set(T.fill_checks(r, jd, f)[0])
        issues = [i for i in T.duplicates(r, g) + T.quality(r, jd, g) if i not in base_issues]  # new ones only
        uncovered = sorted(set(T.fill_checks(r, jd, g)[0]) - base_uncovered)
        current = (job_dir / "resume.tex").read_text()
        backup = (current, (job_dir / "resume.pdf").read_bytes())
        tex, problems, pages, err = render_keeping_projects(r, g, current, job_dir)
        if not (pages == 1 and not err and not issues and not uncovered and not any("LOCKED" in p for p in problems)):
            (job_dir / "resume.tex").write_text(backup[0]); (job_dir / "resume.pdf").write_bytes(backup[1])
            log(f"ats round {rnd}: rejected (pages={pages} {err} new issues={issues[:2]} newly uncovered={uncovered})")
            continue
        new_avg, new_missing, new_info = evaluate(job_dir, jd, jd_text, rnd, sites, log)
        added = [t for a in kp.skills for t in a.terms]
        log(f"ats round {rnd}: average {avg} -> {new_avg} {new_info['scores']}; added {added} + "
            f"{len(kp.bullets)} bullet edit(s)")
        if new_avg <= avg:  # no gain: undo
            (job_dir / "resume.tex").write_text(backup[0]); (job_dir / "resume.pdf").write_bytes(backup[1])
            missing = [m for m in missing if m not in added]
            continue
        f, avg, missing, info, changed = g, new_avg, new_missing, new_info, True
        history.append(avg)
        (job_dir / "filled.json").write_text(f.model_dump_json(indent=1))
    if changed and rewrite_letter:
        from jobagent.steps.tailor import cover_letter
        cover_letter(job_dir, company_slug, log=log)
    result = {"average": avg, "scores": info["scores"], "history": history, "missing": missing,
              "reached": avg >= target}
    log(f"ats: {' -> '.join(map(str, history))} {info['scores']} "
        f"({'target reached' if result['reached'] else 'gap ' + str(round(target - avg, 1))})")
    return result
