"""What the history says (step 15c). Advisory: nothing here changes tailoring or checker-site choice unless the candidate
turns on a switch (`learning_use_tailoring_hints`, `learning_use_lessons`; `learning_use_site_mix` is reported as unsupported),
all off by default.

Two questions, asked of resolved applications only (pending ones are still waiting and are left out, as are withdrawn):
- **Which checker sites' scores go with replies?** The weighted AUC of each site's score for telling applications that got
  a positive reply (assessment, screen, interview, offer) from those that did not.
- **Which skills went with replies, within a role family?** The weighted difference in the share of positive replies
  between resumes that listed a skill and resumes that did not.

**One procedure for the whole family.** Every site and every skill-by-role-family comparison that clears the data floor is
tested TOGETHER by a Westfall-Young max-statistic permutation test: outcomes are shuffled (whole companies move together,
and only within a role family), the largest standardized statistic over the entire family is recorded for each shuffle, and
a finding must beat that distribution. This holds the chance of ANY false recommendation in an effect-free world to about
`FWER` (5%) however many sites, skills and role families are examined, and it respects that applications to one company
are not independent.

**Ghosted applications** count as negatives with weight 0.5 (a silence may still turn into a reply), in every statistic.
**Still-pending applications are censored and left out**; they are NOT counted as fractional failures.

**Held-out check.** The earliest 70% of resolved applications (by submission date) are used to find effects; the latest 30%
are only used to confirm them. A finding is actionable only if the later applications point the same way; if they point
the other way it is reported as contradicted and not acted on; without enough later applications it stays unconfirmed.

**What it cannot say.** These are associations in the candidate's own history. Which checker a resume scored well on, and
which skills it listed, were not randomly assigned, so a finding does NOT show that raising a score or adding a skill
would bring more replies; company size, seniority, referrals and timing are not in the data. A skill the resume cannot
truthfully carry is never a suggestion. Below the floors (`MIN_ARM` positive and `MIN_ARM` other applications per group) the
report says "not enough data".
"""

import hashlib
import json
import math
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime

from jobagent.tools.outcome_stats import AppEvents, Status

MIN_ARM = 15
FWER = 0.05                 # chance of ANY false recommendation in a world with no effects
PERMS = 299                 # shuffles; the smallest possible adjusted p is 1 / (PERMS + 1)
HOLDOUT_FRACTION = 0.3      # the latest applications are kept back to confirm what the earlier ones suggest
MIN_HOLDOUT_ARM = 5         # in each outcome class (sites) or each skill group (skills) before the hold-out can confirm
SEED = 7
NO_KEYWORDS = frozenset({"mismatch", "unknown", "unverifiable"})   # snapshots whose skill list is not known to be the submitted one


@dataclass
class Row:
    """One resolved application: y = 1 for a positive reply, w = its weight (a ghosted application is a weak negative)."""
    app: AppEvents
    y: int
    w: float


def resolved_rows(apps: list[AppEvents], sts: dict[int, Status]) -> list[Row]:
    return [Row(a, 1 if sts[a.id].label == "positive" else 0, sts[a.id].weight if sts[a.id].label == "ghosted" else 1.0)
            for a in apps if a.id in sts and sts[a.id].label in ("positive", "negative", "ghosted")]


def resolved_outcomes(apps: list[AppEvents], sts: dict[int, Status]) -> list[tuple[AppEvents, int]]:
    """(application, 1 if it got a positive reply else 0) for resolved applications only."""
    return [(r.app, r.y) for r in resolved_rows(apps, sts)]


# --- statistics ----------------------------------------------------------------------------------------------------

def auc(pos: list[float], neg: list[float]) -> float:
    """P(a positive application's score beats a non-positive one's), ties half: the Mann-Whitney statistic (unweighted)."""
    if not pos or not neg:
        return 0.5
    return _auc_stat([(v, 1, 1.0) for v in pos] + [(v, 0, 1.0) for v in neg])[0]


def _auc_stat(items: list[tuple[float, int, float]]) -> tuple[float, float]:
    """(weighted AUC, standardized z) of (score, y, weight) items. z uses Kish effective sizes, so ghost weights count."""
    items = sorted(items, key=lambda x: x[0])
    num = cum_neg = wp = wn = sp = sn = 0.0
    i = 0
    while i < len(items):
        j, gp, gn = i, 0.0, 0.0
        while j < len(items) and items[j][0] == items[i][0]:
            _, y, w = items[j]
            if y:
                gp += w
                sp += w * w
            else:
                gn += w
                sn += w * w
            j += 1
        num += gp * (cum_neg + 0.5 * gn)
        cum_neg += gn
        wp += gp
        wn += gn
        i = j
    if wp <= 0 or wn <= 0:
        return 0.5, 0.0
    a = num / (wp * wn)
    ne_p, ne_n = wp * wp / sp, wn * wn / sn
    sd = math.sqrt((ne_p + ne_n + 1) / (12 * ne_p * ne_n))
    return a, (a - 0.5) / sd


