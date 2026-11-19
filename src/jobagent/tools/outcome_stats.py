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
    snapshot_status: str = ""                              # verified | reconstructed | mismatch | unknown ("" for rows before F06)
    company: str = ""                                      # applications to one company are not independent observations


@dataclass
class Status:
    """One application's label as of a date: pending, positive, negative, ghosted or withdrawn, with days to the first real reply, its age, the furthest stage reached and its weight as evidence."""
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
    """Label one application as of a date.

    Args:
        app: The application with its approved events.
        as_of: The date to judge at.
        cutoff_days: After this many silent days an unanswered application counts as ghosted.

    Returns:
        Its `Status`.
    """
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
    """The q-th percentile of `values` (q in 0..1), by linear interpolation; 0.0 for an empty list."""
    xs = sorted(values)
    if not xs:
        raise ValueError("no values")
    k = (len(xs) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


MIN_AT_RISK_FOR_CUTOFF = 15      # applications still at risk at the estimated deadline before it is believed


def visible(apps: list[AppEvents], as_of: datetime) -> list[AppEvents]:
    """The applications that existed at `as_of`: one submitted after it is not part of that evaluation (and has no age)."""
    return [a for a in apps if a.submitted_at <= as_of]


def _observations(group: list[AppEvents], as_of: datetime) -> list[tuple[float, bool]]:
    """(days, responded) for every visible, non-withdrawn application: the day of its first real response, or, if it has
    none yet, its age (it is CENSORED there: still waiting, not a non-response)."""
    out = []
    for a in visible(group, as_of):
        seen = AppEvents(a.id, a.submitted_at, [(s, t) for s, t in a.events if t <= as_of])
        if any(s == "withdrawn" for s, _ in seen.events):
            continue
        fr = first_response(seen)
        out.append((fr[0], True) if fr else (max(0.0, _days(a.submitted_at, as_of)), False))
    return out


def ghost_cutoff_info(apps: list[AppEvents], as_of: datetime, portal: str | None = None) -> tuple[float, str]:
    """(days, basis). The deadline after which silence counts as ghosting is a product heuristic unless it can be ESTIMATED:
    it is the day by which 90% of ALL applications (responders and the still-waiting, the latter censored) had responded -
    the Kaplan-Meier 90th percentile - and only when enough applications responded, the curve actually reaches 90%, and
    enough are still at risk there. Otherwise the conservative default is used and the basis says why. A percentile over
    the responders alone (a few fast replies among many silences) is NOT an unconditional deadline and is never used."""
    for label, group in ((f"portal {portal}", [a for a in apps if a.portal == portal] if portal else []), ("all applications", apps)):
        obs = _observations(group, as_of)
        if sum(r for _, r in obs) < MIN_RESPONSES_FOR_CUTOFF:
            continue
        t90 = next((tt for tt, s in km_curve(obs) if s <= 0.10), None)
        if t90 is not None and at_risk_at(obs, t90) >= MIN_AT_RISK_FOR_CUTOFF:
            days = min(max(t90, CUTOFF_CLAMP[0]), CUTOFF_CLAMP[1])
            return days, f"Kaplan-Meier: 90% of {label} had responded by day {t90:.0f}"
    return DEFAULT_CUTOFF_DAYS, ("default: too few responses, or the response curve does not reach 90% with enough applications "
                                 "still at risk, so no deadline can be estimated (uncertain)")


def ghost_cutoff(apps: list[AppEvents], as_of: datetime, portal: str | None = None) -> float:
    """The number of silent days after which an application counts as ghosted.

    Args:
        apps: All applications.
        as_of: The date to judge at.
        portal: Estimate for this portal only, when enough replies exist.

    Returns:
        The estimated or default cutoff in days (see `ghost_cutoff_info` for the basis).
    """
    return ghost_cutoff_info(apps, as_of, portal)[0]


def statuses(apps: list[AppEvents], as_of: datetime) -> dict[int, Status]:
    """Each application's status as of `as_of`. An application submitted after `as_of` did not exist then and is absent."""
    apps = visible(apps, as_of)
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
    """The Kaplan-Meier survival probability (still no reply) at day `t` of a curve."""
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
    """The (days, replied) observations for the applications in `sts`, with the still-waiting ones censored at their age."""
    out = []
    for a in apps:
        st = sts.get(a.id)
        if st is None or st.label == "withdrawn":
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
    """Outcome counts and rates for one group of applications (a portal, a role, a score band), with Wilson intervals and a `too_few` flag when the group is too small to trust."""
    key: str
    n: int                  # applications (withdrawn left out)
    resolved: int           # positive + negative + ghosted
    positive: int
    negative: int
    ghosted: int
    pending: int
    # NOTE on what these are. `response_rate` / `positive_rate` are SHARES OF THE RESOLVED APPLICATIONS (replied, or silent past
    # the cutoff): fast responders and older applications are over-represented there, so they are NOT the probability that an
    # application gets a callback. The probability at a fixed horizon is `by_30d` (Kaplan-Meier, censoring-aware).
    response_rate: float | None      # (positive + negative) / resolved: a share of the resolved cohort
    response_ci: tuple[float, float] | None
    positive_rate: float | None
    positive_ci: tuple[float, float] | None
    median_days: float | None        # KM median days to first response; None unless MIN_GROUP are at risk then
    by_30d: float | None             # P(first response within 30 days), Kaplan-Meier; None unless MIN_GROUP are at risk at day 30
    too_few: bool
    horizon_days: int = 30


def band(score: float | None) -> str:
    """The resume-score band an average falls in: `under 70`, `70 to 79`, `80 and over`, or `no score`."""
    if score is None:
        return "no score"
    return "under 70" if score < 70 else "70 to 79" if score < 80 else "80 and over"


def group_by(apps: list[AppEvents], sts: dict[int, Status], key) -> list[Group]:
    """Summarise applications by a key.

    Args:
        apps: The applications.
        sts: Each application's `Status`.
        key: A function from an application to its group name.

    Returns:
        One `Group` per name, withdrawn applications left out.
    """
    groups: dict[str, list[AppEvents]] = {}
    for a in apps:
        if a.id in sts and sts[a.id].label != "withdrawn":        # an application submitted after the evaluation date is absent
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
                             frozenset(_json(a["keywords"], [])), a["snapshot_status"] or "", a["company"] or ""))
    return out


