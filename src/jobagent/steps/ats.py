"""Step 10: the ATS loop. Score the tailored resume (local scorer; external sites when their adapters are enabled),
and while the score is below the target, work the missing keywords in (keyword pass), re-render under the same
one-page and bullet rules, and rescore. Every score is recorded in ats_runs; the cover letter is rewritten at the
end if the resume changed.

    jobagent ats-loop outputs/<company>/<job-id> <company-slug>
"""

from pathlib import Path

from langchain_openai import ChatOpenAI

from jobagent.config import get_settings
from jobagent.state import JobDescription
from jobagent.tools import ats_local
from jobagent.tools import projects as P
from jobagent.tools import tailor as T

TARGET, MAX_ROUNDS, MAX_KEYWORDS = 90.0, 5, 10


def render_keeping_projects(r: T.Resume, f: T.Filled, current_tex: str, job_dir: Path) -> tuple[str, list[str], int, str]:
    """The resume for `f` with the current Projects section kept verbatim; compiled (squeeze-aware)."""
    tex, problems = T.apply_fill(r, f)
    old, new = P.projects_span(current_tex), P.projects_span(tex)
    if old and new:
        tex = tex[:new[0]] + current_tex[old[0]:old[1]] + tex[new[1]:]
    return (tex, problems, *T.compile_pdf(tex, job_dir))


def run(job_dir: str | Path, company_slug: str, target: float = TARGET, rounds: int = MAX_ROUNDS, log=print) -> dict:
    s = get_settings()
    job_dir = Path(job_dir) if Path(job_dir).is_absolute() else Path("/app") / job_dir
    jd = JobDescription.model_validate_json((job_dir / "jd.json").read_text())
    jd_text = (job_dir / "jd.txt").read_text() if (job_dir / "jd.txt").exists() else ""
    r = T.parse((s.data_dir / "templates" / "resume.tex").read_text())
    f = T.Filled.model_validate_json((job_dir / "filled.json").read_text())
    model = ChatOpenAI(model="glm-5-3", base_url=s.openai_base_url, api_key=s.openai_api_key, max_retries=1,
                       timeout=600, temperature=0, max_tokens=16000)
    history, changed = [], False
    score = ats_local.score(job_dir / "resume.pdf", jd, jd_text)
    ats_local.record(job_dir, "local", 0, score.score, score.missing, score.to_dict())
    history.append(score.score)
    log(f"ats round 0: local {score.score} (keywords {score.keyword_pct}%), missing {score.missing[:MAX_KEYWORDS]}")
    if score.parse_issues:  # e.g. built before a template fix (header icons): a plain re-render may fix it
        current = (job_dir / "resume.tex").read_text()
        backup = (current, (job_dir / "resume.pdf").read_bytes())
        tex, problems, pages, err = render_keeping_projects(r, f, current, job_dir)
        new = ats_local.score(job_dir / "resume.pdf", jd, jd_text) if pages == 1 and not err else None
        if new and new.score > score.score and not any("LOCKED" in p for p in problems):
            log(f"ats: re-rendered with the current template: {score.score} -> {new.score} "
                f"(parse issues {score.parse_issues} -> {new.parse_issues})")
            ats_local.record(job_dir, "local", 0, new.score, new.missing, new.to_dict())
            score, changed = new, True
            history.append(score.score)
        else:
            (job_dir / "resume.tex").write_text(backup[0])
            (job_dir / "resume.pdf").write_bytes(backup[1])
    for rnd in range(1, rounds + 1):
        if score.score >= target or not score.missing:
            break
        missing = score.missing[:MAX_KEYWORDS]
        try:
            kp = model.with_structured_output(T.KeywordPass, method="json_schema").invoke(
                T.build_keyword_prompt(r, jd, f, missing))
        except Exception as e:
            log(f"ats round {rnd}: model error {type(e).__name__}")
            continue
        g = T.apply_keyword_pass(f, kp)
        base_issues = T.duplicates(r, f) + T.quality(r, jd, f)
        base_uncovered = set(T.fill_checks(r, jd, f)[0])
        issues = [i for i in T.duplicates(r, g) + T.quality(r, jd, g) if i not in base_issues]  # new ones only
        uncovered = sorted(set(T.fill_checks(r, jd, g)[0]) - base_uncovered)
        current = (job_dir / "resume.tex").read_text()
        backup = (current, (job_dir / "resume.pdf").read_bytes())
        tex, problems, pages, err = render_keeping_projects(r, g, current, job_dir)
        ok = pages == 1 and not err and not issues and not uncovered and not any("LOCKED" in p for p in problems)
        if not ok:  # keep the previous version
            (job_dir / "resume.tex").write_text(backup[0])
            (job_dir / "resume.pdf").write_bytes(backup[1])
            log(f"ats round {rnd}: rejected (pages={pages} {err} issues={issues[:2]} uncovered={uncovered})")
            continue
        new = ats_local.score(job_dir / "resume.pdf", jd, jd_text)
        ats_local.record(job_dir, "local", rnd, new.score, new.missing, new.to_dict())
        log(f"ats round {rnd}: local {score.score} -> {new.score}; added "
            f"{[t for a in kp.skills for t in a.terms]} + {len(kp.bullets)} bullet edit(s); still missing {new.missing[:6]}")
        if new.score <= score.score:  # no gain: undo
            (job_dir / "resume.tex").write_text(backup[0])
            (job_dir / "resume.pdf").write_bytes(backup[1])
            continue
        f, score, changed = g, new, True
        history.append(score.score)
        (job_dir / "filled.json").write_text(f.model_dump_json(indent=1))
    if changed:
        from jobagent.steps.tailor import cover_letter
        cover_letter(job_dir, company_slug, log=log)
    result = {"score": score.score, "history": history, "missing": score.missing, "reached": score.score >= target,
              "parse_issues": score.parse_issues}
    log(f"ats: {' -> '.join(map(str, history))} ({'target reached' if result['reached'] else 'gap ' + str(round(target - score.score, 1))})")
    return result
