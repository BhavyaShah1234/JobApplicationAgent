"""Job-specific ATS workflows using the sites' visible scan forms and reports."""

import re
import time
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeout, expect

from jobagent.tools.ats_accounts import AutomationBlocked, check_scan_blockers, google_login, human_handoff


def _text(page) -> str:
    return page.locator("body").inner_text(timeout=15_000)


def _score(text: str, expression: str, site: str) -> float:
    match = re.search(expression, text, re.I)
    if not match or not 0 <= float(match.group(1)) <= 100:
        raise RuntimeError(f"{site}: no valid score in the visible report")
    return float(match.group(1))


def _result(site: str, score: float, page, missing=(), advice=(), **breakdown) -> dict:
    return {
        "site": site,
        "score": score,
        "job_specific": True,
        "missing": list(dict.fromkeys(missing)),
        "advice": list(dict.fromkeys(advice)),
        "breakdown": {"url": page.url, **breakdown},
    }


def _wait_report(page, site: str, ready, timeout_s=120):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        text = _text(page)
        # A result page may show zero remaining credits after its successful last scan.
        if ready(text):
            return text
        check_scan_blockers(text, site)
        page.wait_for_timeout(700)
    raise RuntimeError(f"{site}: no completed report within {timeout_s}s")


def resumeatlas_result(page) -> dict:
    # The interactive sample and personal report use the SAME score classes.
    # The provider explicitly labels their enclosing regions differently.
    report = page.locator('[aria-label="Your analysis report"] .ref-dash')
    if report.count() != 1 or not report.is_visible():
        raise RuntimeError("ResumeAtlas: no completed personal report; sample previews are excluded")
    score = _score(report.locator(".ref-dash-verdict-pct").inner_text(), r"(\d{1,3})\s*%", "ResumeAtlas")

    def skills(label):
        row = report.get_by_text(label, exact=True).locator("xpath=../..")
        return [value.strip() for value in row.locator("ul li").all_inner_texts()
                if value.strip() and value.strip() != "None."]

    missing = skills("Missing from resume")
    weak = skills("Needs stronger evidence")
    return _result(
        "resumeatlas", score, page, missing + weak,
        weak_evidence=weak, matched=skills("Already demonstrated"),
        parser_version=3, jd_word_limit=650, report=report.inner_text(),
    )


def _novoresume_profile(resume_pdf: Path) -> tuple[str, str]:
    from jobagent.tools import applicant

    address = applicant.answer("Current address") or {}
    country = address.get("country", "") if isinstance(address, dict) else ""
    years = applicant.answer("Years of relevant experience")
    tex = resume_pdf.with_suffix(".tex")
    if tex.exists():
        experience = applicant.resume_experience(tex.read_text())
        if experience["roles"]:
            years = experience["years"]
    if not country or isinstance(years, bool) or not isinstance(years, (int, float)) or years < 0:
        raise AutomationBlocked(
            "Novoresume: requires the applicant's address country and dated resume experience "
            "(or a numeric 'Years of relevant experience' answer)", "profile_required",
        )
    level = "Senior" if years >= 6 else "Intermediate" if years >= 3 else "Graduate" if years > 0 else "Student"
    country = {
        "us": "United States of America", "usa": "United States of America",
        "united states": "United States of America", "uk": "United Kingdom",
        "gb": "United Kingdom", "ca": "Canada",
    }.get(country.strip().casefold(), country.strip())
    return country, level


def novoresume_result(page) -> dict:
    # Scope to the completed report: the landing page also has a sample 76/100 score.
    hero = page.locator('section[aria-labelledby="resume-hero-title"]')
    score = _score(hero.inner_text(), r"Match Score\s+(\d{1,3})\s*/\s*100", "Novoresume")
    advice = page.locator('article[aria-labelledby="fix-list-title"] li').all_inner_texts()
    return _result(
        "novoresume", score, page, advice=advice,
        report=hero.inner_text() + "\n" + page.locator('#resume-job-matcher-results-title').locator(
            'xpath=ancestor::section[1]',
        ).inner_text(),
        # Eligibility gaps and "nice-to-haves" are not a list of missing skills to insert.
        critical_gaps=page.locator('article[aria-labelledby="critical-gaps-title"] li').all_inner_texts(),
    )


