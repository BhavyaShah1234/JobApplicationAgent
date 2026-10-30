"""Step 10: online ATS checkers, driven in the agent's browser (AutoApplyMax and Jobalytics with the agent's free
accounts, created with the user's approval 2026-10-28; HireFlow and ResumeAtlas need none). Only sites whose terms
allow automated use are here; SkillsFirst, Jobscan, SkillSyncer and AtsAlign forbid it and ApplyCove runs a CAPTCHA
on every scan, so those are run by hand and recorded with `jobagent ats-record`.

- AutoApplyMax: resume text + job description -> 0-100 ATS score with a breakdown (keywords, formatting,
  completeness, readability) and matched / missing words. Job-specific, but its "missing keywords" are word
  overlap (e.g. "salary", "world"), so the keyword pass only takes the ones that are real skills.
- Jobalytics: resume PDF -> 0-100 general resume score (content, sections, ATS essentials). Not job-specific: its
  job match stays locked for this account. Its issues (repeated words, lines without numbers) are advice.
- HireFlow: resume PDF + job description -> 0-100 match, missing must-have / nice-to-have phrases, recommendations.
- ATS Friendly (agent account via Google, created with the user's approval 2026-10-29): resume PDF + job description
  -> 0-100 score with missing hard skills, soft skills and other keywords. The free plan allows 2 scans a week, so
  the loop runs it only at the start and on the final resume.

Every site gets the PDF as "<First>_<Last>_Resume.pdf" (Jobalytics flags a generic file name). Advice about locked
template parts (header links, contact details) goes in breakdown["template"] for the user, never into the loop.
"""

import re
import shutil
import subprocess
import tempfile
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
    advice: list[str] = field(default_factory=list)   # the site's own suggestions, for the advice pass

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
    issues = {k: int(n) for k, n in re.findall(r"(Repetition|Length|Bullets Consistency|Quantify Impact|Experience Detail|"
                                                r"ATS Readability|Header Links|File Name) (\d+) issues?", body)}
    advice = []
    if words := re.search(r"Some words repeat often: (.*?)\.", body):
        advice.append(f"These words repeat too often ({words.group(1)}): keep each in Technical Skills and at most two "
                      f"bullets, and vary the wording elsewhere")
    if issues.get("Quantify Impact") and (q := re.search(r"invite you in\. (.*?) Repetition \d", body)):
        advice.append(f"Lines without a measurable result (add one where the work plausibly had it): {q.group(1)[:500]}")
    if issues.get("Bullets Consistency"):
        advice.append("Start every bullet with a capital letter and end every bullet with a period")
    template = [t for t, k in (("Header links are hidden hyperlinks; show full URLs (e.g. linkedin.com/in/you)",
                                "Header Links"), ("Generic file name", "File Name")) if issues.get(k)]
    return SiteScore("jobalytics", float(m.group(1)), False, [],
                     {"content": pct("CONTENT"), "sections": pct("SECTIONS"), "ats_essentials": pct("ATS ESSENTIALS"),
                      "issues": issues, "template": template}, advice)


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
    sugg = re.search(r"(?:Recommendations|Suggested Additions) (.*?) (?:Want a version|Tailor my resume|Get your|$)", body)
    # One recommendation per imperative sentence ("Consider ...", "Highlight ...")
    advice = [a.strip() for a in re.split(r" (?=(?:Consider|Highlight|Add|Include|Emphasi[sz]e|Mention|Quantify|Show|"
                                          r"Demonstrate|Use|Remove|Clarify|Describe|Showcase)\b)",
                                          sugg.group(1)[:1500] if sugg else "") if a.strip()]
    return SiteScore("hireflow", float(m.group(1)), True, missing,
                     {"keyword_match": pct("Keyword Match"), "skills_fit": pct("Skills Fit"),
                      "experience_fit": pct("Experience Fit"), "completeness": pct("Completeness")}, advice)


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


