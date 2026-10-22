"""Company research with sources: what the company does, mission, products, values, recent news.

Sources (no browser): web search for the official site, the site's own home / about / mission / values pages,
Wikipedia's summary API, and news search. The fast model writes the profile from those sources only; every claim
carries the ids of the sources it came from, and claims citing nothing (or a source that wasn't fetched) are
dropped. Profiles are cached in state/companies/<slug>.json/.md for CACHE_DAYS.
"""

import json
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

import httpx
from pydantic import BaseModel, Field

from jobagent import db
from jobagent.config import get_settings

CACHE_DAYS = 7
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0 Safari/537.36"}
NOT_OFFICIAL = re.compile(
    r"(?i)(wikipedia|linkedin|glassdoor|indeed|ziprecruiter|crunchbase|bloomberg|facebook|instagram|twitter|x\.com|"
    r"youtube|reddit|yelp|zoominfo|dnb\.com|builtin|levels\.fyi|comparably|simplyhired|monster|careerbuilder|"
    r"wellfound|angel\.co|craft\.co|owler|forbes|reuters|yahoo|msn|cnbc|wsj|nytimes|bbb\.org|mapquest|"
    r"myworkdayjobs|greenhouse|lever\.co|ashbyhq|icims|ultipro|taleo|smartrecruiters)\."
)
ABOUT_LINK = re.compile(r"(?i)about|mission|values|who[- ]we[- ]are|our[- ]story|purpose|company|culture")
MAX_SOURCE_CHARS = 3500


class Claim(BaseModel):
    text: str = Field(description="One factual statement, in plain words")
    sources: list[int] = Field(description="Ids of the sources that state it")


class NewsItem(BaseModel):
    title: str
    date: str | None = None
    summary: str = Field(description="One sentence")
    sources: list[int]


class ProfileDraft(BaseModel):
    """What the model fills in (claims with source ids only)."""

    name: str = Field(description="The company's brand name")
    what_they_do: list[Claim] = Field(default_factory=list, description="What the company does, 1-3 claims")
    mission: list[Claim] = Field(default_factory=list, description="Mission / purpose statements, as they state them")
    products: list[Claim] = Field(default_factory=list, description="Main products, services, brands")
    values: list[Claim] = Field(default_factory=list, description="Stated values / culture")
    facts: list[Claim] = Field(default_factory=list, description="Size, HQ, founding year, industry, notable facts")
    recent_news: list[NewsItem] = Field(default_factory=list, description="Up to 5 recent, notable news items")


class CompanyProfile(ProfileDraft):
    """Stored profile: the draft plus what the code fills in."""

    website: str | None = None
    sources: dict[int, dict] = Field(default_factory=dict)
    researched_at: str = ""


def slug(name: str) -> str:
    base = re.sub(r"(?i)[,.]?\s*\b(inc|llc|ltd|corp|corporation|co|company|plc|llp|lp)\b\.?", "", name)
    return re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-") or "unknown"


def _search(query: str, n: int = 8) -> list[dict]:
    from ddgs import DDGS

    for attempt in range(3):
        try:
            with DDGS() as d:
                return list(d.text(query, max_results=n))
        except Exception:
            time.sleep(2 * (attempt + 1))
    return []


def _news(query: str, n: int = 8) -> list[dict]:
    from ddgs import DDGS

    for attempt in range(3):
        try:
            with DDGS() as d:
                return list(d.news(query, max_results=n, timelimit="y"))
        except Exception:
            time.sleep(2 * (attempt + 1))
    return []


def _fetch(url: str) -> tuple[str, str]:
    """(final url, readable text) of a page; '' if it can't be fetched."""
    import trafilatura

    try:
        r = httpx.get(url, headers=UA, follow_redirects=True, timeout=20)
        if r.status_code >= 400:
            return url, ""
        return str(r.url), (trafilatura.extract(r.text, include_links=False) or "")
    except Exception:
        return url, ""


def _links(url: str) -> list[str]:
    try:
        r = httpx.get(url, headers=UA, follow_redirects=True, timeout=20)
    except Exception:
        return []
    hrefs = re.findall(r'href="([^"#]+)"', r.text)
    host = urlparse(str(r.url)).netloc
    out = []
    for h in hrefs:
        full = urljoin(str(r.url), h)
        p = urlparse(full)
        if p.netloc == host and ABOUT_LINK.search(p.path) and not re.search(r"(?i)\.(pdf|jpg|png)$|/(careers?|jobs?)/", p.path):
            out.append(full.split("?")[0].rstrip("/"))
    return list(dict.fromkeys(out))


def find_website(company: str, hint_text: str = "") -> str | None:
    """The company's official site: first search hit on a non-aggregator domain that shares a word with the name."""
    words = {w for w in re.findall(r"[a-z]{3,}", company.lower()) if w not in {"inc", "llc", "corp", "the", "company"}}
    for hit in _search(f"{company} official site") + _search(company):
        url = hit.get("href", "")
        host = urlparse(url).netloc.lower()
        if not host or NOT_OFFICIAL.search(host):
            continue
        dom = db.registrable_domain(host)
        name_part = dom.split(".")[0]
        if any(w in name_part or name_part in w for w in words) or (hint_text and name_part in hint_text.lower()):
            return f"https://{host}"
    return None


def _wikipedia(company: str) -> tuple[str, str] | None:
    try:
        hits = httpx.get("https://en.wikipedia.org/w/api.php", headers=UA, timeout=20, params={
            "action": "opensearch", "search": company, "limit": 1, "namespace": 0, "format": "json"}).json()
        if not hits[1]:
            return None
        title = hits[1][0]
        s = httpx.get(f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote(title.replace(' ', '_'))}",
                      headers=UA, timeout=20).json()
        if s.get("type") == "disambiguation":
            return None
        return s.get("content_urls", {}).get("desktop", {}).get("page", ""), s.get("extract", "")
    except Exception:
        return None


