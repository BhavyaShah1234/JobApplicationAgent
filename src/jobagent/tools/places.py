"""Job locations as places: a posting's "Corporate Headquarters", "HQ", "Main Campus" or "Home Office" resolved to
the city, state and country it means, from the company's research profile (step 8).

`resolve(jd, company_slug)` adds `locations_resolved` to the job: one entry per location as written, with the city,
state and country, and how it was found (written in the posting / the company's headquarters / a named site / a
cited lookup). A profile that doesn't know its headquarters yet gets it from a web search, kept in the profile with
its sources. A location nothing resolves is kept as written and marked unresolved.
"""

import json
import re
from pathlib import Path

from pydantic import BaseModel, Field

from jobagent.config import get_settings
from jobagent.tools.forms import US_STATES

JARGON = re.compile(r"(?i)\b(corporate\s+)?(head\s?quarters|hq|head\s+office|home\s+office|main\s+(campus|office)|"
                    r"corporate\s+(office|campus|center|centre)|global\s+(headquarters|hq)|world\s?headquarters)\b")
US = re.compile(r"(?i)\b(united states( of america)?|usa|u\.s\.a?\.?|us)\b")
CODES = {v.upper(): k.title() for k, v in US_STATES.items()}
RESOLVER_VERSION = 3
COUNTRY_PREFIX = re.compile(r"(?i)^(?:united states(?: of america)?|usa|u\.s\.a\.?|u\.s\.?|us)(?:\s+|$)")


def written_place(text: str) -> dict | None:
    """A location that already names a US place: 'Austin, TX', 'Minneapolis, Minnesota', 'Remote - US'."""
    parts = [p.strip() for p in re.split(r"[,|/;-]", text) if p.strip()]
    def state_of(p: str) -> str:
        """Resolve an explicitly written US state name or abbreviation."""
        return CODES.get(p.upper()) or (p.title() if p.lower() in US_STATES else "")

    for i, p in enumerate(parts):
        state = state_of(p)
        if state:
            # "New York, NY": a state name followed by its own code or name is a CITY that shares the state's name.
            if i + 1 < len(parts) and state_of(parts[i + 1]) == state and not (i > 0 and state_of(parts[i - 1])):
                city = COUNTRY_PREFIX.sub("", p).strip()
                return {"city": city, "state": state, "country": "United States", "how": "written in the posting"}
            city = parts[i - 1] if i > 0 and not JARGON.search(parts[i - 1]) else ""
            # Workday commonly writes "USA WILMINGTON, MA - HQ". The
            # country tag is not part of the city, nor is a country-only
            # preceding component evidence of a city.
            city = COUNTRY_PREFIX.sub("", city).strip()
            return {"city": city, "state": state, "country": "United States", "how": "written in the posting"}
    return None


class Lookup(BaseModel):
    city: str = Field(default="", description="'' when the sources don't say")
    state: str = ""
    country: str = ""
    sources: list[int] = Field(default_factory=list)


def lookup_headquarters(company: str, log=print) -> dict | None:
    """The company's headquarters from a web search, only as a source states it (sources kept)."""
    from langchain_core.messages import HumanMessage

    from jobagent import llm
    from jobagent.tools.research import _search
    hits = _search(f"{company} corporate headquarters address", 6)
    if not hits:
        return None
    text = "\n".join(f"[{i}] {h.get('title', '')}: {h.get('body', '')} ({h.get('href', '')})"
                     for i, h in enumerate(hits, 1))
    try:
        out = llm.structured_invoke(llm.writer(800, timeout=120), Lookup, [HumanMessage(
            f"Where is the headquarters of {company}? Answer only from these search results, with the ids of the "
            f"results that state it; city '' if none does.\n\n{text}")], first="function_calling", log=log)
    except Exception as e:
        log(f"places: headquarters lookup failed ({type(e).__name__})")
        return None
    if not out.city or not out.sources:
        return None
    return {"city": out.city, "state": out.state, "country": out.country,
            "sources": [hits[i - 1].get("href", "") for i in out.sources if 0 < i <= len(hits)]}


def _profile(company_slug: str) -> tuple[Path, dict]:
    p = get_settings().state_dir / "companies" / f"{company_slug}.json"
    return p, (json.loads(p.read_text()) if p.exists() else {})


def resolve(jd: dict, company_slug: str, log=print) -> list[dict]:
    """Each of the job's locations as a place (see the module doc); also stored in jd['locations_resolved']."""
    path, profile = _profile(company_slug)
    company = jd.get("company") or profile.get("name") or company_slug
    out = []
    for text in jd.get("locations") or []:
        place = written_place(text)
        if place is None and JARGON.search(text):
            named = next((s for s in profile.get("sites") or [] if s.get("name") and s["name"].lower() in text.lower()),
                         None)
            hq = profile.get("headquarters")
            if named:
                place = {**{k: named.get(k, "") for k in ("city", "state", "country")}, "how": f"the company's {named['name']}"}
            elif hq:
                place = {**{k: hq.get(k, "") for k in ("city", "state", "country")}, "how": "the company's headquarters"}
            else:
                found = lookup_headquarters(company, log)
                if found:
                    if path.exists():  # kept in the profile for the next job at this company
                        profile["headquarters"] = {"name": "", "city": found["city"], "state": found["state"],
                                                   "country": found["country"], "sources": [], "urls": found["sources"]}
                        path.write_text(json.dumps(profile, indent=2))
                    place = {"city": found["city"], "state": found["state"], "country": found["country"],
                             "how": "the company's headquarters (web: " + ", ".join(found["sources"][:2]) + ")"}
        if place is None and US.search(text) and not JARGON.search(text):
            place = {"city": "", "state": "", "country": "United States", "how": "written in the posting"}
        out.append({"as_written": text, **(place or {"city": "", "state": "", "country": "", "how": "unresolved"})})
        log(f"places: {text!r} -> " + (", ".join(x for x in (out[-1]['city'], out[-1]['state'], out[-1]['country'])
                                                  if x) or "unresolved") + f" ({out[-1]['how']})")
    jd["locations_resolved"] = out
    jd["locations_resolver_version"] = RESOLVER_VERSION      # a later parser fix re-resolves jobs resolved by an older one
    return out


def resolve_job(job_dir: Path, company_slug: str, log=print) -> list[dict]:
    """Resolve the job folder's locations and write them back to jd.json and jd.md."""
    p = job_dir / "jd.json"
    jd = json.loads(p.read_text())
    places = resolve(jd, company_slug, log)
    p.write_text(json.dumps(jd, indent=2))
    md = job_dir / "jd.md"
    if md.exists() and places:
        line = "- Locations resolved: " + "; ".join(
            f"{x['as_written']} -> {', '.join(y for y in (x['city'], x['state'], x['country']) if y) or 'unresolved'}"
            for x in places)
        text = md.read_text()
        text = re.sub(r"(?m)^- Locations resolved:.*\n", "", text)
        text = re.sub(r"(?m)^(- Locations:.*\n)", lambda m: m.group(1) + line + "\n", text, count=1)
        md.write_text(text)
    return places