def _rate_stat(with_: list[tuple[int, float]], without: list[tuple[int, float]]) -> tuple[float, float, float]:
    """(weighted positive share with the skill, without it, standardized z of the difference) for (y, weight) pairs."""
    def part(xs):
        sw = sum(w for _, w in xs)
        return sw, sum(w for y, w in xs if y), sum(w * w for _, w in xs)
    s1, y1, q1 = part(with_)
    s0, y0, q0 = part(without)
    if s1 <= 0 or s0 <= 0:
        return 0.0, 0.0, 0.0
    p1, p0, pool = y1 / s1, y0 / s0, (y1 + y0) / (s1 + s0)
    var = pool * (1 - pool) * (q1 / (s1 * s1) + q0 / (s0 * s0))
    return p1, p0, 0.0 if var <= 0 else (p1 - p0) / math.sqrt(var)


# --- the family of hypotheses --------------------------------------------------------------------------------------

def _score(a: AppEvents, site: str):
    v = a.ats_scores.get(site)
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _groups(rows: list[Row], role: str, kw: str) -> tuple[list[Row], list[Row]]:
    sub = [r for r in rows if r.app.role_id == role and r.app.snapshot_status not in NO_KEYWORDS]
    return [r for r in sub if kw in r.app.keywords], [r for r in sub if kw not in r.app.keywords]


def _specs(rows: list[Row], min_arm: int) -> tuple[list[tuple], list[tuple]]:
    """(eligible hypotheses, ineligible ones that still deserve a line in the report). A hypothesis is
    ("site", site, "") or ("kw", skill, role)."""
    ok, thin = [], []
    for site in sorted({s for r in rows for s in r.app.ats_scores}):
        sc = [r for r in rows if _score(r.app, site) is not None]
        pos = sum(r.y for r in sc)
        (ok if pos >= min_arm and len(sc) - pos >= min_arm else thin).append(("site", site, ""))
    for role in sorted({r.app.role_id for r in rows if r.app.role_id}):
        in_role = [r for r in rows if r.app.role_id == role and r.app.snapshot_status not in NO_KEYWORDS]
        pos = sum(r.y for r in in_role)
        if pos < min_arm or len(in_role) - pos < min_arm:
            continue
        for kw in sorted({k for r in in_role for k in r.app.keywords}):
            w, wo = _groups(rows, role, kw)
            if len(w) >= min_arm and len(wo) >= min_arm:
                ok.append(("kw", kw, role))
    return ok, thin


def _stat(spec: tuple, rows: list[Row], y: list[int] | None = None, w: list[float] | None = None) -> float:
    """The standardized statistic of one hypothesis on `rows`; `y`/`w` (aligned with rows) override the outcomes (shuffles)."""
    yy = y if y is not None else [r.y for r in rows]
    ww = w if w is not None else [r.w for r in rows]
    kind, key, role = spec
    if kind == "site":
        return _auc_stat([(_score(r.app, key), yy[i], ww[i]) for i, r in enumerate(rows) if _score(r.app, key) is not None])[1]
    with_, without = [], []
    for i, r in enumerate(rows):
        if r.app.role_id == role and r.app.snapshot_status not in NO_KEYWORDS:
            (with_ if key in r.app.keywords else without).append((yy[i], ww[i]))
    return _rate_stat(with_, without)[2]


def _shuffler(rows: list[Row]):
    """A function that returns the outcomes (y, w) shuffled so that nothing but the null is kept: within a role family,
    applications to the same company move together (one company's decision is not many independent observations), and
    companies with the same number of applications trade outcome vectors."""
    bins: dict[tuple, dict[str, list[int]]] = {}
    for i, r in enumerate(rows):
        company = (getattr(r.app, "company", "") or "").strip().lower() or f"#{i}"
        bins.setdefault((r.app.role_id,), {}).setdefault(company, []).append(i)
    pools: list[list[list[int]]] = []
    for clusters in bins.values():
        by_size: dict[int, list[list[int]]] = {}
        for ix in clusters.values():
            by_size.setdefault(len(ix), []).append(ix)
        pools.extend(by_size.values())

    def shuffle(rng: random.Random) -> tuple[list[int], list[float]]:
        y, w = [r.y for r in rows], [r.w for r in rows]
        y2, w2 = list(y), list(w)
        for group in pools:
            order = list(range(len(group)))
            rng.shuffle(order)
            for dst, src in zip(group, (group[k] for k in order)):
                for d, s in zip(dst, src):
                    y2[d], w2[d] = y[s], w[s]
        return y2, w2
    return shuffle


