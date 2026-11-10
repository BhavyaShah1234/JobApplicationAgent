"""Discovery (step 14): from a company name to queued roles.

`discover(company)`: careers page and board (`careers.resolve`) -> every open listing (`listings.collect`) ->
US-only check (`locations.classify`; this applies to discovery only, a URL the candidate pastes is always read) ->
role match (`matching.match_listing`: regex, then embeddings, then the fast-model judge) -> `jobqueue.enqueue`.
One company's failure (no careers page, a bot check, a refused board, an error) is reported and never stops the next.
"""

import re
from dataclasses import dataclass, field

import yaml

from jobagent import db
from jobagent.config import get_settings
from jobagent.tools import careers as careers_mod
from jobagent.tools import jobqueue, listings as listings_mod, locations, matching

MAX_SEMANTIC = 400   # non-matching titles embedded per company (the rest are skipped, said in the log)
MAX_JUDGE = 80       # model-judged listings per company (the rest keep the regex / embedding prior)


@dataclass
class Result:
    company: str
    careers_url: str = ""
    how: str = ""
    listings: int = 0
    counts: dict = field(default_factory=dict)
    note: str = ""            # blocked / no careers page / error
    queued: list = field(default_factory=list)   # (title, location, url, role, score)


def load_preferences() -> dict:
    p = get_settings().data_dir / "preferences.yaml"
    return (yaml.safe_load(p.read_text()) if p.exists() else None) or {}


def discover(company: str, config: dict | None = None, conn=None, log=print, refresh: bool = False,
             resolver=careers_mod.resolve, collector=listings_mod.collect, judge="model", embed="model",
             location_judge=None, limit: int | None = None, only_us: bool = True) -> Result:
    res = Result(company)
    config = config if config is not None else matching.load_roles()
    conn = conn or db.connect()
    try:
        found = resolver(company, log=log, refresh=refresh)
        res.careers_url, res.how = found.get("url", ""), found.get("how", "")
        if not res.careers_url:
            res.note = "no careers page found"
            log(f"discover: {company}: no careers page found")
            return res
        if found.get("blocked"):
            res.note = f"blocked: {res.careers_url} answers with a bot check; open it yourself"
            log(f"discover: {company}: {res.note}")
            return res
        try:
            items = collector(found, company, listings_mod.target_queries(config), log=log)
        except listings_mod.Blocked as e:
            res.note = f"blocked: {e}"
            log(f"discover: {company}: {res.note}")
            return res
        if limit:
            items = items[:limit]
        res.listings = len(items)
        log(f"discover: {company}: {len(items)} listings on {found.get('board', {}) and found['board']['family'] or 'its careers page'}"
            f" ({res.careers_url}, found by {res.how})")
        decisions = {i: (locations.classify(x.locations, x.title, snippet=x.snippet, judge=location_judge) if only_us else None)
                     for i, x in enumerate(items)}
        # embeddings: one batched pass over the titles no pattern hits (and the role descriptions)
        embed_fn = None
        if embed is not None and config.get("roles"):
            base = matching.model_embed() if embed == "model" else embed
            todo = [matching.semantic_text(x.title, x.snippet) for i, x in enumerate(items)
                    if not (decisions[i] and decisions[i].verdict == "non_us")
                    and matching.regex_stage(x.title, config) == ("ok", [])]
            if len(todo) > MAX_SEMANTIC:
                log(f"discover: {company}: {len(todo)} titles match no pattern; embedding the first {MAX_SEMANTIC}")
                todo = todo[:MAX_SEMANTIC]
            try:
                embed_fn = matching.precomputed(todo + matching.role_texts(config), base) if todo else None
            except Exception as e:
                log(f"discover: {company}: embeddings failed ({type(e).__name__}); regex matches only")
        judge_fn = matching.model_judge(load_preferences()) if judge == "model" else judge
        judged = 0
        for i, x in enumerate(items):
            loc = decisions[i]
            if loc is not None and loc.verdict == "non_us":
                m = matching.Match("skipped", "none", reason="not evaluated (outside the United States)")
            else:
                use = judge_fn if judged < MAX_JUDGE else None
                try:
                    m = matching.match_listing(x.title, x.snippet, config, embed_fn, use)
                except Exception as e:  # a judge or embedding error on one listing keeps the prior, not the run
                    m = matching.match_listing(x.title, x.snippet, config, None, None)
                    log(f"discover: {company}: judge failed on {x.title[:40]!r} ({type(e).__name__}); prior kept")
                if use is not None and m.stage in ("judge",):
                    judged += 1
            outcome = jobqueue.enqueue(conn, x, m, loc)
            res.counts[outcome] = res.counts.get(outcome, 0) + 1
            if outcome == "queued":
                res.queued.append((x.title, "; ".join(x.locations), x.url, m.role_id, m.score))
        log(f"discover: {company}: " + ", ".join(f"{n} {k}" for k, n in sorted(res.counts.items())))
    except Exception as e:
        res.note = f"error: {type(e).__name__}: {str(e)[:150]}"
        log(f"discover: {company}: {res.note}")
    return res


def discover_many(companies: list[str], log=print, **kw) -> list[Result]:
    """Companies in turn; one company's problem never stops the others."""
    out = []
    for c in dict.fromkeys(x.strip() for x in companies if x and x.strip()):
        out.append(discover(c, log=log, **kw))
    return out