def _job_folder(key: str) -> Path | None:
    from jobagent.config import get_settings
    bases = [Path("/app"), get_settings().outputs_dir.parent, Path.cwd()]
    for cand in ([Path(key)] if Path(key).is_absolute() else [base / key for base in bases]):
        if (cand / "resume.pdf").exists():
            return cand
    return None


def _jd_urls(folder: Path | None) -> list[str]:
    """The job description's own addresses (apply link and posting), which may differ from the application's recorded URL."""
    try:
        jd = json.loads((folder / "jd.json").read_text()) if folder else {}
    except (OSError, ValueError):
        return []
    return [u for u in (jd.get("apply_url"), jd.get("source_url")) if u]


def _marks(job_dir) -> tuple[str, tuple[str, ...]]:
    from jobagent import db
    al = db.job_key_aliases(job_dir)
    return ",".join("?" * len(al)), al


def _attempt_identity(conn, key: str) -> dict | None:
    """The submitted artifacts' identities as the frozen attempt recorded them (None for an application with no payload)."""
    row = conn.execute(f"SELECT payload_path FROM submission_attempts WHERE job_dir IN ({_marks(key)[0]}) AND payload_path IS NOT NULL "
                       "AND state IN ('submitted', 'confirmed', 'unknown') ORDER BY attempt_no DESC", _marks(key)[1]).fetchone()
    if row is None:
        return None
    try:
        return json.loads(Path(row["payload_path"]).read_text()).get("identity")
    except (OSError, ValueError):
        return None


def _persisted(conn, app_id: int) -> dict:
    r = conn.execute("SELECT resume_sha, resume_pdf_sha256, cover_letter_sha256, jd_fingerprint, ats_average, ats_scores, "
                     "ats_sites, role_id, keywords, snapshot_status, snapshot_detail FROM applications WHERE id = ?",
                     (app_id,)).fetchone()
    return dict(r)