@dataclass
class SiteEffect:
    site: str
    n: int
    positives: int
    auc: float | None
    z: float | None
    p_adj: float | None     # family-wise adjusted p
    verdict: str            # not enough data | predicts replies | inverse | no clear relationship
    holdout: str = "untested"       # confirmed | contradicted | untested (on the latest applications)
    evidence: list = field(default_factory=list)       # application ids


@dataclass
class KeywordEffect:
    keyword: str
    role_id: str
    with_n: int
    with_pos: int
    without_n: int
    without_pos: int
    odds_ratio: float       # weighted, with a half added to each cell
    z: float
    p_adj: float
    verdict: str            # more replies | fewer replies | no clear relationship
    holdout: str = "untested"
    evidence: list = field(default_factory=list)


@dataclass
class Analysis:
    sites: list[SiteEffect]
    keywords: list[KeywordEffect]
    params: dict


def _split(rows: list[Row]) -> tuple[list[Row], list[Row]]:
    ordered = sorted(rows, key=lambda r: (r.app.submitted_at, r.app.id))
    cut = int(round(len(ordered) * (1 - HOLDOUT_FRACTION)))
    return ordered[:cut], ordered[cut:]


def _direction(spec: tuple, later: list[Row], z: float) -> str:
    """Do the latest applications point the same way as the finding? Needs MIN_HOLDOUT_ARM in each class / group."""
    kind, key, role = spec
    if kind == "site":
        sc = [r for r in later if _score(r.app, key) is not None]
        pos = sum(r.y for r in sc)
        if pos < MIN_HOLDOUT_ARM or len(sc) - pos < MIN_HOLDOUT_ARM:
            return "untested"
        a, _ = _auc_stat([(_score(r.app, key), r.y, r.w) for r in sc])
        d = a - 0.5
    else:
        w, wo = _groups(later, role, key)
        if len(w) < MIN_HOLDOUT_ARM or len(wo) < MIN_HOLDOUT_ARM:
            return "untested"
        p1, p0, _ = _rate_stat([(r.y, r.w) for r in w], [(r.y, r.w) for r in wo])
        d = p1 - p0
    if d == 0:
        return "untested"
    return "confirmed" if (d > 0) == (z > 0) else "contradicted"


def analyze(apps: list[AppEvents], sts: dict[int, Status], min_arm: int = MIN_ARM, perms: int = PERMS,
            seed: int = SEED, fwer: float = FWER) -> Analysis:
    """The whole recommendation procedure, once: eligible hypotheses are found on the earliest 70% of resolved applications,
    tested as one family by a max-statistic permutation test, and checked against the latest 30%."""
    rows = resolved_rows(apps, sts)
    train, later = _split(rows)
    ok, thin = _specs(train, min_arm)
    observed = {s: _stat(s, train) for s in ok}
    exceed = {s: 0 for s in ok}
    if ok and perms > 0:
        rng, shuffle = random.Random(seed), _shuffler(train)
        for _ in range(perms):
            y2, w2 = shuffle(rng)
            top = max(abs(_stat(s, train, y2, w2)) for s in ok)
            for s in ok:
                exceed[s] += top >= abs(observed[s]) - 1e-12
    padj = {s: (1 + exceed[s]) / (perms + 1) for s in ok}
    sites, kws = [], []
    for spec in ok:
        kind, key, role = spec
        z, pa = observed[spec], padj[spec]
        hold = _direction(spec, later, z) if pa < fwer else "untested"
        if kind == "site":
            sc = [r for r in train if _score(r.app, key) is not None]
            a, _ = _auc_stat([(_score(r.app, key), r.y, r.w) for r in sc])
            verdict = "no clear relationship" if pa >= fwer else "predicts replies" if z > 0 else "inverse"
            sites.append(SiteEffect(key, len(sc), sum(r.y for r in sc), a, z, pa, verdict, hold, [r.app.id for r in sc]))
        else:
            w, wo = _groups(train, role, key)
            sw = lambda rs, yv: sum(r.w for r in rs if r.y == yv)  # noqa: E731
            odds = ((sw(w, 1) + .5) * (sw(wo, 0) + .5)) / ((sw(w, 0) + .5) * (sw(wo, 1) + .5))
            verdict = "no clear relationship" if pa >= fwer else "more replies" if z > 0 else "fewer replies"
            kws.append(KeywordEffect(key, role, len(w), sum(r.y for r in w), len(wo), sum(r.y for r in wo), round(odds, 2),
                                     z, pa, verdict, hold, [r.app.id for r in w + wo]))
    for _, site, _ in thin:
        sc = [r for r in rows if _score(r.app, site) is not None]
        sites.append(SiteEffect(site, len(sc), sum(r.y for r in sc), None, None, None, "not enough data", "untested",
                                [r.app.id for r in sc]))
    sites.sort(key=lambda e: e.site)
    params = {"min_arm": min_arm, "perms": perms, "seed": seed, "fwer": fwer, "holdout_fraction": HOLDOUT_FRACTION,
              "min_holdout_arm": MIN_HOLDOUT_ARM, "hypotheses": len(ok), "train": len(train), "holdout": len(later),
              "ghost_weight": sorted({round(r.w, 3) for r in rows if r.w != 1.0})}
    return Analysis(sites, kws, params)


