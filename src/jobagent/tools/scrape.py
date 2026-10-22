"""Scrape a job posting into a JobDescription, learning per-portal notes as it goes.

Pipeline: navigate (backoff + challenge hand-off) → schema.org JobPosting JSON-LD if present → the main text
block (remembered selector first, then known candidates) → vision fallback if the text is too thin →
LLM extraction into JobDescription (copy facts, never invent) → jd.json / jd.md + a `jobs` row.
"""

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from html import unescape
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from playwright.sync_api import Page

from jobagent import db, llm
from jobagent.config import get_settings
from jobagent.state import JobDescription
from jobagent.tools import challenge
from jobagent.tools.hitl import Hitl
from jobagent.tools.memory import PortalMemory, portal_key

# Where the description usually lives, per portal family (tried after the remembered selector).
CANDIDATE_SELECTORS = [
    '[data-automation-id="jobPostingDescription"]',        # Workday
    ".job__description", "#content .job-post", "#app_body",  # Greenhouse
    ".posting-page", ".section-wrapper.page-full-width",     # Lever
    '[class*="ashby-job-posting-right-pane"]', '[class*="_descriptionText"]',  # Ashby
    "#job-detail-body", ".job-detail", "#job-description", ".job-description", '[class*="jobDescription"]',
    "article", "main", '[role="main"]',
]
MIN_TEXT = 800          # below this, the page text is too thin to trust
MAX_LLM_CHARS = 24_000

EXTRACT_PROMPT = """You extract job postings into a schema. Rules:
- Copy facts from the posting; never invent or infer pay, locations, sponsorship or requirements that aren't stated.
- Unknown → null or an empty list.
- One list item per bullet/requirement, lightly trimmed, keeping the posting's wording.
- technical_skills: concrete technologies, languages, frameworks, tools and platforms named anywhere in the posting.
- Separate basic/minimum/required qualifications from preferred/nice-to-have ones as the posting does."""


@dataclass
class ScrapeResult:
    jd: JobDescription
    job_id: str
    out_dir: Path
    portal: str
    used_memory: bool
    selector: str | None
    text_chars: int
    via_vision: bool


def _jsonld_jobposting(page: Page) -> dict | None:
    blocks = page.eval_on_selector_all('script[type="application/ld+json"]', "els => els.map(e => e.textContent)")
    for raw in blocks:
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        for item in data if isinstance(data, list) else [data, *data.get("@graph", [])] if isinstance(data, dict) else []:
            if isinstance(item, dict) and "JobPosting" in str(item.get("@type", "")):
                return item
    return None


def _jsonld_text(posting: dict) -> str:
    """Flatten the useful JSON-LD fields into text the LLM (and the length check) can use."""
    desc = re.sub(r"<[^>]+>", "\n", unescape(str(posting.get("description", ""))))
    desc = re.sub(r"\n\s*\n+", "\n", desc).strip()
    hints = {k: posting.get(k) for k in ("title", "datePosted", "validThrough", "employmentType", "identifier",
                                          "hiringOrganization", "jobLocation", "jobLocationType", "baseSalary",
                                          "applicantLocationRequirements") if posting.get(k)}
    return f"[schema.org JobPosting fields]\n{json.dumps(hints, default=str)[:3000]}\n\n[description]\n{desc}"


def _best_text(page: Page, remembered: str | None) -> tuple[str | None, str]:
    """Return (selector, text) of the richest visible block, trying the remembered selector first."""
    tried = ([remembered] if remembered else []) + [s for s in CANDIDATE_SELECTORS if s != remembered]
    best: tuple[str | None, str] = (None, "")
    for sel in tried:
        loc = page.locator(sel)
        if not loc.count():
            continue
        try:
            text = loc.first.inner_text(timeout=3_000).strip()
        except Exception:
            continue
        if sel == remembered and len(text) >= MIN_TEXT:
            return sel, text  # the notes still hold: no need to search
        if len(text) > len(best[1]) * 1.15:  # prefer the earlier (more specific) selector unless clearly richer
            best = (sel, text)
    if len(best[1]) < MIN_TEXT:
        body = page.locator("body").inner_text(timeout=5_000).strip()
        if len(body) > len(best[1]):
            best = ("body", body)
    return best


