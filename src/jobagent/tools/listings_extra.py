"""Read-only adapters for the public UKG board and KPMG's public search endpoint.

Request shapes are those used by the sites' own search pages. Neither signs in or submits an application.
"""
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

from lxml import html as lh

from jobagent.tools.jobqueue import Listing


def parse_ukg(data: dict, company: str, base: str) -> list[Listing]:
    out = []
    for item in (data or {}).get("opportunities", []):
        if not item.get("Id") or not item.get("Title"):
            continue
        locations = []
        for place in item.get("Locations") or []:
            address = place.get("Address") or {}
            state, country = address.get("State") or {}, address.get("Country") or {}
            text = ", ".join(x for x in [address.get("City"), state.get("Code") or state.get("Name"),
                                          country.get("Name") or country.get("Code")] if x)
            locations.append(text or place.get("LocalizedDescription") or place.get("LocalizedName") or "")
        out.append(Listing(company, item["Title"], base.rstrip('/') + '/OpportunityDetail?' + urlencode({"opportunityId":item["Id"]}),
                           [x for x in locations if x], (item.get("PostedDate") or "")[:10],
                           item.get("RequisitionNumber") or item["Id"], item.get("BriefDescription", "")[:600], "ultipro"))
    return out


def collect_ukg(board: dict, company: str, get_json, pause, log) -> list[Listing]:
    base = board['url'].rstrip('/')
    found = {}
    for offset in range(0, 2000, 50):
        # Empty filters mean the same unfiltered public search the board makes on first load.
        body = {"opportunitySearch":{"Top":50,"Skip":offset,"QueryString":"","Filters":[
                    {"t":"TermsSearchFilterDto","fieldName":n,"extra":None,"values":[]} for n in [4,5,6,37]]},
                "matchCriteria":{"PreferredJobs":[],"Educations":[],"LicenseAndCertifications":[],"Skills":[],
                                 "hasNoLicenses":False,"SkippedSkills":[]}}
        data = get_json(base+'/JobBoardView/LoadSearchResults', 'POST', body) or {}
        rows = parse_ukg(data, company, base)
        before = len(found)
        found.update({row.url:row for row in rows})
        if not rows or len(found)==before or len(rows)<50 or offset+50 >= data.get('totalCount',float('inf')):
            break
        pause()
    else:
        log(f'listings: {company}: public board collection capped at 2,000 postings')
    return list(found.values())


def parse_kpmg(markup: str, base: str, company: str) -> list[Listing]:
    if not markup.strip():
        return []
    doc = lh.fromstring(markup)
    out = {}
    for link in doc.xpath('//a[contains(@href,"/jobdetail")]'):
        # The same card includes both grid and list versions; the latter contains the actual office list.
        titles = link.xpath('.//*[contains(concat(" ",normalize-space(@class)," ")," h4 ") or '
                            'contains(concat(" ",normalize-space(@class)," ")," h5 ")]')
        if not titles:
            continue
        title = ' '.join(titles[0].text_content().split())
        locations = []
        for info in link.xpath('.//*[contains(concat(" ",normalize-space(@class)," ")," list-view ")]'
                               '//*[contains(concat(" ",normalize-space(@class)," ")," text-xs ")]'):
            text = info.text_content().split('|',1)[-1].strip()
            locations.extend(x.strip() for x in text.split(';') if x.strip())
        url = urljoin(base, link.get('href'))
        req = parse_qs(urlparse(url).query).get('jobId',[''])[0]
        if title and req:
            out[url] = Listing(company,title,url,locations,req_id=req,portal='kpmg')
    return list(out.values())


def collect_kpmg(board: dict, company: str, queries, get_html, get_json, pause, log) -> list[Listing]:
    base = board['url']
    markup = get_html(base)
    doc = lh.fromstring(markup) if markup.strip() else None
    templates = doc.xpath('//input[@id="template-url"]/@value') if doc is not None else []
    if not templates:
        return parse_kpmg(markup,base,company)
    template = urlparse(urljoin(base,templates[0]))
    origin = urlparse(base)
    if (template.scheme,template.netloc)!=(origin.scheme,origin.netloc):
        raise ValueError('KPMG search endpoint must belong to its careers page')
    endpoint = template.geturl().rstrip('/')+'/page-templates/google/get-jobs.php'
    found = {}
    for query in queries or ['']:
        seen_query=set()
        for page in range(1,21):
            data=get_json(endpoint+'?'+urlencode({'ajax':'1','keyword':query,'spage':page})) or {}
            rows=parse_kpmg((data.get('postings') or {}).get('jobs',''),base,company)
            before=len(seen_query)
            seen_query.update(row.url for row in rows)
            found.update({row.url:row for row in rows})
            pagination=data.get('pagination') or ''
            if not rows or (page>1 and len(seen_query)==before) or not pagination:
                break
            nav=lh.fromstring(pagination)
            if not nav.xpath('//*[contains(concat(" ",normalize-space(@class)," ")," pagination-next ")]/a[@data-href]'):
                break
            pause()
        else:
            log(f'listings: {company}: capped public search for {query!r} at 20 pages')
    return list(found.values())