def site_effects(apps: list[AppEvents], sts: dict[int, Status], min_arm: int = MIN_ARM, **kw) -> list[SiteEffect]:
    return analyze(apps, sts, min_arm, **kw).sites


def keyword_effects(apps: list[AppEvents], sts: dict[int, Status], role_id: str | None = None,
                    min_arm: int = MIN_ARM, **kw) -> list[KeywordEffect]:
    """Skills compared within a role family. The correction covers the WHOLE family (all sites and skills), not just the
    role asked for, so this is a view of `analyze`, not a separate test."""
    return [k for k in analyze(apps, sts, min_arm, **kw).keywords if role_id is None or k.role_id == role_id]


# --- lessons -------------------------------------------------------------------------------------------------------

def _snapshot(rows: list[Row], params: dict | None = None) -> str:
    """A hash of everything a finding depends on: the outcome, weight, scores and skills of every resolved application and
    the analysis parameters. Any change in any of them is a new version."""
    body = sorted((r.app.id, r.y, round(r.w, 3), sorted((k, str(v)) for k, v in r.app.ats_scores.items()),
                   sorted(r.app.keywords), r.app.role_id, r.app.snapshot_status, getattr(r.app, "company", "")) for r in rows)
    return hashlib.sha1(json.dumps([body, params or {}], sort_keys=True, default=str).encode()).hexdigest()[:12]


def findings(apps: list[AppEvents], sts: dict[int, Status], analysis: Analysis | None = None) -> list[dict]:
    """What the data supports right now: {key, scope, text, evidence, actionable, status}. `actionable` is true only for a
    finding that cleared the family-wise test AND points the same way on the latest applications; "no relationship" and
    unconfirmed or contradicted findings are informational. Every text says association, never cause."""
    an = analysis or analyze(apps, sts)
    out = []
    held = {"confirmed": "confirmed on the latest applications", "contradicted": "CONTRADICTED by the latest applications",
            "untested": "not yet confirmed on later applications"}
    for s in an.sites:
        if s.verdict == "not enough data":
            continue
        if s.verdict == "no clear relationship":
            what, act = "showed no clear relationship with replies", False
        else:
            what = "went with more replies" if s.verdict == "predicts replies" else "went with FEWER replies"
            act = s.holdout == "confirmed"
        out.append({"key": f"site:{s.site}", "scope": "global", "actionable": act, "status": s.holdout,
                    "text": f"{s.site}: a higher score {what} (AUC {s.auc:.2f}, adjusted p {s.p_adj:.3f}; {s.positives} positive "
                            f"of {s.n} resolved)" + ("" if s.verdict == "no clear relationship" else f"; {held[s.holdout]}")
                            + ". An association in this history, not proof that raising the score brings replies.",
                    "evidence": {"applications": s.evidence, "auc": round(s.auc, 3), "z": round(s.z, 3),
                                 "p_adj": round(s.p_adj, 4), "holdout": s.holdout}})
    for k in an.keywords:
        if k.verdict == "no clear relationship":
            continue
        out.append({"key": f"keyword:{k.keyword}@{k.role_id}", "scope": k.role_id, "actionable": k.holdout == "confirmed",
                    "status": k.holdout,
                    "text": f"In {k.role_id} applications, resumes listing '{k.keyword}' went with {k.verdict} "
                            f"({k.with_pos} of {k.with_n} vs {k.without_pos} of {k.without_n}; odds ratio {k.odds_ratio}, "
                            f"adjusted p {k.p_adj:.3f}); {held[k.holdout]}. Association, not proof; list it only if it is true.",
                    "evidence": {"applications": k.evidence, "odds_ratio": k.odds_ratio, "z": round(k.z, 3),
                                 "p_adj": round(k.p_adj, 4), "holdout": k.holdout}})
    return out


