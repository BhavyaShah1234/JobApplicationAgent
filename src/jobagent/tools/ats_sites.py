"""Step 10: online ATS checkers, driven in the agent's browser (AutoApplyMax and Jobalytics with the agent's free
accounts, created with the user's approval 2026-10-28; HireFlow and ResumeAtlas need none). SkillsFirst uses the
saved ASU organization account for automated, job-specific written feedback. Jobscan, SkillSyncer and ATSAlign
use Google sign-in. The default numeric average uses AutoApplyMax, HireFlow, SkillSyncer, ATSAlign and ATS Friendly;
SkillsFirst adds written feedback. Other registered sites remain available on request.

- SkillsFirst: resume PDF + job description -> Skills Gap Analysis recommendations, without a numeric score.
  Its advice feeds the writers; it is excluded from numeric averages. Job details are limited to 7,000 characters.
- AutoApplyMax: resume text + job description -> 0-100 ATS score with a breakdown (keywords, formatting,
  completeness, readability) and matched / missing words. Job-specific, but its "missing keywords" are word
  overlap (e.g. "salary", "world"), so the keyword pass only takes the ones that are real skills.
- Jobalytics: resume PDF -> 0-100 general resume score (content, sections, ATS essentials). Not job-specific: its
  job match stays locked for this account. Its issues (repeated words, lines without numbers) are advice.
- HireFlow: resume PDF + job description -> 0-100 match, missing must-have / nice-to-have phrases, recommendations.
- ATS Friendly (agent account via Google, created with the user's approval 2026-10-29): resume PDF + job description
  -> 0-100 score with missing hard skills, soft skills and other keywords. The free plan allows 2 scans a week, so
  the loop runs it only at the start and on the final resume.
- Jobscan, SkillSyncer and ATSAlign: resume PDF + JD -> match score and visible missing skills. Login is restored
  with the saved Google identity. Free-plan hidden feedback is never treated as an empty gap list.
- ApplyCove: resume PDF + JD -> keyword match when its human verification permits the scan. A rejected CAPTCHA
  pauses for the candidate in noVNC, then resumes only after a real result is available.
- Novoresume: resume PDF + JD -> job-match score and specific writing suggestions. Country comes from the
  applicant's address and experience level from the resume's dated roles. Initial and final scans only.

Every site gets the PDF as "<First>_<Last>_Resume.pdf" (Jobalytics flags a generic file name). Advice about locked
template parts (header links, contact details) goes in breakdown["template"] for the user, never into the loop.
"""

import hashlib
import json
import math
import re
import shutil
import subprocess
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

from jobagent.tools.browser import persistent_context, screenshot
from jobagent.tools import ats_providers
from jobagent.tools.ats_accounts import check_scan_blockers, google_login


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


# F20: the posting text each provider's input box actually receives. A provider that takes only part of a long posting scores a
# DIFFERENT requirement set than one that takes all of it, so what was sent is measured, hashed and recorded with the score.
JD_LIMITS = {"autoapplymax": 8000, "hireflow": 8000, "resumeatlas": 8000, "atsfriendly": 8000}   # None/absent: the full text
JD_WORD_LIMITS = {"resumeatlas": 650}
# Bump a provider's version when its adapter or parser changes in a way that can change its scores: a recorded score of another
# version is not reused (limited-scan sites keep scans only for the version that made them).
PROVIDER_VERSIONS = {"jobscan": 2, "resumeatlas": 3, "skillsyncer": 3}      # every other provider: 1
_payload = threading.local()


def provider_version(site: str) -> int:
    return PROVIDER_VERSIONS.get(site, 1)


