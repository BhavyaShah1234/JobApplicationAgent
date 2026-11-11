"""What the history says (step 15c). Advisory: nothing here changes tailoring or checker-site choice unless the candidate
turns on a switch (`learning_use_site_mix`, `learning_use_tailoring_hints`, `learning_use_lessons`, all off by default).

Two questions, asked of resolved applications only (pending ones are still waiting and are left out; withdrawn are
left out):
- **Which checker sites' scores go with replies?** For each site, the AUC of its score for telling applications that got
  a positive reply (assessment, screen, interview, offer) from those that did not, with a bootstrap 99% interval:
  above 0.5 means a higher score went with more replies. Used instead of a bandit over sites: it measures what the
  candidate wants to know (does this checker predict anything) and uses every application, not just a high / low split.
- **Which skills went with replies, within a role family?** Fisher's exact test per skill listed on the resumes, with
  the Benjamini-Hochberg correction across the skills tested.

**Guardrails.** A group is only read with `MIN_ARM` (15) applications in each outcome class (positive, and not), and
a finding needs a 99% interval clear of 0.5 (sites) or a corrected p under 5% (skills). Below that the report says "not
enough data". Correlation is not cause: company size, seniority, referrals and timing are not in this data, and a skill
the resume cannot truthfully carry is never a suggestion.
"""

import hashlib
import json
import math
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime

from jobagent.tools.outcome_stats import AppEvents, Status

MIN_ARM = 15
SITE_CI = 0.99
KEYWORD_FDR = 0.05
BOOTSTRAPS = 400


def resolved_outcomes(apps: list[AppEvents], sts: dict[int, Status]) -> list[tuple[AppEvents, int]]:
    """(application, 1 if it got a positive reply else 0) for resolved applications only."""
    return [(a, 1 if sts[a.id].label == "positive" else 0) for a in apps if sts[a.id].label in ("positive", "negative", "ghosted")]


# --- statistics ----------------------------------------------------------------------------------------------------

def auc(pos: list[float], neg: list[float]) -> float:
    """P(a positive application's score beats a non-positive one's), ties half: the Mann-Whitney statistic."""
    if not pos or not neg:
        return 0.5
    allv = sorted([(v, 1) for v in pos] + [(v, 0) for v in neg])
    ranks, i = {}, 0
    while i < len(allv):
        j = i
        while j < len(allv) and allv[j][0] == allv[i][0]:
            j += 1
        for k in range(i, j):
            ranks[k] = (i + j + 1) / 2
        i = j
    r_pos = sum(ranks[k] for k, (_, y) in enumerate(allv) if y == 1)
    return (r_pos - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))


def bootstrap_auc(pos: list[float], neg: list[float], level: float = SITE_CI, n: int = BOOTSTRAPS, seed: int = 7):
    rng = random.Random(seed)
    vals = sorted(auc([rng.choice(pos) for _ in pos], [rng.choice(neg) for _ in neg]) for _ in range(n))
    lo, hi = (1 - level) / 2, 1 - (1 - level) / 2
    return vals[int(lo * (n - 1))], vals[int(hi * (n - 1))]


def _log_comb(n: int, k: int) -> float:
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def fisher_exact(a: int, b: int, c: int, d: int) -> float:
    """Two-sided Fisher exact p of the 2x2 table [[a, b], [c, d]] (a, c: with the skill; a, b: positive)."""
    r1, r2, c1, n = a + b, c + d, a + c, a + b + c + d
    denom = _log_comb(n, c1)
    prob = lambda x: math.exp(_log_comb(r1, x) + _log_comb(r2, c1 - x) - denom)  # noqa: E731
    lo, hi = max(0, c1 - r2), min(r1, c1)
    p_obs = prob(a)
    return min(1.0, sum(prob(x) for x in range(lo, hi + 1) if prob(x) <= p_obs * (1 + 1e-9)))


def bh_adjust(pvals: list[float]) -> list[float]:
    """Benjamini-Hochberg adjusted p-values (false discovery rate), in the input order."""
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    adj, prev = [0.0] * m, 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        prev = min(prev, pvals[i] * m / rank)
        adj[i] = prev
    return adj


