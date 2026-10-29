"""Step 10: online ATS checkers, driven in the agent's browser (AutoApplyMax and Jobalytics with the agent's free
accounts, created with the user's approval 2026-10-28; HireFlow and ResumeAtlas need none). Only sites whose terms
allow automated use are here; SkillsFirst, Jobscan, SkillSyncer and AtsAlign forbid it and ApplyCove runs a CAPTCHA
on every scan, so those are run by hand and recorded with `jobagent ats-record`.

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


_MISSING_PILLS = r"""() => [...document.querySelectorAll('p')]
  .filter(p => /^Missing \(\d+\)$/.test(p.textContent.trim()))
  .flatMap(p => p.nextElementSibling ? [...p.nextElementSibling.querySelectorAll('span')].map(x => x.textContent.trim()) : [])
  .filter(Boolean)"""


def hireflow(pg, resume_pdf: Path, jd_text: str) -> SiteScore:
    """HireFlow job match: overall 0-100 (keyword match, skills fit, experience fit, completeness), must-have and
    nice-to-have phrases matched / missing, and suggested additions. No account."""
    pg.goto("https://hireflow.net/job-match-score", wait_until="domcontentloaded")
    pg.wait_for_timeout(4000)
    pg.locator("input[type=file]").first.set_input_files(str(resume_pdf))
    pg.wait_for_timeout(3000)
    pg.locator("textarea:visible").first.fill(jd_text[:8000])
    pg.get_by_role("button", name=re.compile("get match score", re.I)).first.click()
    for _ in range(40):
        pg.wait_for_timeout(2000)
        body = _body(pg)
        if m := re.search(r"OVERALL MATCH (\d{1,3})", body):
            break
    else:
        screenshot(pg, "/app/state/logs/ats-hireflow-fail.png")
        raise SiteError("HireFlow: no score on the page")
    pct = lambda name: int(x.group(1)) if (x := re.search(rf"{name} (\d{{1,3}})%", body)) else None
    # Each phrase is a pill after its "Missing (n)" label; the page text runs them together, so read the pills.
    missing = pg.evaluate(_MISSING_PILLS)
    sugg = re.search(r"Suggested Additions (.*?)(?:Tailor|Get your|$)", body)
    return SiteScore("hireflow", float(m.group(1)), True, missing,
                     {"keyword_match": pct("Keyword Match"), "skills_fit": pct("Skills Fit"),
                      "experience_fit": pct("Experience Fit"), "completeness": pct("Completeness"),
                      "suggestions": sugg.group(1)[:1500] if sugg else ""})


def resumeatlas(pg, resume_pdf: Path, jd_text: str, title: str = "") -> SiteScore:
    """ResumeAtlas application verdict: 0-100 share of required skills demonstrated in work bullets, plus the skills
    missing or needing stronger evidence. No account (the job description is cut at 650 words by the site)."""
    pg.goto("https://resumeatlas.io/check-resume-against-job-description", wait_until="domcontentloaded")
    pg.wait_for_timeout(4000)
    pg.locator("input[type=file]").first.set_input_files(str(resume_pdf))
    pg.wait_for_timeout(3000)
    pg.get_by_placeholder(re.compile("Senior GenAI", re.I)).fill(title or "Software Engineer")
    pg.locator("textarea:visible").nth(1).fill(jd_text[:8000])
    pg.get_by_role("button", name=re.compile("analyze my resume", re.I)).first.click()
    for _ in range(60):
        pg.wait_for_timeout(3000)
        top = _body(pg).split("Your Resume Match Results")[0]
        if "Comparing it to the job description" not in top and (m := re.search(r"APPLICATION VERDICT \S* (\d{1,3})%", top)):
            break
    else:
        screenshot(pg, "/app/state/logs/ats-resumeatlas-fail.png")
        raise SiteError("ResumeAtlas: no verdict on the page")
    grab = lambda label: (x.group(1) if (x := re.search(rf"{label} \d+ skills? (.*?) (?:Needs stronger|Already demonstrated|No changes|Choose the fixes)", top)) else "")
    missing = [w for w in re.split(r"\s{2,}|(?<=\w) (?=[A-Z])", grab("Missing from resume")) if w and w != "None."]
    weak = [w for w in re.split(r"(?<=\w) (?=[A-Z])", grab("Needs stronger evidence")) if w and w != "None."]
    return SiteScore("resumeatlas", float(m.group(1)), True, missing + weak, {"weak_evidence": weak})


SITES = {"autoapplymax": autoapplymax, "jobalytics": jobalytics, "hireflow": hireflow, "resumeatlas": resumeatlas}
# Used every round by default. ResumeAtlas allows 2 job matches a month, so it is only run when asked for.
DEFAULT_SITES = ("autoapplymax", "jobalytics", "hireflow")
# Sites whose terms forbid automated use, or that run a CAPTCHA on every scan: scanned by the user, recorded with
# `jobagent ats-record`.
MANUAL_SITES = ("skillsfirst", "jobscan", "skillsyncer", "atsalign", "applycove")


def score_all(resume_pdf: Path, jd_text: str, sites=DEFAULT_SITES, title: str = "", log=print) -> list[SiteScore]:
    """Every enabled site, one browser session; a site that fails is reported, never fatal."""
    out = []
    with persistent_context() as ctx:
        pg = ctx.pages[0]
        for name in sites:
            try:
                out.append(resumeatlas(pg, resume_pdf, jd_text, title) if name == "resumeatlas"
                           else SITES[name](pg, resume_pdf, jd_text))
            except Exception as e:
                log(f"ats site {name}: {type(e).__name__}: {str(e)[:160]}")
                out.append(SiteScore(name, None, name == "autoapplymax", [], {"error": str(e)[:300]}))
    return out


def record(job_dir: Path, site: str, rnd: int, score: float | None, missing: list[str], raw: dict) -> None:
    """One row in ats_runs for this resume version."""
    import hashlib
    import json

    from jobagent import db
    sha = hashlib.sha256((job_dir / "resume.pdf").read_bytes()).hexdigest()
    with (conn := db.connect()):
        conn.execute("INSERT INTO ats_runs (job_dir, site, round, score, missing, raw, resume_sha) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (str(job_dir), site, rnd, score, json.dumps(missing), json.dumps(raw), sha))


def manual_scores(job_dir: Path) -> dict[str, float]:
    """The latest score the user recorded by hand for each manual site, for the current resume.pdf."""
    import hashlib

    from jobagent import db
    sha = hashlib.sha256((job_dir / "resume.pdf").read_bytes()).hexdigest()
    rows = db.connect().execute(
        "SELECT site, score FROM ats_runs WHERE job_dir = ? AND resume_sha = ? AND site IN (%s) AND score IS NOT NULL "
        "ORDER BY id" % ",".join("?" * len(MANUAL_SITES)), (str(job_dir), sha, *MANUAL_SITES)).fetchall()
    return {site: score for site, score in rows}