def jd_payload(site: str, jd_text: str) -> str:
    """The posting text to send to `site`: the whole text, or what its box takes; either way what was sent is remembered
    for `score_all` to record (`payload_record`)."""
    limit = JD_LIMITS.get(site)
    sent = jd_text if limit is None or len(jd_text) <= limit else jd_text[:limit]
    if word_limit := JD_WORD_LIMITS.get(site):
        words = list(re.finditer(r'\S+', sent))
        if len(words) > word_limit:
            sent = sent[:words[word_limit - 1].end()]
        sent = sent.rstrip()  # the provider's word-limited control also removes trailing whitespace
    _payload.last = payload_record(site, jd_text, sent)
    return sent


def payload_record(site: str, jd_text: str, sent: str) -> dict:
    return {"jd_chars_total": len(jd_text), "jd_chars_sent": len(sent), "limit": JD_LIMITS.get(site),
            "jd_words_total": len(jd_text.split()), "jd_words_sent": len(sent.split()),
            "word_limit": JD_WORD_LIMITS.get(site),
            "truncated": len(sent.rstrip()) < len(jd_text.rstrip()), "jd_sha256": hashlib.sha256(jd_text.encode()).hexdigest(),
            "sent_sha256": hashlib.sha256(sent.encode()).hexdigest()}


def _body(pg) -> str:
    return " ".join(pg.locator("body").inner_text(timeout=15_000).split())


def autoapplymax(pg, resume_pdf: Path, jd_text: str, title: str = "", company: str = "") -> SiteScore:
    text = subprocess.run(["pdftotext", "-layout", str(resume_pdf), "-"], capture_output=True, text=True).stdout
    pg.goto("https://www.autoapplymax.com/tools/ats-score-checker", wait_until="domcontentloaded")
    pg.wait_for_timeout(3000)
    pg.get_by_role("button", name=re.compile("^paste text$", re.I)).first.click()
    pg.wait_for_timeout(800)
    pg.locator("#resumeText").fill(text)
    pg.locator("textarea:visible").last.fill(jd_payload("autoapplymax", jd_text))
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


def jobalytics(pg, resume_pdf: Path, jd_text: str = "", title: str = "", company: str = "") -> SiteScore:
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
    # "Bullets Consistency" is not advice: Jobalytics reports 1 such issue even for a resume whose bullets are all
    # capitalised and end with a period (tested 2026-10-31 with 9 bullet/period variants and a plain control)
    template = [t for t, k in (("Header links are hidden hyperlinks; show full URLs (e.g. linkedin.com/in/you)",
                                "Header Links"), ("Generic file name", "File Name")) if issues.get(k)]
    return SiteScore("jobalytics", float(m.group(1)), False, [],
                     {"content": pct("CONTENT"), "sections": pct("SECTIONS"), "ats_essentials": pct("ATS ESSENTIALS"),
                      "issues": issues, "template": template}, advice)


_MISSING_PILLS = r"""() => [...document.querySelectorAll('p')]
  .filter(p => /^Missing \(\d+\)$/.test(p.textContent.trim()))
  .flatMap(p => p.nextElementSibling ? [...p.nextElementSibling.querySelectorAll('span')].map(x => x.textContent.trim()) : [])
  .filter(Boolean)"""


def hireflow(pg, resume_pdf: Path, jd_text: str, title: str = "", company: str = "") -> SiteScore:
    """HireFlow job match: overall 0-100 (keyword match, skills fit, experience fit, completeness), must-have and
    nice-to-have phrases matched / missing, and suggested additions. No account."""
    pg.goto("https://hireflow.net/job-match-score", wait_until="domcontentloaded")
    pg.wait_for_timeout(4000)
    pg.locator("input[type=file]").first.set_input_files(str(resume_pdf))
    pg.wait_for_timeout(3000)
    pg.locator("textarea:visible").first.fill(jd_payload("hireflow", jd_text))
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


