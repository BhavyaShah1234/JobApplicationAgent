"""Is a posting in the United States? The US-only filter of discovery (company / CSV inputs; a pasted URL is exempt).

Signals, cheapest first (the first that decides wins):
1. schema.org JobPosting `jobLocation.address.addressCountry` / `applicantLocationRequirements`;
2. the location text: US state names and codes, "United States", "Remote - US", a gazetteer of major US cities,
   against countries, provinces and major cities elsewhere;
3. the title ("Software Engineer (US)", "... - India", "... EMEA");
4. only when those are missing or conflict: the fast model on title + locations + snippet (optional).

Verdicts: `us` (keep), `non_us` (skip with the reason), `ambiguous` (needs_review: "Remote" with no country, a posting
that lists the US and other countries, nothing decides).
"""

import re
from dataclasses import dataclass

US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
    "connecticut": "CT", "delaware": "DE", "district of columbia": "DC", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY",
    "louisiana": "LA", "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV", "new hampshire": "NH",
    "new jersey": "NJ", "new mexico": "NM", "new york": "NY", "north carolina": "NC", "north dakota": "ND",
    "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI",
    "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT",
    "virginia": "VA", "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}
US_CODES = set(US_STATES.values())
US_NAMES = {"united states", "united states of america", "usa", "u.s.", "u.s.a.", "us", "america"}

US_CITIES = {
    "new york", "new york city", "nyc", "brooklyn", "los angeles", "chicago", "houston", "phoenix", "philadelphia",
    "san antonio", "san diego", "dallas", "san jose", "austin", "jacksonville", "fort worth", "columbus",
    "charlotte", "san francisco", "indianapolis", "seattle", "denver", "washington", "boston", "el paso",
    "nashville", "detroit", "oklahoma city", "portland", "las vegas", "memphis", "louisville", "baltimore",
    "milwaukee", "albuquerque", "tucson", "fresno", "sacramento", "mesa", "kansas city", "atlanta", "omaha",
    "colorado springs", "raleigh", "miami", "long beach", "virginia beach", "oakland", "minneapolis", "tulsa",
    "tampa", "arlington", "new orleans", "wichita", "cleveland", "bakersfield", "aurora", "anaheim", "honolulu",
    "santa ana", "riverside", "corpus christi", "lexington", "stockton", "henderson", "saint paul", "st. louis",
    "st louis", "cincinnati", "pittsburgh", "greensboro", "anchorage", "plano", "lincoln", "orlando", "irvine",
    "newark", "durham", "chula vista", "toledo", "fort wayne", "st. petersburg", "laredo", "jersey city",
    "chandler", "madison", "lubbock", "scottsdale", "reno", "buffalo", "gilbert", "glendale", "winston-salem",
    "chesapeake", "norfolk", "fremont", "garland", "irving", "hialeah", "richmond", "boise", "spokane",
    "baton rouge", "tacoma", "san bernardino", "modesto", "fontana", "des moines", "moreno valley", "santa clara",
    "fairfax", "mountain view", "palo alto", "sunnyvale", "menlo park", "redmond", "bellevue", "cupertino",
    "santa monica", "cambridge", "tempe", "salt lake city", "provo", "boulder", "ann arbor", "bothell",
    "burlington", "hoboken", "stamford", "reston", "mclean", "herndon", "folsom", "hillsboro", "golden valley",
    "round rock", "frisco", "sandy springs", "alpharetta", "king of prussia", "bentonville", "lehi",
}