# --- findings ------------------------------------------------------------------------------------------------------

@dataclass
class SiteEffect:
    site: str
    n: int
    positives: int
    auc: float | None
    ci: tuple[float, float] | None
    verdict: str            # not enough data | predicts replies | inverse | no clear relationship
    evidence: list = field(default_factory=list)       # application ids


def site_effects(apps: list[AppEvents], sts: dict[int, Status], min_arm: int = MIN_ARM) -> list[SiteEffect]:
    rows = resolved_outcomes(apps, sts)
    sites = sorted({s for a, _ in rows for s in a.ats_scores})
    out = []
    for site in sites:
        scored = [(a, y) for a, y in rows if a.ats_scores.get(site) is not None]
        pos = [float(a.ats_scores[site]) for a, y in scored if y]
        neg = [float(a.ats_scores[site]) for a, y in scored if not y]
        ev = [a.id for a, _ in scored]
        if len(pos) < min_arm or len(neg) < min_arm:
            out.append(SiteEffect(site, len(scored), len(pos), None, None, "not enough data", ev))
            continue
        v, ci = auc(pos, neg), bootstrap_auc(pos, neg)
        verdict = "predicts replies" if ci[0] > 0.5 else "inverse" if ci[1] < 0.5 else "no clear relationship"
        out.append(SiteEffect(site, len(scored), len(pos), v, ci, verdict, ev))
    return out


@dataclass
class KeywordEffect:
    keyword: str
    role_id: str
    with_n: int
    with_pos: int
    without_n: int
    without_pos: int
    odds_ratio: float
    p: float
    p_adj: float
    verdict: str            # more replies | fewer replies | no clear relationship
    evidence: list = field(default_factory=list)


def keyword_effects(apps: list[AppEvents], sts: dict[int, Status], role_id: str | None = None,
                    min_arm: int = MIN_ARM) -> list[KeywordEffect]:
    """Skills listed on enough resumes (and left off enough) to compare, within one role family (all if None)."""
    rows = [(a, y) for a, y in resolved_outcomes(apps, sts) if role_id is None or a.role_id == role_id]
    if sum(y for _, y in rows) < min_arm or sum(1 - y for _, y in rows) < min_arm:
        return []
    tested = []
    for kw in sorted({k for a, _ in rows for k in a.keywords}):
        w = [(a, y) for a, y in rows if kw in a.keywords]
        wo = [(a, y) for a, y in rows if kw not in a.keywords]
        if len(w) < min_arm or len(wo) < min_arm:
            continue
        a_, b_ = sum(y for _, y in w), sum(1 - y for _, y in w)
        c_, d_ = sum(y for _, y in wo), sum(1 - y for _, y in wo)
        odds = ((a_ + .5) * (d_ + .5)) / ((b_ + .5) * (c_ + .5))
        tested.append((kw, w, wo, a_, c_, odds, fisher_exact(a_, b_, c_, d_)))
    adj = bh_adjust([t[-1] for t in tested])
    out = []
    for (kw, w, wo, a_, c_, odds, p), pa in zip(tested, adj):
        verdict = "no clear relationship" if pa >= KEYWORD_FDR else "more replies" if odds > 1 else "fewer replies"
        out.append(KeywordEffect(kw, role_id or "", len(w), a_, len(wo), c_, round(odds, 2), p, pa, verdict,
                                 [a.id for a, _ in w + wo]))
    return out


# --- lessons -------------------------------------------------------------------------------------------------------

def _snapshot(rows: list[tuple[AppEvents, int]]) -> str:
    return hashlib.sha1(json.dumps(sorted((a.id, y) for a, y in rows)).encode()).hexdigest()[:12]