def resumeatlas(pg, resume_pdf: Path, jd_text: str, title: str = "", company: str = "") -> SiteScore:
    """ResumeAtlas application verdict: 0-100 share of required skills demonstrated in work bullets, plus the skills
    missing or needing stronger evidence. No account (the job description is cut at 650 words by the site)."""
    pg.goto("https://resumeatlas.io/check-resume-against-job-description", wait_until="domcontentloaded")
    pg.wait_for_timeout(4000)
    pg.locator("input[type=file]").first.set_input_files(str(resume_pdf))
    pg.wait_for_timeout(3000)
    pg.get_by_placeholder(re.compile("Senior GenAI", re.I)).fill(title or "Software Engineer")
    box = pg.locator("textarea:visible").nth(1)
    sent = jd_payload("resumeatlas", jd_text)
    box.fill(sent)
    # Record the actual control value, including any additional client-side cap.
    actual = box.input_value()
    _payload.last = payload_record("resumeatlas", jd_text, actual)
    if actual != sent:
        raise SiteError("ResumeAtlas: job-description input changed unexpectedly; no scan spent")
    if re.search(r"FREE CREDITS\s*0\s*/", _body(pg), re.I):
        from jobagent.tools.ats_accounts import AutomationBlocked
        raise AutomationBlocked("ResumeAtlas: no free checks left this month", "quota_exhausted")
    if pg.locator('[aria-label="Your analysis report"] .ref-dash').count():
        raise SiteError("ResumeAtlas: a previous report is still present before this scan; refusing an unbound result")
    pg.get_by_role("button", name=re.compile("analyze my resume", re.I)).first.click()
    for _ in range(60):
        pg.wait_for_timeout(3000)
        body = _body(pg)
        if pg.locator('[aria-label="Your analysis report"] .ref-dash-verdict-pct:visible').count():
            return SiteScore(**ats_providers.resumeatlas_result(pg))
        check_scan_blockers(body, "ResumeAtlas")
    else:
        screenshot(pg, "/app/state/logs/ats-resumeatlas-fail.png")
        raise SiteError("ResumeAtlas: no verdict on the page")


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


def atsfriendly(pg, resume_pdf: Path, jd_text: str, title: str = "", company: str = "") -> SiteScore:
    """ATS Friendly scan (agent account): 0-100 score; missing hard skills, soft skills and other keywords by section.
    The free plan allows 2 scans a week; a SiteError is raised before scanning when none is left."""
    pg.goto("https://www.atsfriendly.com/dashboard", wait_until="domcontentloaded")
    pg.wait_for_timeout(4000)
    google_login(
        pg, "www.atsfriendly.com",
        lambda page: "atsfriendly.com/dashboard" in page.url
        and page.locator("a.social-login-btn:visible").count() == 0
        and page.locator("#bg-nested-dropdown").count() > 0,
    )
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
    pg.locator("textarea:visible").first.fill(jd_payload("atsfriendly", jd_text))
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

SKILLSFIRST_URL = (
    "https://skillsfirst.com/organizations/asu-engineering"
)
FEEDBACK_ONLY_SITES = {"skillsfirst"}