# Countries (and the regional words companies use) other than the United States.
FOREIGN_COUNTRIES = {
    "india", "united kingdom", "uk", "u.k.", "great britain", "england", "scotland", "wales", "ireland", "canada",
    "mexico", "brazil", "brasil", "argentina", "chile", "colombia", "peru", "germany", "france", "spain", "italy",
    "portugal", "netherlands", "the netherlands", "belgium", "switzerland", "austria", "sweden", "norway",
    "denmark", "finland", "poland", "czech republic", "czechia", "romania", "hungary", "bulgaria", "greece",
    "turkey", "turkiye", "israel", "united arab emirates", "uae", "saudi arabia", "egypt", "south africa", "nigeria",
    "kenya", "china", "japan", "south korea", "korea", "singapore", "malaysia", "indonesia", "thailand", "vietnam",
    "philippines", "taiwan", "hong kong", "australia", "new zealand", "pakistan", "bangladesh", "sri lanka",
    "ukraine", "serbia", "croatia", "lithuania", "latvia", "estonia", "costa rica", "panama", "puerto rico",
}
FOREIGN_REGIONS = re.compile(r"(?i)\b(emea|apac|latam|europe|asia|middle east|africa|dach|nordics?|anz|mena)\b")
FOREIGN_CITIES = {
    "mumbai", "powai", "pune", "bengaluru", "bangalore", "hyderabad", "chennai", "delhi", "new delhi", "gurgaon",
    "gurugram", "noida", "kolkata", "ahmedabad", "london", "manchester", "edinburgh", "dublin", "paris", "berlin",
    "munich", "hamburg", "frankfurt", "amsterdam", "rotterdam", "madrid", "barcelona", "lisbon", "rome", "milan",
    "zurich", "geneva", "vienna", "stockholm", "oslo", "copenhagen", "helsinki", "warsaw", "krakow", "prague",
    "budapest", "bucharest", "sofia", "athens", "istanbul", "tel aviv", "dubai", "abu dhabi", "riyadh", "cairo",
    "cape town", "johannesburg", "lagos", "nairobi", "beijing", "shanghai", "shenzhen", "tokyo", "osaka", "seoul",
    "singapore", "kuala lumpur", "jakarta", "bangkok", "ho chi minh city", "hanoi", "manila", "taipei",
    "sydney", "melbourne", "brisbane", "perth", "auckland", "toronto", "vancouver", "montreal", "ottawa",
    "calgary", "edmonton", "waterloo", "mexico city", "guadalajara", "monterrey", "sao paulo", "são paulo",
    "rio de janeiro", "buenos aires", "santiago", "bogota", "bogotá", "lima", "cluj-napoca", "krakow",
}
CANADIAN_PROVINCES = {"on", "bc", "ab", "qc", "mb", "sk", "ns", "nb", "nl", "pe", "ontario", "british columbia",
                      "alberta", "quebec", "québec", "manitoba", "saskatchewan", "nova scotia", "new brunswick"}

REMOTE = re.compile(r"(?i)\b(remote|work from home|wfh|anywhere|telecommut\w*|virtual)\b")
TITLE_US = re.compile(r"(?i)[\(\[–—\-,|]\s*(us|usa|u\.s\.a?\.?|united states)\s*[\)\]]?\s*$|\((us|usa)\)")
SEPARATORS = re.compile(r"\s*[,;|/•·]\s*|\s+[-–—]\s+|\s+or\s+")


@dataclass
class Decision:
    verdict: str          # us | non_us | ambiguous
    how: str              # which signal decided
    reason: str = ""


def _country_of(name: str) -> str:
    """'us' / 'foreign' / '' for a country string or ISO code (JSON-LD addressCountry)."""
    n = name.strip().lower().strip(".")
    if n in {"us", "usa", "u.s", "u.s.a", "united states", "united states of america", "america"}:
        return "us"
    if n in FOREIGN_COUNTRIES or len(n) == 2:
        return "foreign"
    return ""


def from_jsonld(posting: dict | None) -> Decision | None:
    """schema.org JobPosting: jobLocation[].address.addressCountry and applicantLocationRequirements[].name."""
    if not posting:
        return None
    countries: list[str] = []

    def collect(node):
        if isinstance(node, list):
            for x in node:
                collect(x)
        elif isinstance(node, dict):
            c = node.get("addressCountry")
            c = c.get("name") if isinstance(c, dict) else c
            if isinstance(c, str):
                countries.append(c)
            collect(node.get("address"))
            if node.get("@type") == "Country" and isinstance(node.get("name"), str):
                countries.append(node["name"])
    collect(posting.get("jobLocation"))
    collect(posting.get("applicantLocationRequirements"))
    kinds = {_country_of(c) for c in countries} - {""}
    if kinds == {"us"}:
        return Decision("us", "json-ld", f"addressCountry {', '.join(countries)}")
    if kinds == {"foreign"}:
        return Decision("non_us", "json-ld", f"addressCountry {', '.join(countries)}")
    if kinds == {"us", "foreign"}:
        return Decision("ambiguous", "json-ld", f"countries {', '.join(countries)} include the US and others")
    return None


