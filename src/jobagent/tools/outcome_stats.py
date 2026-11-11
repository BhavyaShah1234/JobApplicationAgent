"""Outcome labels, response timelines and rates (step 15b). Pure functions of the stored events and an `as_of` date:
everything is rebuilt from `outcome_events` on each run, so a late or corrected reply changes the labels by itself.

**Labels** (no reply is not a rejection):
- `positive`: an assessment, recruiter screen, interview or offer arrived. A later rejection does not erase it.
- `negative`: an explicit rejection with no positive stage before it.
- `ghosted`: nothing beyond the automated confirmation after the cutoff; a weak negative (weight 0.5). A late reply
  re-opens it (the labels are recomputed from the events).
- `pending`: inside the cutoff. A pending application is a *censored* observation, never counted as a failure.
- `withdrawn`: the candidate withdrew; left out of every rate.

**Cutoff.** The 90th percentile of the days to first response among applications that got one (clamped to 21-120 days)
once there are 20 such responses (per portal, else all); 45 days until then.

**Time to first response** (any stage but the automated confirmation) is summarised with the Kaplan-Meier estimator,
which handles the applications still waiting. Groups under `MIN_GROUP` resolved applications are marked too few.
"""

import json
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

POSITIVE = {"assessment", "recruiter_screen", "interview", "offer"}
RESPONSES = POSITIVE | {"rejection"}
DEFAULT_CUTOFF_DAYS = 45.0
CUTOFF_CLAMP = (21.0, 120.0)
MIN_RESPONSES_FOR_CUTOFF = 20
MIN_GROUP = 15                  # resolved applications a group needs before a rate means anything
GHOST_WEIGHT = 0.5


@dataclass
class AppEvents:
    """What stats needs of one application."""
    id: int
    submitted_at: datetime
    events: list[tuple[str, datetime]] = field(default_factory=list)     # (stage, mail Date), approved events only
    portal: str = ""
    role_id: str = ""
    resume_sha: str = ""
    ats_average: float | None = None
    ats_scores: dict = field(default_factory=dict)        # site -> score of the submitted resume
    keywords: frozenset = frozenset()                      # the skills the submitted resume listed


@dataclass
class Status:
    label: str                      # pending | positive | negative | ghosted | withdrawn
    first_response_days: float | None
    age_days: float                 # since submission, as of
    furthest: str = ""              # the furthest stage reached
    weight: float = 1.0             # of its evidence as a (weak) negative: ghosted 0.5


RANK = {"rejection": 0, "withdrawn": 1, "confirmation": 2, "assessment": 3, "recruiter_screen": 4, "interview": 5, "offer": 6}