def skillsfirst(pg, resume_pdf: Path, jd_text: str, title: str = "", company: str = "") -> SiteScore:
    """Upload a resume and retrieve job-specific Skills Gap Analysis."""
    from jobagent.tools.vault import open_vault

    original_chars = len(jd_text)
    job_details = jd_text.strip()

    # SkillsFirst accepts at most 7,000 characters.
    # Remove the company introduction when an explicit job overview follows.
    if len(job_details) > 7000:
        overview = re.search(
            r"(?im)^\s*JOB OVERVIEW\s*$",
            job_details,
        )
        if overview:
            job_details = job_details[overview.start():].strip()

    if not job_details:
        raise SiteError("SkillsFirst: the job description is empty")

    if len(job_details) > 7000:
        raise SiteError(
            "SkillsFirst accepts at most 7,000 JD characters. "
            "The job details still exceed that limit after removing "
            "the company introduction; provide a shorter JD."
        )

    pg.goto(SKILLSFIRST_URL, wait_until="domcontentloaded")

    upload_selector = (
        'a[href$="/galleries/resumes/upload"]:visible'
    )
    password_selector = 'input[type="password"]:visible'

    pg.locator(
        f"{password_selector}, {upload_selector}"
    ).first.wait_for(state="visible", timeout=30_000)

    if pg.locator(password_selector).count():
        credentials = (
            open_vault().get("portal:skillsfirst.com") or {}
        )
        if not credentials.get("email") or not credentials.get("password"):
            raise SiteError(
                "SkillsFirst: save email and password under "
                "portal:skillsfirst.com in the vault"
            )

        pg.get_by_label("Email", exact=True).fill(
            credentials["email"]
        )
        pg.get_by_label("Password", exact=True).fill(
            credentials["password"]
        )
        pg.get_by_role(
            "button", name="Sign In", exact=True
        ).click()

    upload = pg.locator(upload_selector)
    upload.wait_for(state="visible", timeout=30_000)
    upload.click()

    file_input = pg.locator("#files_upload")
    file_input.wait_for(state="attached", timeout=20_000)
    file_input.set_input_files(str(resume_pdf))

    pg.wait_for_url(
        re.compile(r"/galleries/resumes/\d+(?:[/?#]|$)"),
        timeout=60_000,
    )
    resume_url = pg.url

    pg.get_by_text(
        "Enhance with AI", exact=True
    ).click(timeout=20_000)

    modal = pg.locator("#pjax-modal")
    modal.get_by_text(
        "Skills Gap Analysis", exact=True
    ).click(timeout=20_000)

    job_field = modal.locator("#analyze_text_field")
    job_field.wait_for(state="visible", timeout=20_000)
    job_field.fill(job_details)

    modal.locator(
        "#form-gpt-prompt-analyze"
    ).get_by_text("Submit", exact=True).click()

    report = modal.locator("#filteredItemsListGpt")
    report.locator(
        'a[href*="downloadGPTReport=true"]'
    ).wait_for(state="visible", timeout=120_000)

    report_text = (
        report.inner_text()
        .rsplit("Download Report", 1)[0]
        .strip()
    )
    if not report_text:
        raise SiteError("SkillsFirst: the analysis report is empty")

    # Preserve the complete report, while passing individual gap
    # recommendations through the existing feedback filters.
    sections = re.split(
        r"(?im)^\s*Skills Gaps\s*:\s*$",
        report_text,
        maxsplit=1,
    )
    gap_text = re.split(
        r"(?im)^\s*Final Recommendations\s*:\s*$",
        sections[-1],
        maxsplit=1,
    )[0]

    suggestions = [
        block.strip()
        for block in re.split(
            r"(?m)(?=^\s*\d+[.)]\s+)",
            gap_text,
        )
        if block.strip()
    ]

    return SiteScore(
        site="skillsfirst",
        score=None,
        job_specific=True,
        missing=[],
        breakdown={
            "feedback_only": True,
            "url": resume_url,
            "report": report_text,
            "jd_original_chars": original_chars,
            "jd_submitted_chars": len(job_details),
        },
        advice=[
            "SkillsFirst suggestion; apply only changes supported "
            f"by the candidate's existing facts: {suggestion}"
            for suggestion in suggestions
        ],
    )

def jobscan(pg, resume_pdf: Path, jd_text: str, title: str = "", company: str = "") -> SiteScore:
    return SiteScore(**ats_providers.jobscan(pg, resume_pdf, jd_text, title, company))


def skillsyncer(pg, resume_pdf: Path, jd_text: str, title: str = "", company: str = "") -> SiteScore:
    return SiteScore(**ats_providers.skillsyncer(pg, resume_pdf, jd_text, title, company))


def atsalign(pg, resume_pdf: Path, jd_text: str, title: str = "", company: str = "") -> SiteScore:
    return SiteScore(**ats_providers.atsalign(pg, resume_pdf, jd_text, title, company))


def applycove(pg, resume_pdf: Path, jd_text: str, title: str = "", company: str = "") -> SiteScore:
    return SiteScore(**ats_providers.applycove(pg, resume_pdf, jd_text, title, company))


