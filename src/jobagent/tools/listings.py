"""Read a company's open listings from the careers board that `careers.resolve` found (step 14c).

Per ATS family, the board's public listing endpoint (what the board's own page loads, no login, one small request per
page): Greenhouse job-board API, Lever postings API, Ashby posting API, SmartRecruiters postings API, and Workday's
`/wday/cxs/<tenant>/<site>/jobs` call (searched once per target role, a few pages each). Any other site: the job links
on the careers page itself (best effort; location unknown, so those listings go to review). Listings carry title,
locations, URL, posted date, requisition id and a snippet. A refused Workday catalog client can use the board's normal
public Chrome interface. Browser-side refusals still stop collection; visible security checks require the candidate.
"""

import re
import time
from datetime import UTC, datetime
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
from lxml import html as lh

from jobagent.tools.careers import UA, board_of
from jobagent.tools.jobqueue import Listing, slug

PAUSE_S = 0.4          # between requests to one board
WORKDAY_PAGES = 5      # x 20 results per search
JOB_HREF = re.compile(r"(?i)/(jobs?|positions?|openings?|requisitions?|vacanc\w+)/[^/?#]+|/careers?/[^/?#]+/[^/?#]+|"
                      r"[?&](job_?id|jid|gh_jid|req(uisition)?_?id|id)=\w+|/jobdetail|/job-details?|/[^/?#]+-\d{4,}/?$")
TITLE_PLACE = re.compile(r"[–—-]\s*([A-Z][\w .'’-]+,\s*[A-Z]{2})\s*$")  # "Sanitation Manager Job – Marion, IN"
WORKDAY_FACETS = frozenset(('locations', 'locationCountry', 'jobFamilyGroup', 'jobFamily', 'timeType', 'workerSubType', 'remoteType'))


def workday_searches(url: str, queries=None):
    """Keep an explicitly filtered careers search intact instead of widening it."""
    params = parse_qs(urlparse(url).query, keep_blank_values=True)
    facets = {key: list(dict.fromkeys(values)) for key, values in params.items() if key in WORKDAY_FACETS}
    searches = [params.get('q', [''])[0]] if facets or 'q' in params else list(queries or [''])
    return searches, facets


class Blocked(RuntimeError):
    """The board refused (a bot check or a 403): reported to the candidate, never worked around."""


def _json(url: str, method: str = "GET", body: dict | None = None, timeout: int = 25):
    r = httpx.request(method, url, headers={**UA, "Accept": "application/json"}, json=body, timeout=timeout,
                      follow_redirects=True)
    if r.status_code in (401, 403, 429) or (r.status_code == 503 and "cf-chl" in r.text[:3000].lower()):
        raise Blocked(f"{urlparse(url).netloc} answered {r.status_code}")
    if r.status_code >= 400:
        return None
    try:
        return r.json()
    except ValueError:
        return None


def _iso(ms_or_text) -> str:
    if isinstance(ms_or_text, (int, float)) and ms_or_text > 10_000_000_000:
        return datetime.fromtimestamp(ms_or_text / 1000, UTC).date().isoformat()
    return str(ms_or_text or "")[:10]


def _text(markup: str, n: int = 600) -> str:
    try:
        return " ".join(lh.fromstring(f"<div>{markup}</div>").text_content().split())[:n]
    except Exception:
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", markup or ""))[:n]


# --- one parser per board (pure: they take the decoded JSON) ----------------------------------------------------

def parse_greenhouse(data: dict, company: str) -> list[Listing]:
    out = []
    for j in (data or {}).get("jobs", []):
        loc = (j.get("location") or {}).get("name", "")
        out.append(Listing(company, j.get("title", ""), j.get("absolute_url", ""), [loc] if loc else [],
                           _iso(j.get("first_published") or j.get("updated_at")),
                           # `id` is unique; `requisition_id` is often a placeholder ("See Opening ID" on every Stripe job)
                           str(j.get("id") or ""), "", "greenhouse"))
    return out


