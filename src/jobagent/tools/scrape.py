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
from jobagent.tools import challenge, jdtext
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
- Separate basic/minimum/required qualifications from preferred/nice-to-have ones as the posting does.
- company: the employer's brand name as people know it ("NVIDIA", "General Mills"), never a legal entity,
  subsidiary code or payroll name ("2100 NVIDIA USA", "Genl Mills India P")."""


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


def jsonld_locations(posting: dict) -> list[str]:
    places = posting.get("jobLocation") or []
    out = []
    for place in places if isinstance(places, list) else [places]:
        addr = place.get("address", {}) if isinstance(place, dict) else {}
        if isinstance(addr, str):
            out.append(addr)
            continue
        parts = [addr.get(k) for k in ("addressLocality", "addressRegion", "addressCountry")]
        parts = [p.get("name") if isinstance(p, dict) else p for p in parts]
        if text := ", ".join(p for p in parts if p):
            out.append(text)
    if not out and "TELECOMMUTE" in str(posting.get("jobLocationType", "")).upper():
        out.append("Remote")
    return list(dict.fromkeys(out))


def _fill_from_jsonld(jd: JobDescription, posting: dict) -> None:
    if not jd.locations:
        jd.locations = jsonld_locations(posting)
    if not jd.title and posting.get("title"):
        jd.title = str(posting["title"])
    org = posting.get("hiringOrganization")
    if not jd.company and isinstance(org, dict) and org.get("name"):
        jd.company = str(org["name"])


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


# Heading → JD list. First match wins; order matters ("preferred qualifications" before "qualifications").
SECTION_RULES = [
    ("preferred_qualifications", re.compile(r"(?i)prefer|nice[- ]to[- ]have|bonus|plus\b|ideal(ly)?|desired|stand out")),
    ("basic_qualifications", re.compile(r"(?i)minimum|basic|require|must[- ]have|qualifications|need to see|"
                                        r"what you('ll)? (need|bring)|you (have|bring)|skills (and|&) experience")),
    ("responsibilities", re.compile(r"(?i)responsib|what you('ll| will)? (be )?do(ing)?\b|your role|the role|in this role|"
                                    r"day[- ]to[- ]day|you will|key duties|duties|what you('ll| will) work on")),
    ("benefits", re.compile(r"(?i)benefit|perks|we offer|compensation (and|&) benefits|what we offer|you('ll)? enjoy")),
    ("technical_skills", re.compile(r"(?i)technolog(y|ies)( we use)?|tech stack|our stack|tools (we use|and technologies)")),
    ("values", re.compile(r"(?i)who you are|about you|we('re| are) looking for|what we look for|our values|"
                          r"what we value|traits")),
]


def classify_heading(heading: str) -> str | None:
    heading = heading.replace("\u2019", "'").replace("\u2018", "'")  # "What you’ll be doing"
    for field_name, pattern in SECTION_RULES:
        if pattern.search(heading):
            return field_name
    return None


def _sections(page: Page, selector: str | None) -> list[dict]:
    """Bullets on the page, each with the nearest heading above it (h1-h6, or a bold/strong lead line)."""
    return page.evaluate(
        r"""(sel) => {
            const root = (sel && sel !== 'body' && sel !== 'vision' && document.querySelector(sel)) || document.body;
            const isHeading = el => /^H[1-6]$/.test(el.tagName) ||
                ((el.tagName === 'STRONG' || el.tagName === 'B') && (el.innerText || '').trim().length < 80) ||
                (el.tagName === 'P' && el.children.length === 1 && /^(STRONG|B)$/.test(el.children[0].tagName) &&
                 (el.innerText || '').trim().length < 80);
            const out = []; let heading = '';
            const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
            for (let el = walker.currentNode; el; el = walker.nextNode()) {
                if (isHeading(el) && !el.closest('li')) heading = (el.innerText || '').trim().replace(/:$/, '');
                else if (el.tagName === 'LI') {
                    const t = (el.innerText || '').trim().replace(/\s+/g, ' ');
                    if (t && !el.querySelector('li')) out.push({heading, text: t});
                }
            }
            return out;
        }""",
        selector,
    )


def apply_sections(jd: JobDescription, bullets: list[dict]) -> list[str]:
    """Replace JD lists with the page's own bullets wherever their heading is recognised. Returns unmapped headings."""
    grouped: dict[str, list[str]] = {}
    unmapped: list[str] = []
    for b in bullets:
        field_name = classify_heading(b["heading"]) if b["heading"] else None
        if field_name:
            items = grouped.setdefault(field_name, [])
            if b["text"] not in items:
                items.append(b["text"])
        elif b["heading"] and b["heading"] not in unmapped:
            unmapped.append(b["heading"])
    for field_name, items in grouped.items():
        setattr(jd, field_name, items)
    # Lists the page didn't cover keep the model's version, minus items the model misfiled there that
    # are really page bullets of another list (e.g. Lever's "What We Value" also put under preferred).
    page_items = {_norm_item(t) for items in grouped.values() for t in items}
    for field_name in ("responsibilities", "basic_qualifications", "preferred_qualifications", "values", "benefits"):
        if field_name not in grouped:
            setattr(jd, field_name, [t for t in getattr(jd, field_name) if _norm_item(t) not in page_items])
    return unmapped