def novoresume(pg, resume_pdf: Path, jd_text: str, title: str = "", company: str = "") -> SiteScore:
    return SiteScore(**ats_providers.novoresume(pg, resume_pdf, jd_text, title, company))


SITES = {
    "skillsfirst": skillsfirst,
    "autoapplymax": autoapplymax,
    "jobalytics": jobalytics,
    "hireflow": hireflow,
    "resumeatlas": resumeatlas,
    "atsfriendly": atsfriendly,
    "jobscan": jobscan,
    "skillsyncer": skillsyncer,
    "atsalign": atsalign,
    "applycove": applycove,
    "novoresume": novoresume,
}

AVERAGE_SITES = (
    "autoapplymax",
    "hireflow",
    "skillsyncer",
    "atsalign",
    "atsfriendly",
)
# SkillsFirst contributes writing advice, not a sixth score.
DEFAULT_SITES = ("skillsfirst", *AVERAGE_SITES)
BUDGET_SITES = ("atsfriendly", "resumeatlas", "jobscan", "skillsyncer", "atsalign", "applycove", "novoresume")
OPTIONAL_SITES = set(BUDGET_SITES) | FEEDBACK_ONLY_SITES | {"applycove"}
# HireFlow's fit scores are model judgments: the same resume scored 52-64 across runs, so it is scored this many times
# and averaged (its missing phrases merged)
REPEATS = {"hireflow": 2}
MANUAL_SITES = ()
RECORDABLE_SITES = tuple(name for name in SITES if name not in FEEDBACK_ONLY_SITES)


def numeric_sites(sites) -> tuple[str, ...]:
    return tuple(dict.fromkeys(name for name in sites if name not in FEEDBACK_ONLY_SITES))


def score_summary(scores: dict, sites=AVERAGE_SITES) -> dict:
    """Equal-weight mean of all requested numeric sites; an unavailable score leaves it incomplete."""
    sites = numeric_sites(sites)
    valid = {name: scores[name] for name in sites
             if isinstance(scores.get(name), (int, float)) and not isinstance(scores[name], bool)
             and math.isfinite(scores[name]) and 0 <= scores[name] <= 100}
    missing = [name for name in sites if name not in valid]
    complete = bool(sites) and not missing
    return {"average": round(sum(valid.values()) / len(sites), 1) if complete else None,
            "sites": list(sites), "scores": valid, "missing": missing, "complete": complete}