# Each result section on ATS Friendly: its percentage and its "Missing keywords" / "Missing items" list
_ATSF_SECTIONS = r"""() => {
  const names = ['Hard Skills', 'Soft Skills', 'Other Keywords', 'Educational Qualifications', 'Job Title Matching',
                 'Contact Details'];
  const out = {};
  for (const ul of document.querySelectorAll('ul.missing-keywords')) {
    let p = ul.parentElement;
    while (p && !names.some(n => p.textContent.trim().startsWith(n))) p = p.parentElement;
    const name = p ? names.find(n => p.textContent.trim().startsWith(n)) : 'Other';
    out[name] = (out[name] || []).concat([...ul.querySelectorAll('li')].map(li => li.textContent.trim()));
  }
  const pct = [...document.querySelectorAll('.progress-text')].map(e => parseInt(e.textContent));
  return {sections: out, pct};
}"""


def atsfriendly(pg, resume_pdf: Path, jd_text: str) -> SiteScore:
    """ATS Friendly scan (agent account): 0-100 score; missing hard skills, soft skills and other keywords by section.
    The free plan allows 2 scans a week; a SiteError is raised before scanning when none is left."""
    pg.goto("https://www.atsfriendly.com/dashboard", wait_until="domcontentloaded")
    pg.wait_for_timeout(4000)
    if "accounts.google.com" in pg.url or pg.locator("a.social-login-btn").count():
        raise SiteError("ATS Friendly: signed out (sign in again with Google)")
    ok = pg.get_by_text("Got it!", exact=True)   # cookie banner covers the wizard buttons
    if ok.count() and ok.first.is_visible():
        ok.first.click()
    # The wizard remembers the last scan: go back to step 1 and drop the previous resume
    for _ in range(3):
        if pg.locator("input[type=file]").count() or "Resume Fetched" in _body(pg):
            break
        back = pg.get_by_role("button", name=re.compile(r"^\s*back\s*$", re.I))
        if back.count() and back.first.is_visible():
            back.first.click()
            pg.wait_for_timeout(1500)
    cancel = pg.get_by_role("button", name=re.compile(r"^\s*cancel\s*$", re.I))
    if "Resume Fetched" in _body(pg) and cancel.count():
        cancel.first.click()
        pg.wait_for_timeout(1500)
    pg.locator("input[type=file]").first.set_input_files(str(resume_pdf))
    pg.wait_for_timeout(3000)
    pg.get_by_role("button", name=re.compile("^next step$", re.I)).first.click()
    pg.wait_for_timeout(2000)
    pg.locator("textarea:visible").first.fill(jd_text[:8000])
    pg.get_by_role("button", name=re.compile("^next step$", re.I)).first.click()
    pg.wait_for_timeout(2000)
    if (left := re.search(r"You have (\d+) free scan", _body(pg))) and left.group(1) == "0":
        raise SiteError("ATS Friendly: no free scans left this week (2 a week)")
    pg.get_by_role("button", name=re.compile("start scan", re.I)).first.click()
    for _ in range(45):
        pg.wait_for_timeout(4000)
        if re.search(r"/dashboard/[0-9a-f]{12,}", pg.url) and pg.locator(".progress-text").count():
            pg.wait_for_timeout(2000)
            break
    else:
        screenshot(pg, "/app/state/logs/ats-atsfriendly-fail.png")
        raise SiteError("ATS Friendly: no result page")
    return atsfriendly_result(pg)


def atsfriendly_result(pg, url: str = "") -> SiteScore:
    """Parse an ATS Friendly result page (the current page, or a past scan's `url`; reading one costs no scan)."""
    if url:
        pg.goto(url, wait_until="domcontentloaded")
        pg.wait_for_timeout(5000)
    res = pg.evaluate(_ATSF_SECTIONS)
    sec, pct = res["sections"], res["pct"]
    strip = lambda k: re.sub(r"\s*\(optional\)$", "", k)
    missing = [strip(k) for name in ("Hard Skills", "Other Keywords") for k in sec.get(name, [])]  # soft skills: advice
    advice = []
    if sec.get("Soft Skills"):
        advice.append("Show these soft skills through what the work involved (Summary or bullets, never Technical "
                      f"Skills): {', '.join(sec['Soft Skills'])}")
    # The header's missing city/state/country is never reported: the user keeps location off the resume on purpose
    template = []
    if sec.get("Job Title Matching"):
        advice.append(f"Open the Summary with the posting's exact title: {sec['Job Title Matching'][0]}")
    if sec.get("Educational Qualifications"):
        advice.append("Name the degree field the posting asks for where the candidate's real degree matches it "
                      f"(Education is locked; say it in the Summary): {', '.join(sec['Educational Qualifications'])}")
    names = ("hard_skills", "soft_skills", "other_keywords")
    return SiteScore("atsfriendly", float(pct[0]), True, missing,
                     {**dict(zip(names, pct[1:4])), "url": pg.url, "template": template}, advice)