def gather_sources(company: str, hint_text: str = "") -> tuple[str | None, list[dict]]:
    sources: list[dict] = []
    website = find_website(company, hint_text)
    if website:
        url, text = _fetch(website)
        if text:
            sources.append({"kind": "official", "url": url, "text": text})
        for link in _links(website)[:4]:
            url, text = _fetch(link)
            if text and all(s["url"] != url for s in sources):
                sources.append({"kind": "official", "url": url, "text": text})
    wiki = _wikipedia(company)
    if wiki and wiki[1] and company.split()[0].lower() in wiki[1].lower():
        sources.append({"kind": "wikipedia", "url": wiki[0], "text": wiki[1]})
    cutoff = datetime.now(UTC) - timedelta(days=365)
    for n in _news(company):
        date = n.get("date", "")
        try:
            if date and datetime.fromisoformat(date.replace("Z", "+00:00")) < cutoff:
                continue
        except ValueError:
            pass
        if company.split()[0].lower() not in f"{n.get('title', '')} {n.get('body', '')}".lower():
            continue
        sources.append({"kind": "news", "url": n.get("url", ""), "date": date[:10],
                        "text": f"{n.get('title', '')} ({n.get('source', '')}, {date[:10]}): {n.get('body', '')}"})
    return website, sources[:14]


RESEARCH_PROMPT = """Write a company profile using ONLY the numbered sources below.
- Every claim and news item must list the ids of the sources that state it. No source → leave it out.
- Keep the company's own wording for mission/values where possible. No speculation, no marketing fluff.
- recent_news: up to 5 notable items (products, results, leadership, layoffs, acquisitions), newest first."""


def _valid(claims: list, ids: set[int]) -> list:
    out = []
    for c in claims:
        c.sources = [i for i in c.sources if i in ids]
        if c.sources:
            out.append(c)
    return out


def research(company: str, hint_text: str = "", refresh: bool = False, log=print) -> CompanyProfile:
    from langchain_core.messages import HumanMessage, SystemMessage

    from jobagent import llm

    s = get_settings()
    root = s.state_dir / "companies"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{slug(company)}.json"
    if path.exists() and not refresh:
        cached = CompanyProfile.model_validate_json(path.read_text())
        if datetime.fromisoformat(cached.researched_at) > datetime.now(UTC) - timedelta(days=CACHE_DAYS):
            return cached
    website, sources = gather_sources(company, hint_text)
    if not sources:
        raise RuntimeError(f"no sources found for {company!r}")
    numbered = {i + 1: src for i, src in enumerate(sources)}
    text = "\n\n".join(f"[{i}] ({src['kind']}) {src['url']}\n{src['text'][:MAX_SOURCE_CHARS]}" for i, src in numbered.items())
    draft = llm.structured(ProfileDraft, "fast").invoke(
        [SystemMessage(RESEARCH_PROMPT), HumanMessage(f"Company: {company}\n\nSources:\n{text}")])
    profile = CompanyProfile(**draft.model_dump())
    ids = set(numbered)
    for field_name in ("what_they_do", "mission", "products", "values", "facts", "recent_news"):
        setattr(profile, field_name, _valid(getattr(profile, field_name), ids))
    cited = {i for f in ("what_they_do", "mission", "products", "values", "facts", "recent_news")
             for c in getattr(profile, f) for i in c.sources}
    profile.website = website
    profile.sources = {i: {k: v for k, v in numbered[i].items() if k != "text"} for i in sorted(cited)}
    profile.researched_at = datetime.now(UTC).isoformat(timespec="seconds")
    path.write_text(profile.model_dump_json(indent=2))
    path.with_suffix(".md").write_text(render_md(profile))
    log(f"research: {profile.name}: {len(profile.sources)} sources cited → {path.with_suffix('.md')}")
    return profile


def render_md(p: CompanyProfile) -> str:
    def cite(ids):
        return " " + "".join(f"[{i}]" for i in ids)

    lines = [f"# {p.name}", "", f"- Website: {p.website or '—'}", f"- Researched: {p.researched_at}", ""]
    for title, items in (("What they do", p.what_they_do), ("Mission", p.mission), ("Products / services", p.products),
                         ("Values / culture", p.values), ("Facts", p.facts)):
        if items:
            lines += [f"## {title}", ""] + [f"- {c.text}{cite(c.sources)}" for c in items] + [""]
    if p.recent_news:
        lines += ["## Recent news", ""] + [f"- {n.date or ''} {n.title}: {n.summary}{cite(n.sources)}"
                                           for n in p.recent_news] + [""]
    lines += ["## Sources", ""] + [f"{i}. ({s['kind']}{', ' + s['date'] if s.get('date') else ''}) {s['url']}"
                                    for i, s in sorted(p.sources.items())]
    return "\n".join(lines) + "\n"


def research_many(companies: list[str], workers: int = 5, refresh: bool = False, log=print) -> list[tuple[str, CompanyProfile | None, str | None, float]]:
    from concurrent.futures import ThreadPoolExecutor

    def one(name):
        t0 = time.monotonic()
        try:
            return name, research(name, refresh=refresh, log=log), None, time.monotonic() - t0
        except Exception as e:
            return name, None, f"{type(e).__name__}: {str(e)[:150]}", time.monotonic() - t0

    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(companies)))) as pool:
        return list(pool.map(one, companies))
