"""Find a company's careers page and the applicant-tracking board behind it (step 14a).

1. Web search for exactly "<company> careers"; take the first result on the company's own domain (or its ATS board)
   whose host starts with / contains careers, or whose path contains /careers.
2. Fallback: the company's official homepage; links in its footer (or the last 15% of the page) whose text or href
   says careers / jobs / join us / work with us. If the page needs JavaScript, a throwaway headless Chrome loads it.
3. Last resort: the fast model picks among the search results by the company's domain; the choice is flagged.

The page is then followed (redirects, links, iframes, scripts) to the ATS board it uses (Workday, Greenhouse, Lever,
Ashby, ...), which `listings.py` reads. A page behind a bot check is reported as `blocked`: it is the candidate's to
open, never worked around. The result is cached 30 days in state/companies/<slug>.careers.json.
"""

import json
import re
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
from lxml import html as lh

from jobagent.config import get_settings

UA = {"User-Agent": "Mozilla/5.0 (compatible; JobApplicationAgent/0.1; personal job search)"}
TTL = timedelta(days=30)

AGGREGATORS = re.compile(
    r"(?i)(wikipedia|linkedin|glassdoor|indeed|ziprecruiter|crunchbase|bloomberg|facebook|instagram|twitter|x\.com|"
    r"youtube|reddit|yelp|zoominfo|dnb\.com|builtin|levels\.fyi|comparably|simplyhired|monster|careerbuilder|"
    r"wellfound|angel\.co|owler|forbes|reuters|yahoo|msn|cnbc|wsj|nytimes|bbb\.org|mapquest|jooble|talent\.com|"
    r"adzuna|learn4good|jobrapido|joblist|snagajob|salary\.com|payscale|tiktok|pinterest|medium\.com)\.?")
CAREERS_HOST = re.compile(r"(?i)(^|[.\-])(careers?|jobs?|join)([.\-]|$)|career|jobs")
CAREERS_PATH = re.compile(r"(?i)/(careers?|jobs?|join(-us|-our-team)?|work-(with|at)-us|opportunities|open-roles)(/|$)")
FOOTER_TEXT = re.compile(r"(?i)^\s*(careers?|jobs?|join (us|our team)|work (with|at) us|we['’]?re hiring|"
                         r"opportunities|open (roles|positions)|search jobs|view jobs)\s*$")
FOOTER_HREF = re.compile(r"(?i)/(careers?|jobs?|join-us|work-with-us|work-at)(/|$|\?)|careers?\.|/en/careers")
CHALLENGE = re.compile(r"(?i)just a moment|cf-chl|performing security verification|verify you are human|"
                       r"checking your browser|access denied|px-captcha|datadome")

ATS = [("workday", r"(^|\.)myworkdayjobs\.com$|(^|\.)myworkdaysite\.com$"), ("greenhouse", r"(^|\.)greenhouse\.io$"),
       ("lever", r"(^|\.)lever\.co$"), ("ashby", r"(^|\.)ashbyhq\.com$"),
       ("smartrecruiters", r"(^|\.)smartrecruiters\.com$"), ("icims", r"(^|\.)icims\.com$"),
       ("ultipro", r"(^|\.)ultipro\.com$|(^|\.)ukg\.com$"), ("oracle", r"(^|\.)oraclecloud\.com$"),
       ("taleo", r"(^|\.)taleo\.net$"), ("bamboohr", r"(^|\.)bamboohr\.com$"), ("workable", r"(^|\.)workable\.com$"),
       ("jobvite", r"(^|\.)jobvite\.com$"), ("breezy", r"(^|\.)breezy\.hr$"),
       ("kpmg", r"(^|\.)kpmguscareers\.com$")]


def ats_of(url: str) -> str:
    host = urlparse(url).netloc.lower().split(":")[0]
    return next((name for name, pat in ATS if re.search(pat, host)), "")


def board_of(url: str) -> dict | None:
    """The ATS family and board token of a URL: {'family': 'greenhouse', 'token': 'carvana', 'url': ...}."""
    p = urlparse(url)
    family = ats_of(url)
    if not family:
        return None
    parts = [x for x in p.path.split("/") if x]
    q = parse_qs(p.query)
    token = ""
    if family == "greenhouse":
        token = (q.get("for") or q.get("board") or [""])[0] or next(
            (x for x in parts if x not in ("embed", "v1", "boards", "job_board", "jobs", "job_app")), "")
    elif family in ("lever", "ashby", "smartrecruiters", "workable", "breezy"):
        token = parts[0] if parts else ""
    elif family == "workday":
        host = p.netloc.lower()
        site = next((x for x in parts if not re.fullmatch(r"[a-z]{2}[-_][A-Za-z]{2}", x)), "")
        token = f"{host.split('.')[0]}/{host.split('.')[1] if host.count('.') >= 2 else ''}/{site}"
    elif family == "ultipro":
        if len(parts) >= 3 and parts[1].lower() == "jobboard" and re.fullmatch(r"[\da-fA-F-]{36}", parts[2]):
            token = "/".join(parts[:3])
    elif family == "kpmg":
        token = "us"
    if not token:
        return None
    path = '/' + token if family == "ultipro" else ('/' + parts[0] if parts else '')
    return {"family": family, "token": token, "url": f"{p.scheme}://{p.netloc}{path}"}