def novoresume(page, resume_pdf: Path, jd_text: str, title="", company="") -> dict:
    country, level = _novoresume_profile(resume_pdf)
    page.goto("https://novoresume.com/tools/resume-job-matcher", wait_until="domcontentloaded")
    consent = page.locator('label[aria-label="Accept terms"]')
    upload = page.locator('input[type="file"]').first
    # The input is server-rendered before its upload handler is hydrated. A fast first
    # upload can be lost; retry the local file selection once before starting a scan.
    for attempt in range(2):
        upload.set_input_files(str(resume_pdf))
        try:
            consent.wait_for(state="visible", timeout=10_000)
            break
        except PlaywrightTimeout:
            if attempt:
                raise
            upload.set_input_files([])
    cookies = page.get_by_role("button", name="Accept Necessary", exact=True)
    if cookies.count() and cookies.is_visible():
        cookies.click()
    if not consent.locator('input[type="checkbox"]').is_checked():
        consent.locator('> span[aria-hidden="true"]').click(no_wait_after=True)
    page.get_by_role("button", name="Continue", exact=True).click(no_wait_after=True)
    page.locator("#country-input").fill(country)
    page.locator('li[role="option"]').filter(has_text=re.compile(rf"^{re.escape(country)}$", re.I)).click(
        timeout=15_000, no_wait_after=True,
    )
    # HeroUI exposes a native select for form/autofill events. Using it avoids the
    # previous country popup intercepting a click during its closing animation.
    page.locator('select[name="experience"]').select_option(level, force=True)
    dialog = page.get_by_role("dialog")
    dialog.get_by_role("button", name="Continue", exact=True).click(no_wait_after=True)
    dialog.locator("textarea").fill(jd_text)
    dialog.get_by_role("button", name="Get My Match Score", exact=True).click(no_wait_after=True)
    _wait_report(
        page, "Novoresume",
        lambda text: page.locator('section[aria-labelledby="resume-hero-title"]').is_visible()
        and page.locator('article[aria-labelledby="fix-list-title"]').is_visible(),
        timeout_s=180,
    )
    result = novoresume_result(page)
    result["breakdown"]["experience_level"] = level
    return result


_JOBSCAN_ROWS = r"""() => {
  const out = {};
  for (const name of ['hardSkills', 'softSkills']) {
    const panel = document.getElementById(name + '-comparison-panel');
    out[name] = panel ? [...panel.querySelectorAll('[data-test="skill-row"]')]
      .filter(row => row.getClientRects().length).map(row => {
      const label = row.querySelector('[data-test="skill-name"]');
      const cells = row.querySelectorAll('[role="cell"]');
      return {
        name: label ? label.innerText.trim() : '',
        missing: !!row.querySelector('[aria-label="Skill not found"]'),
        locked: !!row.querySelector('.hazy, .upgrade-badge'),
        resume: cells[1] ? cells[1].innerText.trim() : '',
        job: cells[2] ? cells[2].innerText.trim() : ''
      };
    }) : [];
  }
  return out;
}"""


def jobscan_result(page) -> dict:
    if "/match-report/" not in page.url:
        raise RuntimeError("Jobscan: the current page is not a completed match report")
    text = _text(page)
    score = _score(text, r"\bMatch Rate\s+(\d{1,3})(?!\d)", "Jobscan")
    sections = page.evaluate(_JOBSCAN_ROWS)
    hard, soft = sections["hardSkills"], sections["softSkills"]
    usable = lambda row: row["name"] and not row["locked"] and row["name"] != "Upgrade to view"
    missing = [r["name"] for r in hard if usable(r) and r["missing"]]
    soft_missing = [r["name"] for r in soft if usable(r) and r["missing"]]
    advice = []
    if soft_missing:
        advice.append("Show supported examples of these soft skills in the work: " + ", ".join(soft_missing))
    if "experience is less than the role requires" in text:
        advice.append("Explain the relevance of existing experience clearly; do not add unsupported years.")
    return _result(
        "jobscan", score, page, missing, advice,
        parser_version=2,
        limited_feedback=any(r["locked"] for r in hard + soft),
        matched=[r["name"] for r in hard if usable(r) and not r["missing"]],
        report=text,
    )


def _wait_jobscan_report(page) -> None:
    previous = None
    unchanged_since = time.monotonic()

    def stable(text):
        nonlocal previous, unchanged_since
        match = re.search(r"Match Rate\s+(\d{1,3})(?!\d)", text)
        if "/match-report/" not in page.url or not match:
            return False
        # The report animates its score upward. It can already contain all skill rows
        # while the displayed number is still changing, so wait for it to settle.
        current = (int(match[1]), text.split("Resume Report", 1)[-1])
        if current != previous:
            previous, unchanged_since = current, time.monotonic()
            return False
        return time.monotonic() - unchanged_since >= 2

    _wait_report(page, "Jobscan", stable)