def reflect(conn, apps: list[AppEvents], sts: dict[int, Status], as_of: datetime | None = None,
            analysis: Analysis | None = None) -> dict:
    """Write the supported lessons, retire the ones the data no longer supports. A lesson is a new version whenever its
    wording OR the evidence snapshot behind it changed. Only actionable findings and "no relationship" notes are stored:
    findings that are unconfirmed or contradicted stay in the report. Returns {new, kept, retired, found, actionable}."""
    an = analysis or analyze(apps, sts)
    found = findings(apps, sts, an)
    storable = [f for f in found if f["actionable"] or f["text"].find("no clear relationship") >= 0]
    snap = _snapshot(resolved_rows(apps, sts), an.params)
    now = (as_of or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
    active = {r["key"]: r for r in conn.execute("SELECT * FROM lessons WHERE status = 'active'")}
    new = kept = 0
    with conn:
        for f in storable:
            evidence = json.dumps(f["evidence"], sort_keys=True)
            cur = active.get(f["key"])
            if cur and cur["text"] == f["text"] and cur["snapshot"] == snap and cur["evidence"] == evidence:
                kept += 1
                continue
            if cur:   # same subject, new numbers or new evidence: the old lesson is superseded, not edited
                conn.execute("UPDATE lessons SET status = 'retired', retired_at = ? WHERE id = ?", (now, cur["id"]))
            conn.execute("INSERT INTO lessons (key, scope, text, evidence, snapshot, created_at) VALUES (?,?,?,?,?,?)",
                         (f["key"], f["scope"], f["text"], evidence, snap, now))
            new += 1
        keys = {f["key"] for f in storable}
        retired = 0
        for key, row in active.items():
            if key not in keys:
                conn.execute("UPDATE lessons SET status = 'retired', retired_at = ? WHERE id = ?", (now, row["id"]))
                retired += 1
    return {"found": len(found), "actionable": sum(1 for f in found if f["actionable"]), "new": new, "kept": kept,
            "retired": retired, "snapshot": snap}


# --- the switches (off by default) ---------------------------------------------------------------------------------

def prompt_hints(conn, role_id: str = "", settings=None) -> str:
    """Text for the tailor's prompt: skills that went with replies and the active lessons for this role family. Empty
    unless the candidate switched `learning_use_tailoring_hints` / `learning_use_lessons` on."""
    from jobagent.config import get_settings

    s = settings or get_settings()
    lines: list[str] = []
    if not (s.learning_use_tailoring_hints or s.learning_use_lessons):
        return ""
    for r in conn.execute("SELECT key, scope, text FROM lessons WHERE status = 'active' AND (scope = 'global' OR scope = ?) "
                          "ORDER BY id", (role_id or "",)):
        if r["key"].startswith("keyword:") and s.learning_use_tailoring_hints and "more replies" in r["text"]:
            lines.append(f"- {r['text']}")
        elif s.learning_use_lessons and not r["key"].startswith("site:"):
            lines.append(f"- {r['text']}")
    if not lines:
        return ""
    return ("\n\nWHAT PAST APPLICATIONS SHOWED (advisory; list a skill only if the candidate really has it):\n"
            + "\n".join(lines))


def site_mix(sites: tuple[str, ...], conn=None, apps=None, sts=None, settings=None) -> tuple[str, ...]:
    """The checker sites to run: always all of them. Dropping a site would change the five-site average the ATS loop reports
    (and a site's score predicting replies in the candidate's own history says nothing about whether it measures the resume),
    so `learning_use_site_mix` has no effect; `site_mix_unsupported()` says so and the commands print it."""
    return tuple(sites)


def site_mix_unsupported(settings=None) -> str:
    """"" unless the candidate switched `learning_use_site_mix` on; then why that switch does nothing."""
    from jobagent.config import get_settings

    s = settings or get_settings()
    if not s.learning_use_site_mix:
        return ""
    return ("LEARNING_USE_SITE_MIX is not supported and has no effect: the ATS loop always runs all required checker sites so "
            "that the cross-site average stays comparable between applications.")