def _vision_text(page: Page) -> str:
    shot = page.screenshot(full_page=True)
    msg = llm.chat("vision").invoke([HumanMessage(content=[
        {"type": "text", "text": "Transcribe all the job posting text in this screenshot, keeping headings and "
                                 "bullets. Output only the text."},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(shot).decode()}},
    ])])
    return str(msg.content)


def job_id_for(url: str, jd: JobDescription) -> str:
    base = jd.req_id or hashlib.sha1(url.encode()).hexdigest()[:10]
    return re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip("-")[:60]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "unknown"


def save(jd: JobDescription, url: str) -> tuple[str, Path]:
    s = get_settings()
    job_id = job_id_for(url, jd)
    out = s.outputs_dir / _slug(jd.company) / job_id
    out.mkdir(parents=True, exist_ok=True)
    (out / "jd.json").write_text(jd.model_dump_json(indent=2))
    md = [f"# {jd.title} — {jd.company}", "", f"- URL: {url}", f"- Locations: {', '.join(jd.locations) or '—'}",
          f"- Workplace / type / seniority: {jd.workplace_type or '—'} / {jd.employment_type or '—'} / {jd.seniority or '—'}",
          f"- Pay: {jd.pay_range.raw if jd.pay_range and jd.pay_range.raw else '—'}",
          f"- Sponsorship: {jd.visa_sponsorship or '—'}", "", jd.role_summary or "", ""]
    for title, items in (("Responsibilities", jd.responsibilities), ("Basic qualifications", jd.basic_qualifications),
                         ("Preferred qualifications", jd.preferred_qualifications),
                         ("Technical skills", jd.technical_skills), ("What they look for", jd.values)):
        if items:
            md += [f"## {title}", ""] + [f"- {i}" for i in items] + [""]
    (out / "jd.md").write_text("\n".join(md))
    conn = db.connect()
    with conn:
        conn.execute(
            "INSERT INTO jobs (url, job_id, company, title, location, portal, jd_json, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'scraped') ON CONFLICT(url) DO UPDATE SET job_id=excluded.job_id, "
            "company=excluded.company, title=excluded.title, location=excluded.location, portal=excluded.portal, "
            "jd_json=excluded.jd_json, updated_at=strftime('%Y-%m-%dT%H:%M:%SZ', 'now')",
            (url, job_id, jd.company, jd.title, "; ".join(jd.locations), jd.portal, jd.model_dump_json()),
        )
    return job_id, out


def scrape(page: Page, url: str, hitl: Hitl, log=print) -> ScrapeResult:
    portal = portal_key(url)
    memory = PortalMemory(portal)
    used_memory = memory.known
    if used_memory:
        log(f"scrape: {portal}: using notes (selector {memory.data['content_selector']!r}, "
            f"JSON-LD {memory.data['has_jsonld']}, settle {memory.data['settle_ms']} ms)")
    challenge.goto(page, url, hitl, log, wait_until="domcontentloaded")
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except Exception:
        pass
    page.wait_for_timeout(memory.data["settle_ms"])
    posting = _jsonld_jobposting(page)
    selector, text = _best_text(page, memory.data["content_selector"])
    via_vision = False
    if len(text) < MIN_TEXT and not posting:
        log(f"scrape: only {len(text)} chars of page text; reading the page with the vision model")
        text, selector, via_vision = _vision_text(page), "vision", True
        memory.note("page text too thin; needed the vision model")
    source = (_jsonld_text(posting) + "\n\n" if posting else "") + f"[page text]\n{text}"
    jd = llm.structured(JobDescription, "planner").invoke([
        SystemMessage(EXTRACT_PROMPT),
        HumanMessage(f"Posting URL: {url}\n\n{source[:MAX_LLM_CHARS]}"),
    ])
    jd.source_url, jd.portal = url, portal
    jd.apply_url = jd.apply_url or (posting or {}).get("url") or url
    missing = jd.missing_core()
    if missing:
        memory.note(f"extraction missed {', '.join(missing)} at {url}")
    if selector and selector not in ("body", "vision") and memory.data["content_selector"] != selector:
        memory.note(f"job description lives in `{selector}`")
    memory.record_visit(url, ok=not missing, content_selector=selector if selector not in ("vision",) else None,
                        has_jsonld=posting is not None)
    job_id, out = save(jd, url)
    log(f"scrape: {jd.title} @ {jd.company} → {out} ({'complete' if not missing else 'missing ' + ', '.join(missing)})")
    return ScrapeResult(jd, job_id, out, portal, used_memory, selector, len(text), via_vision)