def snapshot(conn, job_dir: str | Path) -> dict:
    """Freeze what was submitted, as ONE coherent unit: the identity of the submitted resume and cover letter, the job's
    fingerprint, the authoritative ATS result for exactly that version, the role family and the skills.

    The identities come from the frozen attempt (the bytes the browser attached to the resume and cover-letter controls,
    F04), never from whatever file the folder holds now; the disk hash is kept only as a diagnostic
    (`snapshot_detail`). `snapshot_status`: `verified` (identity from the attempt's own resume control), `unverifiable`
    (an attempt exists but the resume control's bytes could not be read, or were ambiguous: no hash, no scores, nothing
    attributed to a version), `reconstructed` (no attempt payload, older rows: the disk files are assumed), `unknown` (no
    PDF). A snapshot is never merged cell by cell: once it exists it is returned as persisted, and the only update is the
    completion of its OWN version's scores (same hash, same job fingerprint) as a whole."""
    from jobagent.tools import ats_result

    from jobagent import db

    key = db.job_key(job_dir)
    app = conn.execute(f"SELECT id, url, snapshot_status FROM applications WHERE job_dir IN ({_marks(job_dir)[0]}) ORDER BY (job_dir = ?) DESC",
                       (*_marks(job_dir)[1], key)).fetchone()
    if not app:
        return {}
    given = Path(str(job_dir))
    folder = given if (given / "resume.pdf").exists() else _job_folder(key)      # the folder as the caller named it first
    if app["snapshot_status"] is not None:                       # frozen: refuse to recompute it from the folder's current state
        have = _persisted(conn, app["id"])
        if have["ats_average"] is None and have["resume_sha"] and have["jd_fingerprint"]:
            g = ats_result.gather(folder or Path(key), conn=conn, sha=have["resume_sha"], fingerprint=have["jd_fingerprint"])
            if g["scores"] and (g["complete"] or json.dumps(g["scores"]) != have["ats_scores"]):
                with conn:                                       # the same version's own scores, replaced as a whole
                    conn.execute("UPDATE applications SET ats_scores = ?, ats_average = ?, ats_sites = ? WHERE id = ?",
                                 (json.dumps(g["scores"]), g["average"] if g["complete"] else None, json.dumps(g["sites"]), app["id"]))
                have = _persisted(conn, app["id"])
        return have
    disk_sha = ats_result.sha256_file(folder / "resume.pdf") if folder else None
    disk_cover = ats_result.sha256_file(folder / "cover_letter.pdf") if folder else None
    ident = _attempt_identity(conn, str(job_dir))
    if ident is not None:
        resume_sha, cover_sha = ident.get("resume_sha256"), ident.get("cover_letter_sha256")
        fingerprint = ident.get("job_fingerprint") or (ats_result.identity(folder)[1] if folder else None)
        status = "verified" if resume_sha else "unverifiable"
    else:
        resume_sha, cover_sha = disk_sha, disk_cover
        fingerprint = ats_result.identity(folder)[1] if folder else None
        status = "reconstructed" if resume_sha else "unknown"
    ats = ats_result.gather(folder or Path(key), conn=conn, sha=resume_sha, fingerprint=fingerprint) if resume_sha else None
    scores = ats["scores"] if ats else {}
    jd_urls = _jd_urls(folder)
    role = db.job_for_urls(conn, app["url"], *jd_urls)         # F25: the apply link or the posting's own address, aliases included
    keywords: list[str] = []
    p = folder / "filled.json" if folder else None
    disk_keywords: list[str] = []
    if p is not None and p.exists():
        try:
            disk_keywords = skill_terms(json.loads(p.read_text()))
        except ValueError:
            pass
    # the folder's skills describe the SUBMITTED resume only when that is established: its verified identity matches the
    # disk file, or (explicit legacy policy) there is no attempt payload at all. Otherwise they are kept apart, as a diagnostic.
    if status == "reconstructed" or (resume_sha is not None and disk_sha == resume_sha):
        keywords = disk_keywords
    snap = {"resume_sha": resume_sha, "resume_pdf_sha256": resume_sha, "cover_letter_sha256": cover_sha,
            "jd_fingerprint": fingerprint, "ats_average": ats["average"] if ats and ats["complete"] else None,
            "ats_scores": json.dumps(scores) if scores else None, "ats_sites": json.dumps(ats["sites"]) if ats else None,
            "role_id": role["role_id"] if role else None, "keywords": json.dumps(keywords) if keywords else None,
            "snapshot_status": status,
            "snapshot_detail": json.dumps({"disk_resume_sha256": disk_sha, "disk_cover_letter_sha256": disk_cover,
                                           "disk_matches_submitted": bool(resume_sha) and disk_sha == resume_sha,
                                           "disk_keywords_withheld": [] if keywords else disk_keywords})}
    with conn:
        conn.execute("UPDATE applications SET resume_sha = :resume_sha, resume_pdf_sha256 = :resume_pdf_sha256, "
                     "cover_letter_sha256 = :cover_letter_sha256, jd_fingerprint = :jd_fingerprint, ats_average = :ats_average, "
                     "ats_scores = :ats_scores, ats_sites = :ats_sites, role_id = :role_id, keywords = :keywords, "
                     "snapshot_status = :snapshot_status, snapshot_detail = :snapshot_detail WHERE id = :id",
                     {**snap, "id": app["id"]})
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
