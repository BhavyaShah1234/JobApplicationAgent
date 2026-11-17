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
MAX_JUDGE = 80       # model-judged listings per company (the rest are NOT auto-validated: they go to needs_review)


@dataclass
class Result:
    company: str
    careers_url: str = ""
    how: str = ""
    listings: int = 0
    counts: dict = field(default_factory=dict)
    note: str = ""            # blocked / no careers page / error
    budget: dict = field(default_factory=dict)   # what was actually spent and what was left unevaluated (see discover)
    queued: list = field(default_factory=list)   # (title, location, url, role, score)


def load_preferences() -> dict:
    p = get_settings().data_dir / "preferences.yaml"
    return (yaml.safe_load(p.read_text()) if p.exists() else None) or {}


def discover(company: str, config: dict | None = None, conn=None, log=print, refresh: bool = False,
             resolver=careers_mod.resolve, collector=listings_mod.collect, judge="model", embed="model",
             location_judge=None, limit: int | None = None, only_us: bool = True,
             detail_locations=listings_mod.workday_locations) -> Result:
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
        # embeddings: one batched pass over the titles no pattern hits (and the role descriptions), within the budget
        embed_fn, embed_note = None, ""
        stats: dict = {}
        if embed is not None and config.get("roles"):
            base = matching.model_embed() if embed == "model" else embed
            todo = [matching.semantic_text(x.title, x.snippet) for i, x in enumerate(items)
                    if not (decisions[i] and decisions[i].verdict == "non_us")
                    and matching.regex_stage(x.title, config) == ("ok", [])]
            unique = list(dict.fromkeys(todo))
            capped = unique[:MAX_SEMANTIC]
            if len(unique) > MAX_SEMANTIC:
                log(f"discover: {company}: {len(unique)} titles match no pattern; embedding only the first {MAX_SEMANTIC}, "
                    f"the other {len(unique) - MAX_SEMANTIC} are left unevaluated (recorded as skipped, not embedded on demand)")
            try:
                embed_fn = matching.precomputed(capped + matching.role_texts(config), base, stats=stats) if capped else None
            except Exception as e:
                embed_note = f"embeddings failed ({type(e).__name__})"
                log(f"discover: {company}: {embed_note}; titles that match no pattern are not evaluated")
        res.budget = {"embedding_cap": MAX_SEMANTIC, "embedding_texts": stats.get("embedded", 0),
                      "embedding_calls": stats.get("calls", 0), "judge_cap": MAX_JUDGE, "judged": 0,
                      "judge_failed": 0, "judge_skipped_budget": 0, "unevaluated_semantic": 0}
        judge_fn = matching.model_judge(load_preferences()) if judge == "model" else judge
        for i, x in enumerate(items):
            loc = decisions[i]
            if loc is not None and loc.verdict == "non_us":
                m = matching.Match("skipped", "none", reason="not evaluated (outside the United States)")
            else:
                budget_left = res.budget["judged"] < MAX_JUDGE
                use = judge_fn if (judge_fn is not None and budget_left) else None
                why = "" if judge_fn is None else ("" if budget_left else "judge budget reached")   # no judge by choice: no reason
                try:
                    m = matching.match_listing(x.title, x.snippet, config, embed_fn, use, degraded=why)
                    if use is not None:
                        res.budget["judged"] += 1
                except Exception as e:  # the judge failed on one listing: NOT validated, so it is reviewed, never auto-queued
                    m = matching.match_listing(x.title, x.snippet, config, embed_fn, None, degraded=f"judge failed: {type(e).__name__}")
                    res.budget["judge_failed"] += 1
                    log(f"discover: {company}: judge failed on {x.title[:40]!r} ({type(e).__name__}); sent to review, not queued")
                if why:
                    res.budget["judge_skipped_budget"] += 1
                if m.detail.get("degraded") == "embedding budget":
                    res.budget["unevaluated_semantic"] += 1
                elif embed_note and m.verdict == "skipped" and m.stage == "none" and m.reason.startswith("no target role fits"):
                    res.budget["unevaluated_semantic"] += 1       # the semantic check could not run: say so, do not imply "no fit"
                    m = matching.Match("skipped", "none", reason=f"not evaluated ({embed_note})", detail={"degraded": embed_note})
            if (loc is not None and loc.verdict == 'ambiguous' and m.verdict != 'skipped'
                    and x.portal == 'workday'):
                try:
                    expanded=detail_locations(x)
                    if expanded != x.locations:
                        x.locations=expanded
                        loc=locations.classify(expanded,x.title,snippet=x.snippet,judge=location_judge)
                except listings_mod.Blocked:
                    log(f'discover: {company}: location detail refused for {x.title!r}; left for review')
                except Exception as exc:
                    log(f'discover: {company}: location detail failed ({type(exc).__name__}); left for review')
            outcome = jobqueue.enqueue(conn, x, m, loc)
            res.counts[outcome] = res.counts.get(outcome, 0) + 1
            if outcome == "queued":
                res.queued.append((x.title, "; ".join(x.locations), x.url, m.role_id, m.score))
        b_ = res.budget
        log(f"discover: {company}: budget used: {b_['embedding_texts']}/{b_['embedding_cap']} embeddings in {b_['embedding_calls']} call(s), "
            f"{b_['judged']}/{b_['judge_cap']} judgments; {b_['judge_failed']} judge failure(s), {b_['judge_skipped_budget']} candidate(s) over the "
            f"judge budget and {b_['unevaluated_semantic']} left unevaluated - none of them auto-queued")
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