SITES = {"autoapplymax": autoapplymax, "jobalytics": jobalytics, "hireflow": hireflow, "resumeatlas": resumeatlas,
         "atsfriendly": atsfriendly}
# Used by default. ATS Friendly (2 scans a week) and ResumeAtlas (2 a month) are "budget" sites: the loop scores them
# only at the start and on the final resume; ResumeAtlas only when asked for.
DEFAULT_SITES = ("autoapplymax", "jobalytics", "hireflow", "atsfriendly")
BUDGET_SITES = ("atsfriendly", "resumeatlas")
# HireFlow's fit scores are model judgments: the same resume scored 52-64 across runs, so it is scored this many times
# and averaged (its missing phrases merged)
REPEATS = {"hireflow": 2}
# Sites whose terms forbid automated use, or that run a CAPTCHA on every scan: scanned by the user, recorded with
# `jobagent ats-record`.
MANUAL_SITES = ("skillsfirst", "jobscan", "skillsyncer", "atsalign", "applycove")


def score_all(resume_pdf: Path, jd_text: str, sites=DEFAULT_SITES, title: str = "", log=print) -> list[SiteScore]:
    """Every enabled site, one browser session; a site that fails is reported, never fatal. A budget site that
    already scored this exact resume (same PDF hash) is not scanned again."""
    out = []
    for name in [x for x in sites if x in BUDGET_SITES]:
        if hit := cached(resume_pdf, name):
            log(f"ats site {name}: reusing its score for this exact resume (no scan spent)")
            out.append(hit)
    sites = [x for x in sites if x not in {s.site for s in out}]
    tmp = Path(tempfile.mkdtemp())
    named = shutil.copy(resume_pdf, tmp / upload_name())
    with persistent_context() as ctx:
        pg = ctx.pages[0]
        for name in sites:
            try:
                runs = [resumeatlas(pg, named, jd_text, title) if name == "resumeatlas"
                        else SITES[name](pg, named, jd_text) for _ in range(REPEATS.get(name, 1))]
                first = runs[0]
                first.score = round(sum(r.score for r in runs) / len(runs), 1)
                first.missing = list(dict.fromkeys(m for r in runs for m in r.missing))
                if len(runs) > 1:
                    first.breakdown["runs"] = [r.score for r in runs]
                out.append(first)
            except Exception as e:
                log(f"ats site {name}: {type(e).__name__}: {str(e)[:160]}")
                out.append(SiteScore(name, None, name == "autoapplymax", [], {"error": str(e)[:300]}))
    shutil.rmtree(tmp, ignore_errors=True)
    return out


def cached(resume_pdf: Path, site: str) -> SiteScore | None:
    """The latest successful run of `site` on this exact resume PDF, from ats_runs."""
    import hashlib
    import json

    from jobagent import db
    sha = hashlib.sha256(Path(resume_pdf).read_bytes()).hexdigest()
    row = db.connect().execute("SELECT raw FROM ats_runs WHERE site = ? AND resume_sha = ? AND score IS NOT NULL "
                               "ORDER BY id DESC LIMIT 1", (site, sha)).fetchone()
    return SiteScore(**json.loads(row[0])) if row else None


def upload_name() -> str:
    """"First_Middle_Last_Resume.pdf" from personal_info.yaml ("Resume.pdf" without a name)."""
    import yaml

    from jobagent.config import get_settings
    try:
        n = yaml.safe_load((get_settings().data_dir / "personal_info.yaml").read_text())["name"]
        parts = " ".join(str(n.get(k) or "") for k in ("first", "middle", "last")).split()
    except Exception:
        parts = []
    return "_".join(re.sub(r"[^A-Za-z0-9-]", "", p) for p in parts + ["Resume"]) + ".pdf"


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