def parse_lever(data: list, company: str) -> list[Listing]:
    out = []
    for j in data or []:
        cats = j.get("categories") or {}
        locs = [x for x in [cats.get("location"), *(cats.get("allLocations") or [])] if x]
        out.append(Listing(company, j.get("text", ""), j.get("hostedUrl", ""), list(dict.fromkeys(locs)),
                           _iso(j.get("createdAt")), str(j.get("id", "")), (j.get("descriptionPlain") or "")[:600], "lever"))
    return out


def parse_ashby(data: dict, company: str) -> list[Listing]:
    out = []
    for j in (data or {}).get("jobs", []):
        if j.get("isListed") is False:
            continue
        locs = [j.get("location", "")] + [x.get("location", "") for x in j.get("secondaryLocations") or []]
        out.append(Listing(company, j.get("title", ""), j.get("jobUrl", ""), [x for x in locs if x],
                           _iso(j.get("publishedAt")), str(j.get("id", "")), (j.get("descriptionPlain") or "")[:600], "ashby"))
    return out


def parse_smartrecruiters(data: dict, company: str, token: str) -> list[Listing]:
    out = []
    for j in (data or {}).get("content", []):
        loc = j.get("location") or {}
        place = ", ".join(x for x in [loc.get("city"), loc.get("region"), (loc.get("country") or "").upper()] if x)
        out.append(Listing(company, j.get("name", ""), f"https://jobs.smartrecruiters.com/{token}/{j.get('id', '')}",
                           [place] if place else [], _iso(j.get("releasedDate")), str(j.get("refNumber") or j.get("id", "")),
                           "", "smartrecruiters"))
    return out


def parse_workable(data: dict, company: str, token: str) -> list[Listing]:
    """Workable's public board (`apply.workable.com/<token>`): one listing per published posting, located by its city, region and country."""
    out = []
    for j in (data or {}).get("results", []):
        if j.get("state", "published") != "published" or j.get("isInternal"):
            continue
        loc = j.get("location") or {}
        place = ", ".join(dict.fromkeys(x for x in [loc.get("city"), loc.get("region"), loc.get("country")] if x))
        out.append(Listing(company, j.get("title", ""), f"https://apply.workable.com/{token}/j/{j.get('shortcode', '')}/",
                           [place] if place else [], _iso(j.get("published")), str(j.get("shortcode") or ""),
                           "Remote" if j.get("remote") else "", "workable"))
    return out


def parse_rippling(items: list, company: str) -> list[Listing]:
    """Rippling's public board (`ats.rippling.com/<token>`): the same posting is listed once per location, so locations are merged by id."""
    merged: dict[str, Listing] = {}
    for j in items or []:
        jid = str(j.get("id") or "")
        if not jid:
            continue
        places = [", ".join(dict.fromkeys(x for x in [l.get("city"), l.get("state") or l.get("stateCode"), l.get("country")] if x)) or l.get("name", "")
                  for l in j.get("locations") or []]
        if jid not in merged:
            merged[jid] = Listing(company, j.get("name", ""), j.get("url", ""), [], "", jid, (j.get("department") or {}).get("name", ""), "rippling")
        merged[jid].locations = list(dict.fromkeys([*merged[jid].locations, *(p for p in places if p)]))
    return list(merged.values())


def parse_gem(data: list, company: str) -> list[Listing]:
    """Gem's public board (`jobs.gem.com/<token>`): a list of postings with their location name and plain-text description."""
    out = []
    for j in data or []:
        name = (j.get("location") or {}).get("name", "")
        out.append(Listing(company, j.get("title", ""), j.get("absolute_url", ""), [name] if name else [], _iso(j.get("first_published_at")),
                           str(j.get("requisition_id") or j.get("id") or ""), (j.get("content_plain") or "")[:600], "gem"))
    return out