def completeness_problems(jd: JobDescription, secs: list, labels: dict[int, str], jd_text: str, page_text: str) -> list[str]:
    """Checks that don't trust the model's own labels."""
    problems = []
    reqs = len(jd.basic_qualifications) + len(jd.preferred_qualifications)
    labelled = sum(len(s.items) for i, s in enumerate(secs)
                   if labels.get(i) in ("basic_qualifications", "preferred_qualifications"))
    if labelled and reqs < labelled:
        problems.append("requirement items from the job description")
    # independent signal: heading rules see a requirements section, but nothing ended up in the lists
    if not reqs and any(classify_heading(s.heading) in ("basic_qualifications", "preferred_qualifications")
                        for s in secs if s.heading and s.items):
        problems.append("requirements (a requirements heading exists)")
    if not reqs and not jd.responsibilities:
        problems.append("both responsibilities and qualifications")
    if len(page_text) >= 2 * MIN_TEXT and len(jd_text) < 0.5 * len(page_text):
        problems.append(f"jd.txt looks short ({len(jd_text)} of {len(page_text)} page chars)")
    return problems


def _chars(blocks: list) -> int:
    return sum(len(b.text) for b in blocks)


def choose_source(dom: list, ld: list) -> list:
    """Pick the fuller copy of the job description. The page's own description element keeps the headings and
    is preferred unless it's clearly thinner: JSON-LD descriptions are often just an overview (Cafe Valley) or
    lose heading markup (Lever)."""
    if not ld:
        return dom
    if not dom:
        return ld
    dom_heads, ld_heads = sum(b.kind == "h" for b in dom), sum(b.kind == "h" for b in ld)
    if _chars(dom) >= 0.8 * _chars(ld) or (dom_heads > ld_heads and _chars(dom) >= 0.6 * _chars(ld)):
        return dom
    return ld


LIST_CATEGORIES = ("basic_qualifications", "preferred_qualifications", "responsibilities", "values", "benefits",
                   "technical_skills")


def apply_labeled(jd: JobDescription, secs: list, labels: dict[int, str]) -> None:
    """Fill JD lists with the items of the sections labelled for them, verbatim and in order. Lists no section
    covers keep the model's version, minus items that are really another section's (misfiled)."""
    grouped: dict[str, list[str]] = {}
    for i, sec in enumerate(secs):
        cat = labels.get(i)
        if cat in LIST_CATEGORIES:
            items = grouped.setdefault(cat, [])
            items += [t for t in sec.items if t not in items]
    if "technical_skills" in grouped:  # keep skills the model found elsewhere in the text too
        grouped["technical_skills"] += [s for s in jd.technical_skills if s not in grouped["technical_skills"]]
    for cat, items in grouped.items():
        setattr(jd, cat, items)
    page_items = {_norm_item(t) for items in grouped.values() for t in items}
    for cat in LIST_CATEGORIES:
        if cat not in grouped:
            setattr(jd, cat, [t for t in getattr(jd, cat) if _norm_item(t) not in page_items])
    if not jd.role_summary:
        summary = next((" ".join(s.paragraphs) for i, s in enumerate(secs) if labels.get(i) == "role_summary"), "")
        jd.role_summary = summary[:1500] or None