def findings(apps: list[AppEvents], sts: dict[int, Status]) -> list[dict]:
    """The lessons the data supports right now: {key, scope, text, evidence, actionable}. Empty below the floors.
    `actionable` is False for "this site's score showed no relationship": informational, not a recommendation."""
    out = []
    for s in site_effects(apps, sts):
        if s.verdict in ("predicts replies", "inverse", "no clear relationship"):
            what = {"predicts replies": "went with more replies", "inverse": "went with FEWER replies",
                    "no clear relationship": "showed no relationship with replies"}[s.verdict]
            out.append({"key": f"site:{s.site}", "scope": "global", "actionable": s.verdict != "no clear relationship",
                        "text": f"{s.site}: a higher score {what} (AUC {s.auc:.2f}, {SITE_CI:.0%} interval "
                                f"{s.ci[0]:.2f}-{s.ci[1]:.2f}; {s.positives} positive of {s.n} resolved).",
                        "evidence": {"applications": s.evidence, "auc": round(s.auc, 3), "ci": [round(x, 3) for x in s.ci]}})
    for role in sorted({a.role_id for a in apps if a.role_id}):
        for k in keyword_effects(apps, sts, role):
            if k.verdict != "no clear relationship":
                out.append({"key": f"keyword:{k.keyword}@{role}", "scope": role, "actionable": True,
                            "text": f"In {role} applications, resumes listing '{k.keyword}' went with {k.verdict} "
                                    f"({k.with_pos} of {k.with_n} vs {k.without_pos} of {k.without_n}; odds ratio "
                                    f"{k.odds_ratio}, corrected p {k.p_adj:.3f}). Association, not proof; list it only if it is true.",
                            "evidence": {"applications": k.evidence, "odds_ratio": k.odds_ratio, "p_adj": round(k.p_adj, 4)}})
    return out


def reflect(conn, apps: list[AppEvents], sts: dict[int, Status], as_of: datetime | None = None) -> dict:
    """Write the supported lessons, retire the ones the data no longer supports. Returns {new, kept, retired, found}."""
    found = findings(apps, sts)
    snap = _snapshot(resolved_outcomes(apps, sts))
    now = (as_of or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
    active = {r["key"]: r for r in conn.execute("SELECT * FROM lessons WHERE status = 'active'")}
    new = kept = 0
    with conn:
        for f in found:
            if f["key"] in active and active[f["key"]]["text"] == f["text"]:
                kept += 1
                continue
            if f["key"] in active:   # same subject, new numbers: the old lesson is superseded
                conn.execute("UPDATE lessons SET status = 'retired', retired_at = ? WHERE id = ?", (now, active[f["key"]]["id"]))
            conn.execute("INSERT INTO lessons (key, scope, text, evidence, snapshot, created_at) VALUES (?,?,?,?,?,?)",
                         (f["key"], f["scope"], f["text"], json.dumps(f["evidence"]), snap, now))
            new += 1
        keys = {f["key"] for f in found}
        retired = 0
        for key, row in active.items():
            if key not in keys:
                conn.execute("UPDATE lessons SET status = 'retired', retired_at = ? WHERE id = ?", (now, row["id"]))
                retired += 1
    return {"found": len(found), "actionable": sum(1 for f in found if f["actionable"]), "new": new, "kept": kept,
            "retired": retired}


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


def site_mix(sites: tuple[str, ...], conn, apps: list[AppEvents] | None = None, sts: dict | None = None,
             settings=None) -> tuple[str, ...]:
    """The checker sites to run. Unchanged unless `learning_use_site_mix` is on; then a site whose score has been shown
    to carry no signal (a 99% interval inside 0.45-0.55 with twice the minimum in each class) is dropped. Never drops
    the last site, and never drops one on weak evidence."""
    from jobagent.config import get_settings
    from jobagent.tools import outcome_stats as S

    s = settings or get_settings()
    if not s.learning_use_site_mix:
        return tuple(sites)
    if apps is None:
        apps = S.load(conn)
        sts = S.statuses(apps, datetime.now(UTC))
    useless = {e.site for e in site_effects(apps, sts, min_arm=2 * MIN_ARM)
               if e.ci is not None and e.ci[0] >= 0.45 and e.ci[1] <= 0.55}
    kept = tuple(x for x in sites if x not in useless)
    return kept or tuple(sites)