def write_summary(job_dir: Path, scores: dict, sites=AVERAGE_SITES, conn=None) -> dict:
    """Persist the average with the exact resume and job identity it describes. `scores` are the ones just obtained for
    this PDF; scores of the required sites recorded earlier for the same PDF and the same job (another round, a manual
    entry) complete them, and nothing from another PDF or job is mixed in (tools/ats_result.gather)."""
    from datetime import UTC, datetime

    from jobagent.tools import ats_result

    summary = ats_result.gather(job_dir, sites, conn=conn, fresh=scores)
    summary["scored_at"] = datetime.now(UTC).isoformat()
    (job_dir / "ats_summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def job_fingerprint(jd_text: str, title: str = "", company: str = "") -> str:
    return hashlib.sha256(json.dumps([jd_text, title, company], ensure_ascii=False).encode()).hexdigest()


def score_all(resume_pdf: Path, jd_text: str, sites=DEFAULT_SITES, title: str = "", log=print,
              company: str = "") -> list[SiteScore]:
    """Run enabled providers; reuse limited scans only for the same PDF, JD, title and company."""
    unknown = set(sites) - SITES.keys()
    if unknown:
        raise ValueError(f"unknown ATS sites: {', '.join(sorted(unknown))}")
    out = []
    fingerprint = job_fingerprint(jd_text, title, company)
    for name in [x for x in sites if x in BUDGET_SITES]:
        if hit := cached(resume_pdf, name, jd_text, title, company):
            log(f"ats site {name}: reusing its score for this exact resume and job (no scan spent)")
            out.append(hit)
    sites = [x for x in sites if x not in {s.site for s in out}]
    if not sites:
        return out
    tmp = Path(tempfile.mkdtemp())
    named = shutil.copy(resume_pdf, tmp / upload_name())
    # The job matcher asks for experience level. Derive it from this resume's dated roles.
    tex = Path(resume_pdf).with_suffix(".tex")
    if tex.exists():
        shutil.copy(tex, Path(named).with_suffix(".tex"))
    with persistent_context() as ctx:
        pg = ctx.pages[0]
        for name in sites:
            out.append(observe(name, pg, named, jd_text, title, company, fingerprint, log))
    shutil.rmtree(tmp, ignore_errors=True)
    return out


# a failure that more attempts cannot fix this run: stop at once instead of repeating it
NO_RETRY_STATUS = {"quota_exhausted", "blocked", "challenge", "sign_in_required", "captcha"}


def observe(name: str, pg, named, jd_text: str, title: str, company: str, fingerprint: str, log=print) -> SiteScore:
    """Run one provider the number of times it needs (REPEATS) and return ONE result for it.

    Every run is kept exactly as observed in `breakdown["observations"]` (score, missing words, the provider's own breakdown,
    or the error), unaltered by what is computed from them. The aggregate (the mean of the runs) is a separate value: the
    site's score. If a required run fails, the site has NO score - a mean of fewer runs than the provider needs would be a
    different measurement - and the observations that did succeed are kept. A failure that repeating cannot fix (an
    exhausted allowance, a challenge) stops the remaining runs at once."""
    import copy
    import time
    from datetime import UTC, datetime

    need = REPEATS.get(name, 1)
    observations, results = [], []
    for i in range(need):
        _payload.last = None
        started = datetime.now(UTC).isoformat(timespec="seconds")
        t0 = time.monotonic()
        try:
            r = SITES[name](pg, named, jd_text, title, company)
            payload = getattr(_payload, "last", None) or payload_record(name, jd_text, jd_text)
            results.append(r)
            complete = r.score is not None or (
                name in FEEDBACK_ONLY_SITES and r.breakdown.get("feedback_only")
                and isinstance(r.breakdown.get("report"), str) and bool(r.breakdown["report"].strip())
            )
            observations.append({"run": i + 1, "started_at": started, "seconds": round(time.monotonic() - t0, 1), "ok": bool(complete),
                                 "payload": payload, "result": copy.deepcopy(r.to_dict())})
        except Exception as e:
            status = getattr(e, "status", "error")
            log(f"ats site {name}: run {i + 1}/{need}: {type(e).__name__}: {str(e)[:160]}")
            observations.append({"run": i + 1, "started_at": started, "seconds": round(time.monotonic() - t0, 1), "ok": False,
                                 "status": status, "error": str(e)[:300], "payload": getattr(_payload, "last", None)})
            if status in NO_RETRY_STATUS or need > 1:
                break                                           # more runs cannot rescue this measurement
    scored = [r for r in results if r.score is not None]
    payload = next((o["payload"] for o in observations if o.get("payload")), None) or payload_record(name, jd_text, jd_text)
    meta = {"job_fingerprint": fingerprint, "provider_version": provider_version(name), "payload": payload,
            "observations": observations}
    if len(scored) == need or (name in FEEDBACK_ONLY_SITES and len(results) == need
                               and all(o["ok"] for o in observations)):
        first = results[0]
        agg = SiteScore(first.site, first.score, first.job_specific, list(first.missing), {**first.breakdown, **meta},
                        list(first.advice))
        if need > 1:
            agg.score = round(sum(r.score for r in scored) / need, 1)
            agg.missing = list(dict.fromkeys(m for r in results for m in r.missing))
            agg.breakdown["runs"] = [r.score for r in scored]
            agg.breakdown["aggregate"] = {"method": "mean", "runs": need}
        return agg
    failed = next((o for o in reversed(observations) if not o["ok"]), {})
    why = failed.get("error") or ("a run returned no score" if results else "no run completed")
    return SiteScore(name, None, name != "jobalytics", [], {
        **meta, "error": why, "status": failed.get("status", "incomplete"),
        "incomplete": f"{len(scored)} of {need} required run(s) produced a score",
    })


def cached(resume_pdf: Path, site: str, jd_text: str, title: str = "", company: str = "") -> SiteScore | None:
    """Reuse a score only for the same PDF bytes, full JD, title and company."""
    import hashlib
    import json

    from jobagent import db
    sha = hashlib.sha256(Path(resume_pdf).read_bytes()).hexdigest()
    fingerprint = job_fingerprint(jd_text, title, company)
    rows = db.connect().execute("SELECT raw FROM ats_runs WHERE site = ? AND resume_sha = ? AND score IS NOT NULL "
                                "ORDER BY id DESC", (site, sha)).fetchall()
    for row in rows:
        try:
            raw = json.loads(row[0])
            breakdown = raw.get("breakdown", {})
            seen = breakdown.get("provider_version", breakdown.get("parser_version"))
            if seen is None:
                if provider_version(site) != 1:        # recorded before versions were kept: only a provider still at version 1 can claim it
                    continue
            elif seen != provider_version(site):       # another adapter or parser version measured it: a different measurement
                continue
            if breakdown.get("job_fingerprint") == fingerprint:
                known = {f.name for f in __import__('dataclasses').fields(SiteScore)}
                return SiteScore(**{k: v for k, v in raw.items() if k in known})   # extra record keys (job_fingerprint) are not fields
        except (ValueError, TypeError, AttributeError):
            continue  # legacy/manual/corrupt rows must not break a fresh scoring run
    return None


def upload_name() -> str:
    """"First_Middle_Last_Resume.pdf" from the applicant's legal name ("Resume.pdf" without a name)."""
    from jobagent.tools import applicant
    try:
        n = applicant.name()
        parts = " ".join(str(n.get(k) or "") for k in ("first", "middle", "last")).split()
    except Exception:
        parts = []
    return "_".join(re.sub(r"[^A-Za-z0-9-]", "", p) for p in parts + ["Resume"]) + ".pdf"


def record(job_dir: Path, site: str, rnd: int, score: float | None, missing: list[str], raw: dict) -> None:
    """One row in ats_runs for this resume version."""
    import hashlib
    import json

    from jobagent import db
    from jobagent.tools import ats_result
    sha = hashlib.sha256((job_dir / "resume.pdf").read_bytes()).hexdigest()
    raw = {**raw, "job_fingerprint": ats_result.identity(job_dir)[1]}     # every row, manual ones too, names its PDF and its job
    with (conn := db.connect()):
        conn.execute("INSERT INTO ats_runs (job_dir, site, round, score, missing, raw, resume_sha) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (db.job_key(job_dir), site, rnd, score, json.dumps(missing), json.dumps(raw), sha))


def manual_scores(job_dir: Path) -> dict[str, float]:
    """The latest explicitly manual scores for this job's current PDF AND current job posting, even when an adapter also
    exists. A manual score recorded without a job identity (before F06) is not used: record it again."""
    from jobagent import db
    from jobagent.tools import ats_result
    sha, fingerprint = ats_result.identity(job_dir)
    rows = db.connect().execute(
        "SELECT site, score, raw FROM ats_runs WHERE job_dir = ? AND resume_sha = ? "
        "AND json_extract(raw, '$.manual') = 1 AND score IS NOT NULL ORDER BY id", (db.job_key(job_dir), sha)).fetchall()
    out = {}
    for site, score, raw in rows:
        try:
            if ats_result.row_fingerprint(json.loads(raw)) == fingerprint:
                out[site] = score
        except ValueError:
            continue
    return out