def _norm_item(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()[:120]


def _vision_text(page: Page) -> str:
    shot = page.screenshot(full_page=True)
    msg = llm.chat("vision").invoke([HumanMessage(content=[
        {"type": "text", "text": "Transcribe all the job posting text in this screenshot, keeping headings and "
                                 "bullets. Output only the text."},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(shot).decode()}},
    ])])
    return str(msg.content)


_URL_IDS = [
    re.compile(r"[?&](?:gh_jid|jobId|job_id|jid|opportunityId|req(?:uisition)?Id|id)=([A-Za-z0-9-]+)", re.I),
    re.compile(r"_((?:JR|R|REQ)?\d{5,}(?:-\d+)?)(?:[/?#]|$)", re.I),   # Workday ..._JR2026744
    re.compile(r"/jobs?/(\d{4,})(?:[/?#-]|$)", re.I),                    # /jobs/8130881, /jobs/10585694/slug
    re.compile(r"/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?:[/?#]|$)", re.I),  # Lever/Ashby
    re.compile(r"-(\d{5,})/?(?:[?#]|$)"),                                # cafevalleyjobs.com/...-179452/
]


def job_id_for(url: str, jd: JobDescription | None = None) -> str:
    """A stable folder id from the URL (never from the model, whose req id can vary between runs)."""
    for pattern in _URL_IDS:
        if m := pattern.search(url):
            return m.group(1)[:60]
    return hashlib.sha1(url.encode()).hexdigest()[:10]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "unknown"


def save(jd: JobDescription, url: str, jd_text: str = "") -> tuple[str, Path]:
    s = get_settings()
    job_id = job_id_for(url, jd)
    out = s.outputs_dir / _slug(jd.company) / job_id
    out.mkdir(parents=True, exist_ok=True)
    (out / "jd.json").write_text(jd.model_dump_json(indent=2))
    if jd_text:  # the posting text exactly as written: what the ATS step submits
        (out / "jd.txt").write_text(jd_text)
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
            "INSERT INTO jobs (url, job_id, company, title, location, portal, jd_json, jd_text, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'scraped') ON CONFLICT(url) DO UPDATE SET job_id=excluded.job_id, "
            "company=excluded.company, title=excluded.title, location=excluded.location, portal=excluded.portal, "
            "jd_json=excluded.jd_json, jd_text=excluded.jd_text, updated_at=strftime('%Y-%m-%dT%H:%M:%SZ', 'now')",
            (url, job_id, jd.company, jd.title, "; ".join(jd.locations), jd.portal, jd.model_dump_json(), jd_text),
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
    # jd.txt: the posting's description exactly as written (no model). The JSON-LD description is the cleanest
    # copy of the body when it's substantial; otherwise the page's description element.
    blocks = choose_source(
        dom=jdtext.blocks(page, selector=selector if selector not in (None, "vision") else "body") if text else [],
        ld=jdtext.blocks(page, html=unescape(str((posting or {}).get("description") or ""))) if posting else [],
    )
    jd_text = jdtext.render(blocks) if blocks else ""
    if len(jd_text) < MIN_TEXT and not posting:
        log(f"scrape: only {len(text)} chars of page text; reading the page with the vision model")
        text, selector, via_vision = _vision_text(page), "vision", True
        jd_text, blocks = text.strip() + "\n", []
        memory.note("page text too thin; needed the vision model")
    for href, label in jdtext.attachment_links(page, selector):
        try:
            got = page.request.get(href, timeout=60_000)
            attached = jdtext.attachment_text(got.body(), href) if got.ok else ""
        except Exception as e:
            attached, label = "", f"{label} (download failed: {type(e).__name__})"
        jd_text += f"\n[Attachment: {label}]\n{attached or '(could not convert this file to text)'}\n"
        if attached:
            blocks += [jdtext.Block("h", f"Attachment: {label}")] + [
                jdtext.Block("p", line.strip()) for line in attached.splitlines() if line.strip()]
        memory.note("postings can link the job description as a file")
    source = (_jsonld_text(posting).split("[description]")[0] + "\n" if posting else "") + f"[job description]\n{jd_text}"
    messages = [SystemMessage(EXTRACT_PROMPT), HumanMessage(f"Posting URL: {url}\n\n{source[:MAX_LLM_CHARS]}")]
    # The fast model (~8x quicker, same quality in the benchmark) does the extraction; the large one is the
    # fallback when it errors or misses title/company/locations.
    try:
        jd = llm.structured(JobDescription, "fast").invoke(messages)
    except Exception as e:
        log(f"scrape: fast model failed ({type(e).__name__}); using the planner model")
        jd = None
    if jd is not None and posting:
        _fill_from_jsonld(jd, posting)  # cheap, exact fixes before paying for the large model
    if jd is None or not (jd.title and jd.company and jd.locations):
        jd = llm.structured(JobDescription, "planner").invoke(messages)
        memory.note("fast model missed core fields; planner model used")
    jd.source_url, jd.portal = url, portal
    jd.apply_url = jd.apply_url or (posting or {}).get("url") or url
    # Sections of jd.txt, labelled by the fast model (content, not just heading wording), fill the lists verbatim.
    secs = jdtext.sections(blocks)
    labels = jdtext.classify_sections(secs, log) if secs else {}
    apply_labeled(jd, secs, labels)
    unlabeled = [s.heading for i, s in enumerate(secs) if labels.get(i) == "other" and s.heading]
    if unlabeled:
        memory.note(f"sections labelled 'other': {', '.join(unlabeled[:5])}")
    missing = jd.missing_core() + completeness_problems(jd, secs, labels, jd_text, text)
    if missing:
        memory.note(f"extraction missed {', '.join(missing)} at {url}")
    if selector and selector not in ("body", "vision") and memory.data["content_selector"] != selector:
        memory.note(f"job description lives in `{selector}`")
    memory.record_visit(url, ok=not missing, content_selector=selector if selector not in ("vision",) else None,
                        has_jsonld=posting is not None)
    job_id, out = save(jd, url, jd_text)
    log(f"scrape: {jd.title} @ {jd.company} → {out} ({'complete' if not missing else 'missing ' + ', '.join(missing)})")
    return ScrapeResult(jd, job_id, out, portal, used_memory, selector, len(jd_text), via_vision)