def scan_for_board(markup: str, base: str) -> dict | None:
    """The first ATS board linked, framed or scripted in a careers page."""
    try:
        doc = lh.fromstring(markup)
    except Exception:
        return None
    urls = [urljoin(base, u) for u in doc.xpath("//a/@href | //iframe/@src | //script/@src | //link/@href")]
    urls += [urljoin(base, m) for m in re.findall(r"https?://[^\s\"'<>]+", markup)]
    for u in urls:
        if ats_of(u) and (b := board_of(u)):
            return b
    return None


def _words(company: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{3,}", company.lower()) if w not in {"inc", "llc", "corp", "the", "company",
                                                                                  "corporation", "ltd", "llp", "group"}}


def pick_from_search(hits: list[dict], company: str) -> dict | None:
    """First result on the company's own domain or ATS board that looks like a careers page."""
    words = _words(company)
    for hit in hits:
        url = hit.get("href", "")
        p = urlparse(url)
        host = p.netloc.lower()
        if not host or AGGREGATORS.search(host):
            continue
        name = re.sub(r"[^a-z0-9]", "", host + p.path.lower())
        own = any(w in name for w in words) or any(w in re.sub(r"[^a-z0-9]", "", company.lower()) for w in
                                                    [host.split(".")[-2] if host.count(".") else host])
        if own and (CAREERS_HOST.search(host) or CAREERS_PATH.search(p.path) or ats_of(url)):
            return {"url": url, "title": hit.get("title", "")}
    return None


def footer_links(markup: str, base: str) -> list[str]:
    """Careers links in the page's footer (or its last 15% of links), best first: careers host / path before the rest."""
    try:
        doc = lh.fromstring(markup)
    except Exception:
        return []
    anchors = doc.xpath("//footer//a[@href] | //*[@role='contentinfo']//a[@href]")
    if not anchors:
        every = doc.xpath("//a[@href]")
        anchors = every[int(len(every) * 0.85):]
    out = []
    for a in anchors:
        href = urljoin(base, a.get("href"))
        text = " ".join(a.text_content().split())
        if href.startswith("http") and (FOOTER_TEXT.match(text) or FOOTER_HREF.search(href)):
            out.append((0 if CAREERS_HOST.search(urlparse(href).netloc) else 1 if CAREERS_PATH.search(urlparse(href).path)
                        else 2, href))
    return list(dict.fromkeys(h for _, h in sorted(out, key=lambda x: x[0])))


def _get(url: str, timeout: int = 20) -> tuple[str, str, int, bool]:
    """(final url, html, status, blocked). `blocked`: a bot check or an outright refusal answered."""
    try:
        r = httpx.get(url, headers=UA, follow_redirects=True, timeout=timeout)
    except Exception:
        return url, "", 0, False
    blocked = r.status_code in (403, 429, 503) and bool(CHALLENGE.search(r.text[:6000])) or \
        r.status_code == 403
    return str(r.url), r.text, r.status_code, blocked


def _browser_html(url: str) -> str:
    """The page after JavaScript and a scroll to the bottom (lazy footers), in a throwaway headless Chrome."""
    from playwright.sync_api import sync_playwright

    s = get_settings()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel=s.chrome_channel or None, headless=True)
            try:
                page = browser.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                try:
                    page.wait_for_load_state("networkidle", timeout=8_000)
                except Exception:
                    pass
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(1500)
                return page.content()
            finally:
                browser.close()
    except Exception:
        return ""