def jobscan(page, resume_pdf: Path, jd_text: str, title="", company="") -> dict:
    page.goto("https://app.jobscan.co/dashboard", wait_until="domcontentloaded")
    google_login(page, "app.jobscan.co", lambda p: p.locator("#jobDescriptionInput").count() > 0)
    check_scan_blockers(_text(page), "Jobscan")
    got_it = page.get_by_role("button", name="Got it", exact=True)
    if got_it.count() and got_it.first.is_visible():
        got_it.first.click()
    page.locator("#jobDescriptionInput").fill(jd_text)
    page.locator("#file").set_input_files(str(resume_pdf))
    page.get_by_role("button", name="Scan", exact=True).first.click(timeout=30_000)
    _wait_jobscan_report(page)
    dismiss = page.get_by_role("button", name="Dismiss", exact=True)
    if dismiss.count() and dismiss.first.is_visible():
        dismiss.first.click()
    return jobscan_result(page)


def skillsyncer_result(page) -> dict:
    text = _text(page)
    lines = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    score = _score(lines, r"Match Report\s+(\d{1,3})\s*%", "SkillSyncer")
    sections = {}
    for heading, following in (
        ("HARD SKILLS", "SOFT SKILLS"),
        ("SOFT SKILLS", "OTHER KEYWORDS"),
        ("OTHER KEYWORDS", "Job Title Match"),
    ):
        match = re.search(rf"(?s){heading}\n(.*?)(?={following}\n|$)", lines)
        sections[heading] = [
            {"name": row[0], "percent": int(row[1]), "resume": int(row[2]), "job": int(row[3])}
            for row in re.findall(r"(?m)^([^\n]+)\n(\d+)%\n(\d+)\n(\d+)(?=\n|$)", match[1] if match else "")
        ]
    hard = sections["HARD SKILLS"] + sections["OTHER KEYWORDS"]
    soft = [row["name"] for row in sections["SOFT SKILLS"] if row["resume"] == 0]
    advice = ["Show supported examples of these soft skills in the work: " + ", ".join(soft)] if soft else []
    return _result(
        "skillsyncer", score, page,
        [row["name"] for row in hard if row["resume"] == 0], advice,
        matched=[row["name"] for row in hard if row["resume"] > 0],
        frequency_shortfalls=[row for row in hard if 0 < row["resume"] < row["job"]],
        report=text.split("JOB DESCRIPTION", 1)[0],
    )


def _paste_rich_text(page, editor, text):
    """Use the editor's normal paste/key handlers so its model receives the text."""
    editor.click()
    editor.press('ControlOrMeta+A')
    try:
        page.evaluate('text => navigator.clipboard.writeText(text)', text)
    except PlaywrightError:
        # Clipboard writes can be unavailable in a browser context. Real key
        # events still exercise the editor's transaction handlers; DOM fill does not.
        editor.press_sequentially(text, delay=0)
    else:
        editor.press('ControlOrMeta+V')
    expected=re.compile(r'^\s*'+r'\s+'.join(re.escape(w) for w in text.split())+r'\s*$')
    expect(editor).to_have_text(expected, use_inner_text=True, timeout=10000)
    editor.blur()
    expect(editor).to_have_text(expected, use_inner_text=True, timeout=10000)
    return editor.inner_text()


def skillsyncer(page, resume_pdf: Path, jd_text: str, title="", company="") -> dict:
    if not title or not company:
        raise ValueError("SkillSyncer requires the posting's job title and company")
    page.goto("https://app.skillsyncer.com/scans/create", wait_until="domcontentloaded")
    google_login(
        page, "app.skillsyncer.com",
        lambda p: "app.skillsyncer.com" in p.url and not re.search(r"/auth/", p.url)
        and (p.get_by_text("Free plan", exact=True).count() > 0
             or p.get_by_role("button", name="Skip for now", exact=True).count() > 0),
    )
    skip = page.get_by_role("button", name="Skip for now", exact=True)
    if skip.count() and skip.first.is_visible():
        skip.first.click()
        page.goto("https://app.skillsyncer.com/scans/create", wait_until="domcontentloaded")
    page.get_by_role("button", name=re.compile(r"^Classic Scan")).click(timeout=25_000)
    check_scan_blockers(_text(page), "SkillSyncer")
    # The remaining credit count is displayed in the sidebar, not in marketing copy.
    if re.search(r"Scans:\s*0\b", _text(page)):
        raise AutomationBlocked("SkillSyncer: no scan credits left", "quota_exhausted")
    page.locator("#company").fill(company)
    page.locator("#job_title").fill(title)
    # Upload first: its editor hydration can replace the other editor's state.
    page.locator('input[type="file"]').last.set_input_files(str(resume_pdf))
    page.wait_for_function(
        "() => [...document.querySelectorAll('[contenteditable=true]')].at(-1)?.innerText.trim().length > 50",
        timeout=30_000,
    )
    actual=_paste_rich_text(page,page.locator('[contenteditable="true"]').first,jd_text)
    from jobagent.tools import ats_sites
    ats_sites._payload.last=ats_sites.payload_record('skillsyncer',jd_text,actual)
    page.get_by_role("button", name="Create Scan", exact=True).first.click(timeout=20_000)
    def completed(text):
        if re.search(r'(?i)this is too short to be a job posting',text):
            raise RuntimeError('SkillSyncer rejected the job-description editor; no score was produced')
        modal=page.get_by_role('button',name='Close modal',exact=True)
        return (not modal.count() or not modal.is_visible()) and bool(re.search(r'Match Report\s+\d+\s*%',text))
    _wait_report(page, "SkillSyncer", completed)
    return skillsyncer_result(page)