def parse_workday(data: dict, company: str, host: str, site: str) -> list[Listing]:
    out = []
    for j in (data or {}).get("jobPostings", []):
        path = j.get("externalPath", "")
        bullets = j.get("bulletFields") or []
        # Workday may prepend labels such as "Spotlight Job" to bulletFields.
        # That shared label must never become a requisition identity for deduping.
        leaf = path.rsplit("/", 1)[-1]
        req_id = (leaf.rsplit("_", 1)[-1] if "_" in leaf else
                  next((str(b) for b in bullets if re.fullmatch(r"(?=.*\d)[A-Za-z0-9][A-Za-z0-9.-]*", str(b))), ""))
        out.append(Listing(company, j.get("title", ""), f"https://{host}/en-US/{site}{path}",
                           [j["locationsText"]] if j.get("locationsText") else [], j.get("postedOn", ""),
                           req_id, "", "workday"))
    return out


def workday_locations(item: Listing, get_json=_json) -> list[str]:
    """Expand a public Workday 'N Locations' placeholder using the posting's own detail response."""
    parsed=urlparse(item.url)
    board=board_of(item.url)
    if not board or board['family']!='workday' or not re.fullmatch(r'\d+ locations?', ' '.join(item.locations),re.I):
        return item.locations
    tenant,_,site=board['token'].split('/')
    path=parsed.path[parsed.path.find('/job/'):]
    if not path.startswith('/job/'):
        return item.locations
    try:
        data=get_json(f'https://{parsed.netloc}/wday/cxs/{tenant}/{site}{path}') or {}
    except (Blocked, httpx.HTTPError):
        from jobagent.tools.listings_browser import read_workday_detail
        data=read_workday_detail(item.url)
    info=data.get('jobPostingInfo') or {}
    primary=info.get('location') or ''
    additional=info.get('additionalLocations') or []
    count=int(re.match(r'\d+', ' '.join(item.locations))[0])
    if not primary or 1+len(additional)<count:
        return item.locations  # a country or a partial office list cannot establish every advertised location
    country=info.get('country') or {}
    if isinstance(country,dict):country=country.get('descriptor') or ''
    values=[', '.join(x for x in [primary,country] if x)] + list(additional)
    # Every expanded office must be explicit enough to classify; never let one known US city hide unknown offices.
    from jobagent.tools.locations import from_text
    values=list(dict.fromkeys(x for x in values if isinstance(x,str) and x.strip()))
    return values if values and all(from_text(x) is not None for x in values) else item.locations


def parse_dom(markup: str, base: str, company: str) -> list[Listing]:
    """Job links on a careers page: best effort, location unknown."""
    try:
        doc = lh.fromstring(markup)
    except Exception:
        return []
    out, seen = [], set()
    for a in doc.xpath("//a[@href]"):
        href = urljoin(base, a.get("href"))
        title = " ".join(a.text_content().split())
        if href in seen or not href.startswith("http") or len(title.split()) < 2 or len(title) > 140:
            continue
        if JOB_HREF.search(href) and not re.search(r"(?i)(privacy|cookie|terms|sign.?in|log.?in|search all|view all|"
                                                   r"apply now|learn more)", title):
            seen.add(href)
            place = TITLE_PLACE.search(title)
            out.append(Listing(company, title, href, [place.group(1)] if place else [], "",
                               (re.search(r"-(\d{4,})/?$", urlparse(href).path) or [None, ""])[1], "", "dom"))
    return out


# --- collection ---------------------------------------------------------------------------------------------------