def _words(names) -> re.Pattern:
    return re.compile(r"(?i)(?<![\w])(" + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)) + r")(?![\w])")


STATE_NAME = _words(US_STATES)
US_CITY = _words(US_CITIES)
FOREIGN = _words(FOREIGN_COUNTRIES | FOREIGN_CITIES)
PROVINCE_NAME = _words({p for p in CANADIAN_PROVINCES if len(p) > 2})
US_WORDS = re.compile(r"(?<![\w])(US|USA|U\.S\.A?\.?|United States( of America)?)(?![\w])")
SPLIT = re.compile(r"\s*[,;|/•·()\[\]]\s*|\s+[-–—]\s+")


def from_text(text: str) -> Decision | None:
    """One location string: 'Austin, TX', 'Santa Clara, California', 'Remote - US', 'Powai, Mumbai, India'."""
    toks = [t.strip().strip(".") for t in SPLIT.split(text or "") if t.strip()]
    if not toks:
        return None
    us_strong = bool(STATE_NAME.search(text) or US_WORDS.search(text)
                     or (len(toks) == 1 and US_CITY.fullmatch(toks[0])))
    us_code = any(len(t) == 2 and t.upper() in US_CODES and t.isupper() and i > 0 for i, t in enumerate(toks))
    province = bool(PROVINCE_NAME.search(text)) or any(
        t.lower() in CANADIAN_PROVINCES and len(t) == 2 and t.isupper() and i > 0 for i, t in enumerate(toks))
    rest = STATE_NAME.sub(" ", text)  # "New Mexico" is not Mexico
    named = FOREIGN.search(rest)
    region = FOREIGN_REGIONS.search(text)
    foreign = bool(named or province or region)
    why = (f"{named.group(1).title()} is outside the United States" if named else
           "a Canadian province" if province else f"region word {region.group(1)!r}" if region else "")
    if us_strong and foreign:
        return Decision("ambiguous", "location text", f"{text!r} names the US and {why}")
    if foreign:  # "Hyderabad, IN" is India, not Indiana
        return Decision("non_us", "location text", why)
    if us_strong or us_code:
        return Decision("us", "location text", f"{text!r} is in the United States")
    return None


def from_title(title: str) -> Decision | None:
    if TITLE_US.search(title or ""):
        return Decision("us", "title", "the title says US")
    m = re.search(r"(?i)[\(\[–—\-,|]\s*(india|uk|u\.k\.|canada|emea|apac|latam|europe|germany|france|singapore|"
                  r"australia|japan|ireland|mexico|brazil|israel|china)\s*[\)\]]?\s*$", title or "")
    if m:
        return Decision("non_us", "title", f"the title says {m.group(1)}")
    return None


def classify(locations: list[str], title: str = "", posting: dict | None = None, snippet: str = "",
             judge=None) -> Decision:
    """The posting's country. `judge(title, locations, snippet) -> Decision | None` is the optional model fallback."""
    if d := from_jsonld(posting):
        return d
    per = [from_text(x) for x in locations if x and x.strip()]
    decided = [d for d in per if d]
    verdicts = {d.verdict for d in decided}
    if verdicts == {"us"}:
        return decided[0]
    if verdicts == {"non_us"}:
        return decided[0]
    if verdicts:  # a mix of US and non-US locations, or one that itself names both
        return Decision("ambiguous", "location text", "; ".join(d.reason for d in decided)[:240])
    if d := from_title(title):
        return d
    remote_only = [x for x in locations if REMOTE.search(x or "")]
    if judge is not None:
        try:
            if d := judge(title, locations, snippet):
                return d
        except Exception:
            pass
    if remote_only or not locations:
        return Decision("ambiguous", "none", "remote or no location with no country")
    return Decision("ambiguous", "none", f"couldn't place {locations[:3]}")