def already_scraped(url: str) -> JobDescription | None:
    row = db.connect().execute("SELECT jd_json FROM jobs WHERE url = ? AND jd_json IS NOT NULL", (url,)).fetchone()
    return JobDescription.model_validate_json(row[0]) if row else None


@dataclass
class BatchItem:
    url: str
    result: ScrapeResult | None = None
    cached: JobDescription | None = None
    error: str | None = None
    seconds: float = 0.0


def scrape_many(urls: list[str], hitl: Hitl, workers: int = 5, refresh: bool = False, log=print) -> list[BatchItem]:
    """Scrape postings in parallel. Public postings don't need the agent's signed-in profile, so each worker
    thread runs its own Chrome with a throwaway session (Playwright's sync API is per-thread). Postings already
    in the jobs table are skipped unless `refresh`."""
    import time
    from concurrent.futures import ThreadPoolExecutor

    from playwright.sync_api import sync_playwright

    from jobagent.config import get_settings

    s = get_settings()

    def one(url: str) -> BatchItem:
        # Browser opened and closed in this thread: Playwright's sync objects are bound to their thread.
        item, t0 = BatchItem(url), time.monotonic()
        try:
            if not refresh and (cached := already_scraped(url)):
                item.cached = cached
            else:
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(channel=s.chrome_channel or None, headless=s.headless,
                                                 args=["--window-size=1600,1000"])
                    try:
                        page = browser.new_context(viewport={"width": 1600, "height": 1000}).new_page()
                        item.result = scrape(page, url, hitl, log)
                    finally:
                        browser.close()
        except Exception as e:
            item.error = f"{type(e).__name__}: {str(e)[:200]}"
            log(f"scrape: {url} failed: {item.error}")
        item.seconds = time.monotonic() - t0
        return item

    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(urls)))) as pool:
        return list(pool.map(one, urls))