def _days(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / 86400


def first_response(app: AppEvents) -> tuple[float, str] | None:
    """(days from submission, stage) of the first real response (not the automated confirmation), or None."""
    real = sorted((t, s) for s, t in app.events if s in RESPONSES)
    return (_days(app.submitted_at, real[0][0]), real[0][1]) if real else None


def status_of(app: AppEvents, as_of: datetime, cutoff_days: float = DEFAULT_CUTOFF_DAYS) -> Status:
    stages = [s for s, t in app.events if t <= as_of]          # events after `as_of` don't exist yet (replays)
    visible = AppEvents(app.id, app.submitted_at, [(s, t) for s, t in app.events if t <= as_of])
    fr = first_response(visible)
    age = _days(app.submitted_at, as_of)
    furthest = max(stages, key=lambda s: RANK.get(s, -1)) if stages else ""
    if "withdrawn" in stages:
        return Status("withdrawn", fr[0] if fr else None, age, furthest, 0.0)
    if any(s in POSITIVE for s in stages):
        return Status("positive", fr[0] if fr else None, age, furthest)
    if "rejection" in stages:
        return Status("negative", fr[0] if fr else None, age, furthest)
    if age > cutoff_days:
        return Status("ghosted", None, age, furthest, GHOST_WEIGHT)
    return Status("pending", None, age, furthest, 0.0)


def percentile(values: list[float], q: float) -> float:
    xs = sorted(values)
    if not xs:
        raise ValueError("no values")
    k = (len(xs) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def ghost_cutoff(apps: list[AppEvents], as_of: datetime, portal: str | None = None) -> float:
    """Days of silence after which an application counts as ghosted, learned from the responses seen so far."""
    def times(group):
        return [fr[0] for a in group if (fr := first_response(AppEvents(a.id, a.submitted_at,
                                                                     [(s, t) for s, t in a.events if t <= as_of])))]
    for group in ([a for a in apps if a.portal == portal] if portal else [], apps):
        got = times(group)
        if len(got) >= MIN_RESPONSES_FOR_CUTOFF:
            return min(max(percentile(got, 0.9), CUTOFF_CLAMP[0]), CUTOFF_CLAMP[1])
    return DEFAULT_CUTOFF_DAYS


def statuses(apps: list[AppEvents], as_of: datetime) -> dict[int, Status]:
    cutoffs: dict[str, float] = {}      # one per portal, not one per application
    for portal in {a.portal for a in apps}:
        cutoffs[portal] = ghost_cutoff(apps, as_of, portal or None)
    return {a.id: status_of(a, as_of, cutoffs[a.portal]) for a in apps}


# --- Kaplan-Meier --------------------------------------------------------------------------------------------------

def km_curve(obs: list[tuple[float, bool]]) -> list[tuple[float, float]]:
    """Survival curve [(t, S(t))] of the time to first response. `obs`: (days, responded). An application still waiting
    is censored at its age: it stays at risk until then and never counts as a non-response."""
    pts = sorted(obs)
    at_risk, s, out, i = len(pts), 1.0, [], 0
    while i < len(pts):
        t = pts[i][0]
        events = censored = 0
        while i < len(pts) and pts[i][0] == t:
            events += pts[i][1]
            censored += not pts[i][1]
            i += 1
        if events:
            s *= 1 - events / at_risk
            out.append((t, s))
        at_risk -= events + censored
    return out


def survival_at(curve: list[tuple[float, float]], t: float) -> float:
    s = 1.0
    for ti, si in curve:
        if ti > t:
            break
        s = si
    return s


def km_median(curve: list[tuple[float, float]]) -> float | None:
    """Days by which half have responded, or None when fewer than half ever do."""
    return next((t for t, s in curve if s <= 0.5), None)


def at_risk_at(obs: list[tuple[float, bool]], t: float) -> int:
    """Applications that can tell us about day `t`: responded by then, or old enough that they would have by now."""
    return sum(1 for d, responded in obs if d >= t or responded)


def observations(apps: list[AppEvents], sts: dict[int, Status]) -> list[tuple[float, bool]]:
    out = []
    for a in apps:
        st = sts[a.id]
        if st.label == "withdrawn":
            continue
        out.append((st.first_response_days, True) if st.first_response_days is not None else (st.age_days, False))
    return out


# --- rates ---------------------------------------------------------------------------------------------------------

def wilson(k: float, n: float, z: float = 1.96) -> tuple[float, float]:
    """The Wilson score interval of k out of n (0, 1 when n is 0)."""
    if n <= 0:
        return 0.0, 1.0
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


@dataclass
class Group:
    key: str
    n: int                  # applications (withdrawn left out)
    resolved: int           # positive + negative + ghosted
    positive: int
    negative: int
    ghosted: int
    pending: int
    response_rate: float | None      # (positive + negative) / resolved
    response_ci: tuple[float, float] | None
    positive_rate: float | None
    positive_ci: tuple[float, float] | None
    median_days: float | None        # KM median days to first response; None unless MIN_GROUP are at risk then
    by_30d: float | None             # share responded within 30 days (KM); None unless MIN_GROUP are at risk at day 30
    too_few: bool


def band(score: float | None) -> str:
    if score is None:
        return "no score"
    return "under 70" if score < 70 else "70 to 79" if score < 80 else "80 and over"


def group_by(apps: list[AppEvents], sts: dict[int, Status], key) -> list[Group]:
    groups: dict[str, list[AppEvents]] = {}
    for a in apps:
        if sts[a.id].label != "withdrawn":
            groups.setdefault(str(key(a) or "(none)"), []).append(a)
    out = []
    for k, members in sorted(groups.items()):
        c = {label: sum(1 for a in members if sts[a.id].label == label) for label in ("positive", "negative", "ghosted", "pending")}
        resolved = c["positive"] + c["negative"] + c["ghosted"]
        obs = observations(members, sts)
        curve = km_curve(obs)
        # a Kaplan-Meier figure from a handful of applications at risk is noise: show it only with MIN_GROUP of them
        median = km_median(curve)
        median = median if median is not None and at_risk_at(obs, median) >= MIN_GROUP else None
        by30 = (1 - survival_at(curve, 30)) if curve and at_risk_at(obs, 30) >= MIN_GROUP else None
        out.append(Group(
            k, len(members), resolved, c["positive"], c["negative"], c["ghosted"], c["pending"],
            (c["positive"] + c["negative"]) / resolved if resolved else None,
            wilson(c["positive"] + c["negative"], resolved) if resolved else None,
            c["positive"] / resolved if resolved else None, wilson(c["positive"], resolved) if resolved else None,
            median, by30, resolved < MIN_GROUP))
    return out


# --- from the database ---------------------------------------------------------------------------------------------

def _ts(text: str | None) -> datetime | None:
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC) if text else None
    except ValueError:
        return None


def _json(text, default):
    try:
        return json.loads(text) if text else default
    except ValueError:
        return default


def load(conn) -> list[AppEvents]:
    """Every submitted application with its approved events and the snapshot taken at submission."""
    out = []
    for a in conn.execute("SELECT * FROM applications WHERE submitted_at IS NOT NULL AND state IN ('submitted', 'confirmed') "
                          "ORDER BY id"):
        sub = _ts(a["submitted_at"])
        if sub is None:
            continue
        events = [(e["stage"], _ts(e["received_at"])) for e in conn.execute(
            "SELECT stage, received_at FROM outcome_events WHERE application_id = ? AND needs_review = 0", (a["id"],))]
        out.append(AppEvents(a["id"], sub, [(s, t) for s, t in events if t], a["portal"] or "", a["role_id"] or "",
                             (a["resume_sha"] or "")[:8], a["ats_average"], _json(a["ats_scores"], {}),
                             frozenset(_json(a["keywords"], []))))
    return out


def snapshot(conn, job_dir: str | Path) -> dict:
    """Freeze what the submitted resume was: its hash, the checker scores at the last round, the role family matched at
    discovery and the skills it listed. Taken at submission (and filled in later for older rows)."""
    from jobagent.config import get_settings

    key = str(job_dir)
    app = conn.execute("SELECT id, url FROM applications WHERE job_dir = ?", (key,)).fetchone()
    if not app:
        return {}
    keys = (key, str(Path("/app") / key) if not Path(key).is_absolute() else key)
    row = None
    for k in keys:
        row = conn.execute("SELECT resume_sha, MAX(round) AS r FROM ats_runs WHERE job_dir = ?", (k,)).fetchone()
        if row and row["resume_sha"]:
            break
    scores: dict[str, float] = {}
    if row and row["resume_sha"]:
        for r in conn.execute("SELECT site, score FROM ats_runs WHERE job_dir = ? AND round = ? AND score IS NOT NULL",
                              (k, row["r"])):
            scores[r["site"]] = r["score"]
    role = conn.execute("SELECT role_id FROM jobs WHERE url = ?", (app["url"],)).fetchone()
    keywords: list[str] = []
    for base in (Path("/app"), get_settings().outputs_dir.parent, Path.cwd()):
        p = (Path(key) if Path(key).is_absolute() else base / key) / "filled.json"
        if p.exists():
            try:
                keywords = skill_terms(json.loads(p.read_text()))
            except ValueError:
                pass
            break
    snap = {"resume_sha": (row["resume_sha"] if row and row["resume_sha"] else None),
            "ats_average": round(sum(scores.values()) / len(scores), 2) if scores else None,
            "ats_scores": json.dumps(scores) if scores else None, "role_id": role["role_id"] if role else None,
            "keywords": json.dumps(keywords) if keywords else None}
    with conn:
        conn.execute("UPDATE applications SET resume_sha = COALESCE(resume_sha, :resume_sha), ats_average = COALESCE("
                     "ats_average, :ats_average), ats_scores = COALESCE(ats_scores, :ats_scores), role_id = COALESCE("
                     "role_id, :role_id), keywords = COALESCE(keywords, :keywords) WHERE id = :id", {**snap, "id": app["id"]})
    return snap


def skill_terms(filled: dict) -> list[str]:
    """The skills the resume listed (lower-cased, unique): the terms and tools of every skills line."""
    out: list[str] = []
    for s in filled.get("skills") or []:
        for t in s.get("terms") or []:
            out.append(str(t))
        for tf in s.get("tools_for") or []:
            out += [str(x) for x in (tf.get("tools") or [])]
    return list(dict.fromkeys(x.strip().lower() for x in out if x and x.strip()))