def collect(careers: dict, company: str, queries: list[str] | None = None, log=print, get_json=_json,
            get_html=None) -> list[Listing]:
    """All open listings of the company's board. `queries`: target role titles (Workday searches by them)."""
    board = careers.get("board") or board_of(careers.get("url") or "")
    if not board:
        if not careers.get("url"):
            return []
        markup = (get_html or _html)(careers["url"])
        found = parse_dom(markup, careers["url"], company)
        log(f"listings: {company}: no ATS board; {len(found)} job links on the careers page (location unknown)")
        return found
    fam, token = board["family"], board["token"]
    if fam in ('ultipro','kpmg'):
        from jobagent.tools.listings_extra import collect_ukg, collect_kpmg
        pause = lambda: time.sleep(PAUSE_S)
        if fam == 'ultipro':
            return collect_ukg(board,company,get_json,pause,log)
        return collect_kpmg(board,company,queries,get_html or _html,get_json,pause,log)
    if fam == "greenhouse":
        data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs")
        return parse_greenhouse(data, company)
    if fam == "lever":
        return parse_lever(get_json(f"https://api.lever.co/v0/postings/{token}?mode=json"), company)
    if fam == "ashby":
        return parse_ashby(get_json(f"https://api.ashbyhq.com/posting-api/job-board/{token}"), company)
    if fam == "smartrecruiters":
        return parse_smartrecruiters(get_json(f"https://api.smartrecruiters.com/v1/companies/{token}/postings?limit=100"),
                                     company, token)
    if fam == "workable":
        body, found = {"query": "", "location": [], "department": [], "worktype": [], "remote": []}, []
        for _ in range(10):                                           # follow Workable's page token, at most 10 pages
            data = get_json(f"https://apply.workable.com/api/v3/accounts/{token}/jobs", "POST", body)
            found += parse_workable(data, company, token)
            if not (data or {}).get("nextPage"):
                break
            body = {**body, "token": data["nextPage"]}
            time.sleep(PAUSE_S)
        return found
    if fam == "rippling":
        items = []
        for page in range(10):                                        # 100 postings a page, at most 1000
            data = get_json(f"https://ats.rippling.com/api/v2/board/{token}/jobs?page={page}&pageSize=100") or {}
            items += data.get("items") or []
            if page + 1 >= int(data.get("totalPages") or 1) or not data.get("items"):
                break
            time.sleep(PAUSE_S)
        return parse_rippling(items, company)
    if fam == "gem":
        return parse_gem(get_json(f"https://api.gem.com/job_board/v0/{token}/job_posts/"), company)
    if fam == "workday":
        tenant, wd, site = token.split("/")
        host = f"{tenant}.{wd}.myworkdayjobs.com"
        found, seen = [], set()
        searches, facets = workday_searches(careers.get('url', ''), queries)
        try:
            for q in searches:
                for page in range(WORKDAY_PAGES):
                    data = get_json(f"https://{host}/wday/cxs/{tenant}/{site}/jobs", "POST",
                                    {"appliedFacets": facets, "limit": 20, "offset": page * 20, "searchText": q})
                    rows = parse_workday(data, company, host, site)
                    for r in rows:
                        if r.url not in seen:
                            seen.add(r.url)
                            found.append(r)
                    if len(rows) < 20:
                        break
                    time.sleep(PAUSE_S)
        except Blocked as exc:
            # Use the site's ordinary public UI, without changing identity or
            # repeating a refused API call. A refusal in Chrome still stops.
            from jobagent.tools.listings_browser import collect_workday
            log(f'listings: {company}: catalog client refused ({exc}); opening the public careers page in Chrome')
            return collect_workday(careers, company, queries, log=log)
        return found
    log(f"listings: {company}: {fam} boards are not read yet; open {careers.get('url')} yourself")
    return []


def _html(url: str) -> str:
    try:
        r = httpx.get(url, headers=UA, follow_redirects=True, timeout=25)
        if r.status_code in (401, 403, 429):
            raise Blocked(f"{urlparse(url).netloc} answered {r.status_code}")
        return r.text if r.status_code < 400 else ""
    except Blocked:
        raise
    except Exception:
        return ""


def target_queries(config: dict) -> list[str]:
    """Search words for boards that search server-side: each target role's title."""
    return list(dict.fromkeys(r.get("title", "") for r in config.get("roles", []) if r.get("title")))


def company_slug(company: str) -> str:
    return slug(company)