def atsalign_result(page) -> dict:
    text = _text(page)
    score = _score(text, r"Job Match\s+(\d{1,3})\s*/\s*100", "ATSAlign")
    covered = re.findall(
        r"(?m)^([^\n:]+): (?:Critical|Important|Preferred), (not covered|partially covered|covered) by your resume\.",
        text,
    )
    advice = [
        "Improve this existing bullet or project without adding unsupported facts: " + " ".join(block.split())
        for block in re.findall(r"(?s)(?:Could be stronger\.|Fix first\.)\s*(.*?)(?=Edit\s+Rewrite with AI|$)", text)
        if block.strip()
    ]
    return _result(
        "atsalign", score, page,
        [name for name, status in covered if status != "covered"], advice,
        matched=[name for name, status in covered if status == "covered"],
        limited_feedback="View all skills" in text, report=text,
    )


def atsalign(page, resume_pdf: Path, jd_text: str, title="", company="") -> dict:
    page.goto("https://www.atsalign.com/start", wait_until="domcontentloaded")
    google_login(
        page, "www.atsalign.com",
        lambda p: p.locator("#start-jd-text").count() > 0
        or p.get_by_text("Free plan", exact=True).count() > 0,
    )
    if not page.locator("#start-jd-text").count():
        page.goto("https://www.atsalign.com/start", wait_until="domcontentloaded")
    page.locator("#start-file").set_input_files(str(resume_pdf))
    page.locator("#start-jd-text").fill(jd_text)
    check_scan_blockers(_text(page), "ATSAlign")
    page.get_by_role("button", name="Get my ATS score", exact=True).click(timeout=30_000)
    _wait_report(page, "ATSAlign", lambda text: bool(re.search(r"Job Match\s+\d+\s*/\s*100", text)))
    return atsalign_result(page)


def applycove_result(page) -> dict:
    dialog = page.get_by_role("dialog", name="ATS resume report")
    text = dialog.inner_text(timeout=15_000)
    check_scan_blockers(text, "ApplyCove")
    score = _score(text, r"(?:keyword match|job (?:description )?match)\s*:?\s*(\d{1,3})\s*%", "ApplyCove")
    match = re.search(r"(?is)missing (?:keywords|terms)\s*:?\s*(.*?)(?=\n\n|$)", text)
    missing = [x.strip(" •\n") for x in re.split(r",|\n", match[1])] if match else []
    return _result("applycove", score, page, missing, report=text)


def applycove(page, resume_pdf: Path, jd_text: str, title="", company="") -> dict:
    page.goto("https://applycove.com/ats-resume-checker/", wait_until="domcontentloaded")
    page.get_by_role("button", name="Add a job description for a keyword match", exact=True).click(timeout=25_000)
    page.locator("textarea:visible").first.fill(jd_text)
    page.locator('input[type="file"]').first.set_input_files(str(resume_pdf))
    dialog = page.get_by_role("dialog", name="ATS resume report")
    dialog.wait_for(state="visible", timeout=20_000)
    def ready(current):
        report = current.get_by_role("dialog", name="ATS resume report")
        return report.count() > 0 and bool(re.search(
            r"(?i)(?:keyword match|job (?:description )?match)\s*:?\s*\d+\s*%", report.inner_text(),
        ))

    try:
        _wait_report(page, "ApplyCove", lambda text: ready(page))
    except AutomationBlocked as exc:
        if exc.status != "human_required":
            raise
        human_handoff(
            page, "ApplyCove",
            "background verification failed; retry the scan manually and complete a challenge only if the site shows one",
            ready,
        )
    return applycove_result(page)
