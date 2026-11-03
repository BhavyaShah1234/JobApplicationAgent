"""Lookups a form answer may need (an applicant.yaml entry's `tools`, or the planner asking for one): web search, a place's
address, and the distance / driving time between two places.

Maps use OpenStreetMap's Nominatim (geocoding, at most one request a second, with an identifying User-Agent as its
usage policy asks) and the public OSRM router (driving distance and time). No keys needed.
"""

import time

import httpx

UA = {"User-Agent": "jobagent/0.1 (job application assistant; personal use)"}
_last = [0.0]


def web_search(query: str, n: int = 5) -> str:
    from jobagent.tools.research import _search
    hits = _search(query, n)
    return "\n".join(f"- {h.get('title', '')}: {h.get('body', '')[:240]} ({h.get('href', '')})" for h in hits) or "no results"


def geocode(place: str) -> dict | None:
    wait = 1.1 - (time.monotonic() - _last[0])
    if wait > 0:
        time.sleep(wait)
    _last[0] = time.monotonic()
    r = httpx.get("https://nominatim.openstreetmap.org/search", params={"q": place, "format": "jsonv2", "limit": 1,
                  "addressdetails": 1}, headers=UA, timeout=20)
    r.raise_for_status()
    hits = r.json()
    return hits[0] if hits else None


def route(origin: str, destination: str) -> str:
    a, b = geocode(origin), geocode(destination)
    if not a or not b:
        return f"place not found: {origin if not a else destination}"
    r = httpx.get(f"https://router.project-osrm.org/route/v1/driving/{a['lon']},{a['lat']};{b['lon']},{b['lat']}",
                  params={"overview": "false"}, headers=UA, timeout=20)
    r.raise_for_status()
    leg = r.json()["routes"][0]
    return (f"{a['display_name']} -> {b['display_name']}: {leg['distance'] / 1609.34:.1f} miles, "
            f"about {leg['duration'] / 60:.0f} minutes by car")


def run(request: str) -> str:
    """`web: <query>` | `place: <name or address>` | `maps: <origin> | <destination>`."""
    kind, _, arg = request.partition(":")
    kind, arg = kind.strip().lower(), arg.strip()
    try:
        if kind in ("web", "web_search", "search"):
            return web_search(arg)
        if kind == "place":
            g = geocode(arg)
            return g["display_name"] if g else "place not found"
        if kind in ("maps", "route", "distance"):
            origin, _, dest = arg.partition("|")
            return route(origin.strip(), dest.strip())
    except Exception as e:
        return f"lookup failed: {type(e).__name__}: {str(e)[:120]}"
    return f"unknown lookup {kind!r} (use web:, place: or maps: origin | destination)"