def guess_greenhouse(company: str, website: str = "", get=None) -> dict | None:
    """A Greenhouse board named like the company, accepted only when the board's own name matches the company
    (a board token alone could be someone else's)."""
    get = get or (lambda u: httpx.get(u, headers=UA, timeout=15, follow_redirects=True))
    flat = re.sub(r"[^a-z0-9]", "", company.lower())
    label = urlparse(website).netloc.lower().split(".")[-2] if website.count(".") else ""
    words = _words(company)
    for token in dict.fromkeys(x for x in [flat, re.sub(r"[^a-z0-9]+", "-", company.lower()).strip("-"), label,
                                           flat + "usa", flat + "inc"] if x):
        try:
            r = get(f"https://boards-api.greenhouse.io/v1/boards/{token}")
        except Exception:
            continue
        if r.status_code != 200:
            continue
        name = re.sub(r"[^a-z0-9]", "", str(r.json().get("name", "")).lower())
        if name and (flat in name or name in flat or (words and all(w in name for w in words))):
            return {"family": "greenhouse", "token": token, "url": f"https://boards.greenhouse.io/{token}"}
    return None


def _cache_path(company: str):
    from jobagent.tools.jobqueue import slug

    return get_settings().state_dir / "companies" / f"{slug(company)}.careers.json"


def cached(company: str) -> dict | None:
    p = _cache_path(company)
    try:
        data = json.loads(p.read_text())
        # Older misses on UKG/KPMG predate their adapters. Refresh those unknown boards once.
        current = data.get('board') or data.get('resolver_version',0) >= 2
        if current and datetime.now(UTC) - datetime.fromisoformat(data["resolved_at"]) < TTL:
            return data
    except Exception:
        pass
    return None


def llm_pick(company: str, hits: list[dict]):
    """Last resort: the fast model chooses the careers page among search results."""
    from langchain_core.messages import HumanMessage
    from pydantic import BaseModel, Field

    from jobagent import llm

    class Choice(BaseModel):
        index: int = Field(description="1-based index of the result that is the company's own careers / jobs page; "
                                       "0 if none is")

    text = "\n".join(f"[{i}] {h.get('title', '')} {h.get('href', '')}" for i, h in enumerate(hits, 1))
    out = llm.structured(Choice, "fast", timeout=60).invoke([HumanMessage(
        f"Which result is the official careers or jobs page of {company} (its own site or its applicant-tracking "
        f"board), not a job aggregator or news page?\n{text}")])
    return hits[out.index - 1] if 0 < out.index <= len(hits) else None


def resolve(company: str, website: str | None = None, log=print, refresh: bool = False, search=None,
            pick_llm=llm_pick) -> dict:
    """{'company', 'url', 'how', 'board', 'blocked', 'resolved_at'}; url is '' when nothing was found."""
    if not refresh and (c := cached(company)):
        return c
    if search is None:
        from jobagent.tools.research import _search as search
    hits = search(f"{company} careers", 8)
    if not hits:  # an empty answer is usually rate limiting: once more after a pause
        time.sleep(8)
        hits = search(f"{company} careers", 8)
    found = pick_from_search(hits, company)
    how = "search"
    if not found:
        site = website
        if not site:
            from jobagent.tools.research import find_website
            site = find_website(company)
        links: list[str] = []
        if site:
            final, markup, status, _ = _get(site)
            links = footer_links(markup, final) if markup else []
            if not links:  # JavaScript-built footer
                links = footer_links(_browser_html(site), site)
        if links:
            found, how = {"url": links[0], "title": "footer link"}, "footer"
            log(f"careers: {company}: no careers result in search; footer link {links[0]}")
    if not found and hits and pick_llm is not None:
        try:
            if pick := pick_llm(company, hits):
                found, how = {"url": pick["href"], "title": pick.get("title", "")}, "llm (flagged: check it)"
                log(f"careers: {company}: chosen by the model, please check: {found['url']}")
        except Exception as e:
            log(f"careers: {company}: model pick failed ({type(e).__name__})")
    result = {"company": company, "url": "", "how": "none", "board": None, "blocked": False, "resolver_version": 2,
              "resolved_at": datetime.now(UTC).isoformat(timespec="seconds")}
    if found:
        final, markup, status, blocked = _get(found["url"])
        board = board_of(final) or (scan_for_board(markup, final) if markup else None)
        if not board and not markup:  # a JavaScript shell with no links in the plain response
            board = scan_for_board(_browser_html(found["url"]), found["url"])
        result.update(url=final if status and status < 400 else found["url"], how=how, board=board,
                      blocked=bool(blocked and not board))
        if not result["board"]:
            result["board"] = guess_greenhouse(company, website or found["url"])
            result["blocked"] = bool(blocked and not result["board"])
        if blocked and not result["board"]:
            log(f"careers: {company}: {found['url']} answered with a bot check or refusal; open it yourself")
    if result["url"]:  # a miss is never cached: the next run searches again
        p = _cache_path(company)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(result, indent=2))
    return result
