"""Step 10: external ATS checkers, driven in the agent's browser with the agent's free accounts (created with the
user's approval, 2026-10-28, via "Continue with Google"). Only sites whose terms allow automated use are here;
Jobscan, SkillSyncer and SkillsFirst forbid it and are used by hand.

- AutoApplyMax: resume text + job description -> 0-100 ATS score with a breakdown (keywords, formatting,
  completeness, readability) and matched / missing words. Job-specific, but its "missing keywords" are word
  overlap (e.g. "salary", "world"), so the keyword pass only takes the ones that are real skills.
- Jobalytics: resume PDF -> 0-100 general resume score (content, sections, ATS essentials). Not job-specific: its
  job match stays locked for this account.
"""

import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

from jobagent.tools.browser import persistent_context, screenshot


class SiteError(RuntimeError):
    pass


@dataclass
class SiteScore:
    site: str
    score: float | None
    job_specific: bool
    missing: list[str] = field(default_factory=list)
    breakdown: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _body(pg) -> str:
    return " ".join(pg.locator("body").inner_text(timeout=15_000).split())


def autoapplymax(pg, resume_pdf: Path, jd_text: str) -> SiteScore:
    text = subprocess.run(["pdftotext", "-layout", str(resume_pdf), "-"], capture_output=True, text=True).stdout
    pg.goto("https://www.autoapplymax.com/tools/ats-score-checker", wait_until="domcontentloaded")
    pg.wait_for_timeout(3000)
    pg.get_by_role("button", name=re.compile("^paste text$", re.I)).first.click()
    pg.wait_for_timeout(800)
    pg.locator("#resumeText").fill(text)
    pg.locator("textarea:visible").last.fill(jd_text[:8000])
    pg.get_by_role("button", name=re.compile("analyze resume", re.I)).first.click()
    for _ in range(20):
        pg.wait_for_timeout(1500)
        body = _body(pg)
        if m := re.search(r"Your ATS Score Results.*?(\d{1,3}) ATS SCORE", body):
            break
    else:
        screenshot(pg, "/app/state/logs/ats-autoapplymax-fail.png")
        raise SiteError("AutoApplyMax: no score on the page")
    if "PREVIEW" in body[:body.find("ATS SCORE")]:
        raise SiteError("AutoApplyMax: only a preview score (signed out?)")
    part = lambda name: int(x.group(1)) if (x := re.search(rf"{name} \d+% weight (\d{{1,3}})", body)) else None
    missing = re.search(r"MISSING KEYWORDS (.*?) \d+ missing keywords", body)
    matched = re.search(r"MATCHED KEYWORDS (.*?) MISSING KEYWORDS", body)
    return SiteScore("autoapplymax", float(m.group(1)), True,
                     missing.group(1).split() if missing else [],
                     {"keywords": part("Keywords"), "formatting": part("Formatting"),
                      "completeness": part("Completeness"), "readability": part("Readability"),
                      "matched": matched.group(1).split() if matched else []})


def jobalytics(pg, resume_pdf: Path, jd_text: str = "") -> SiteScore:
    pg.goto("https://jobalytics.app/resume-checker", wait_until="domcontentloaded")
    pg.wait_for_timeout(3000)
    pg.locator("input[type=file]").first.set_input_files(str(resume_pdf))
    for _ in range(20):
        pg.wait_for_timeout(1500)
        body = _body(pg)
        if m := re.search(r"Your Score (\d{1,3})", body):
            break
    else:
        screenshot(pg, "/app/state/logs/ats-jobalytics-fail.png")
        raise SiteError("Jobalytics: no score on the page")
    pct = lambda name: int(x.group(1)) if (x := re.search(rf"{name} (\d{{1,3}})%", body)) else None
    issues = re.findall(r"(Repetition|Length|Bullets Consistency|Quantify Impact|Experience Detail|ATS Readability) "
                        r"(\d+) issues?", body)
    return SiteScore("jobalytics", float(m.group(1)), False, [],
                     {"content": pct("CONTENT"), "sections": pct("SECTIONS"), "ats_essentials": pct("ATS ESSENTIALS"),
                      "issues": {k: int(n) for k, n in issues}})


SITES = {"autoapplymax": autoapplymax, "jobalytics": jobalytics}


def score_all(resume_pdf: Path, jd_text: str, sites=tuple(SITES), log=print) -> list[SiteScore]:
    """Every enabled site, one browser session; a site that fails is reported, never fatal."""
    out = []
    with persistent_context() as ctx:
        pg = ctx.pages[0]
        for name in sites:
            try:
                out.append(SITES[name](pg, resume_pdf, jd_text))
            except Exception as e:
                log(f"ats site {name}: {type(e).__name__}: {str(e)[:160]}")
                out.append(SiteScore(name, None, name == "autoapplymax", [], {"error": str(e)[:300]}))
    return out
