"""What the dashboard shows, read from the agent's own files: the job database (opened read-only), `state/logs/*.jsonl`, `state/hitl`
and each application's result file. Every function returns plain JSON-able data and never raises for a missing file."""

import json
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

MAX_LOG_BYTES = 2_000_000
STEM = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")
STALLED_AFTER_S = 15 * 60                               # a run with no closing record and no new line for this long is "stalled" (killed?)
IDLE_AFTER_S = 120                                      # ... and "waiting" (a long model call) after this long


def ro_conn(settings) -> sqlite3.Connection | None:
    """The job database, read-only (the dashboard never writes it); None when there is none yet."""
    path = settings.state_dir / "jobs.sqlite"
    if not path.exists():
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _rows(conn, sql: str, args=()) -> list[dict]:
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    except sqlite3.Error:
        return []


def _count(conn, sql: str) -> dict:
    return {r[0] or "": r[1] for r in conn.execute(sql)} if conn else {}


def overview(settings) -> dict:
    conn = ro_conn(settings)
    out = {"now": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "jobs": {}, "applications": {}, "outcomes": {}, "waiting": 0, "ready": 0,
           "questions": len(hitl_pending(settings)), "runs_active": sum(1 for r in runs(settings, 40) if r["running"]),
           "jobs_active": sum(1 for j in pipeline(settings) if j["active"])}
    if conn is None:
        return out
    try:
        out["jobs"] = _count(conn, "SELECT status, COUNT(*) FROM jobs GROUP BY status")
        out["applications"] = _count(conn, "SELECT state, COUNT(*) FROM applications GROUP BY state")
        out["outcomes"] = _count(conn, "SELECT stage, COUNT(*) FROM outcome_events WHERE needs_review = 0 GROUP BY stage")
        rows = review(settings, conn)
        out["waiting"], out["ready"] = len(rows), sum(1 for r in rows if r["ready"])
    except sqlite3.Error:
        pass
    finally:
        conn.close()
    return out


def queue(settings, limit: int = 300) -> list[dict]:
    conn = ro_conn(settings)
    if conn is None:
        return []
    try:
        keep = ("id", "company", "title", "location", "status", "notes", "role_id", "match_score", "claimed_by", "updated_at", "url")
        return [{k: (str(r[k])[:200] if isinstance(r.get(k), str) else r.get(k)) for k in keep if k in r}
                for r in _rows(conn, "SELECT * FROM jobs ORDER BY updated_at DESC, id DESC LIMIT ?", (limit,))]
    finally:
        conn.close()


def applications(settings, limit: int = 300) -> list[dict]:
    conn = ro_conn(settings)
    if conn is None:
        return []
    try:
        rows = _rows(conn, "SELECT a.id, a.company, a.title, a.url, a.portal, a.state, a.filled_at, a.decided_at, a.submitted_at, "
                           "(SELECT stage FROM outcome_events o WHERE o.application_id = a.id AND o.needs_review = 0 "
                           " ORDER BY received_at DESC LIMIT 1) AS latest_stage "
                           "FROM applications a ORDER BY COALESCE(a.submitted_at, a.decided_at, a.filled_at) DESC, a.id DESC LIMIT ?", (limit,))
        return rows
    finally:
        conn.close()


def review(settings, conn=None) -> list[dict]:
    """Applications filled to their review screen and waiting for the candidate (tools/review_queue.py), with the command that opens each."""
    from jobagent.tools import review_queue
    own = conn is None
    conn = conn or ro_conn(settings)
    if conn is None:
        return []
    try:
        rows = review_queue.waiting(conn, settings.outputs_dir.parent)
        # A saved review belongs to the previous attempt while a rebuild/refill is
        # active. Keep it visible, but do not advertise the old packet as ready.
        root = settings.outputs_dir.parent
        def identity(value):
            p = Path(value)
            return str((p if p.is_absolute() else root / p).resolve())
        latest = {identity(j['job_dir']): j for j in pipeline(settings) if j['job_dir']}
        for row in rows:
            path = Path(identity(row['job_dir']))
            job = latest.get(str(path))
            if job is None:
                continue
            try:
                predates_run = (path / 'application/application.json').stat().st_mtime < datetime.fromisoformat(job['started'].replace('Z', '+00:00')).timestamp()
            except (OSError, ValueError, KeyError):
                predates_run = False
            if job['active'] or predates_run:
                row['ready'] = False
                row['blockers'] = [*row['blockers'], 'A newer run is still updating this application; wait for its review result.' if job['active'] else
                                   'This saved review predates the latest run; refill and review the current documents.']
        return rows
    except Exception:
        return []
    finally:
        if own:
            conn.close()


def hitl_pending(settings) -> list[dict]:
    """Questions the agent is waiting on. A secret is listed (so you know to answer it) but is answered only in a terminal."""
    from jobagent.tools.hitl import Request, _alive, process_namespace, submission_prompt
    from jobagent.log import host_command
    root = settings.state_dir / 'hitl'
    rows = []
    # A viewer may run in a different PID namespace or have read-only mounts.
    # Reading it must never prune an active worker's request or create folders.
    for p in (root / 'pending').glob('*.json'):
        try:
            r = Request(**json.loads(p.read_text()))
        except (OSError, ValueError, TypeError):
            continue
        if (root / 'answers' / p.name).exists():
            continue
        if r.pid_namespace and r.pid_namespace == process_namespace() and not _alive(r.pid):
            continue
        terminal = r.secret or r.terminal_only or submission_prompt(r.prompt)
        rows.append({'id': r.id, 'prompt': r.prompt, 'secret': bool(r.secret),
                     'terminal_only': terminal, 'context': r.context,
                     'default': None if r.secret else r.default, 'created': r.created,
                     'terminal_command': host_command(['jobagent', 'hitl', 'answer', r.id], interactive=r.secret)
                        + ('' if r.secret else " '<answer>'")})
    return sorted(rows, key=lambda r: r['created'])


def _parse_log(path: Path) -> list[dict]:
    try:
        if path.stat().st_size > MAX_LOG_BYTES:
            raw = path.read_bytes()[-MAX_LOG_BYTES:].split(b"\n", 1)[-1].decode("utf-8", "replace")
        else:
            raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out = []
    for line in raw.splitlines():
        try:
            rec = json.loads(line)
            if isinstance(rec, dict):
                out.append(rec)
        except json.JSONDecodeError:
            continue
    return out


def runs(settings, limit: int = 60) -> list[dict]:
    """Recent runs from the structured logs, newest first. `step` is the command that was run (older logs: the first line's tag), `state` is
    finished (closing record written), running, waiting (no line for 2 minutes: usually a long model call) or stalled (no line for 15 minutes
    and never closed: the run was most likely killed)."""
    d = settings.state_dir / "logs"
    if not d.is_dir():
        return []
    files = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    now = datetime.now().timestamp()
    out = []
    for p in files:
        recs = _parse_log(p)
        body = [r for r in recs if r.get("level") not in ("end", "progress")]
        if not body:
            continue
        levels = [r.get("level") for r in body]
        quiet_s = now - p.stat().st_mtime
        state = ("finished" if any(r.get("level") == "end" for r in recs) else
                 "running" if quiet_s < IDLE_AFTER_S else "waiting" if quiet_s < STALLED_AFTER_S else "stalled")
        final = next((r for r in reversed(recs) if r.get('level') == 'progress' and r.get('status') in ('done', 'failed', 'blocked')), None)
        if final and state == 'finished':
            state = 'finished' if final['status'] == 'done' else final['status']
        if any(r.get('level') == 'end' and r.get('exit_code') not in (None, 0) for r in recs):
            state = 'failed'
        out.append({"id": p.stem, "run": body[0].get("run", ""), "step": body[0].get("cmd") or body[0].get("step", ""), "job": body[0].get("job", ""),
                    "started": body[0].get("ts", ""), "last": body[-1].get("ts", ""), "lines": len(body), "errors": levels.count("error"),
                    "warnings": levels.count("warn"), "message": str(body[-1].get("msg", ""))[:160], "state": state,
                    "running": state in ("running", "waiting"), "quiet_s": int(quiet_s)})
    return out


STAGES = [("scrape", "Read posting"), ("research", "Research company"), ("documents", "Resume & cover letter"), ("apply", "Fill the form"),
          ("submit", "Submit")]
DONE_STATES = ("done", "skipped")


def _log_state(path: Path, recs: list[dict], now: float) -> str:
    quiet_s = now - path.stat().st_mtime
    return ("finished" if any(r.get("level") == "end" for r in recs) else
            "running" if quiet_s < IDLE_AFTER_S else "waiting" if quiet_s < STALLED_AFTER_S else "stalled")


def pipeline(settings, hours: float = 72, limit: int = 30) -> list[dict]:
    """One entry per job the agent has worked on recently: which stages its plan has, where each stands, which step inside the current stage
    (the iteration, e.g. "ATS round 2 of 3"), how far along it is overall, and a short trail of what happened. Built from the `progress` records
    in the run logs, so every command that touches a job (run, tailor, apply, ...) contributes to the same entry."""
    d = settings.state_dir / "logs"
    if not d.is_dir():
        return []
    now = datetime.now().timestamp()
    files = [p for p in sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:150] if now - p.stat().st_mtime < hours * 3600]
    parsed = [(p, _parse_log(p)) for p in reversed(files)]
    conn = ro_conn(settings)
    try:
        receipts = _rows(conn, "SELECT a.job_dir,a.company,a.title,a.state,a.submitted_at,a.confirmed_at,"
                              "COALESCE(j.url,a.url) AS url FROM applications a LEFT JOIN jobs j ON j.id=a.job_id "
                              "WHERE a.state IN ('submitted','confirmed') AND a.submitted_at IS NOT NULL") if conn else []
    finally:
        if conn:
            conn.close()
    def directory(value):
        path = Path(value)
        return str((path if path.is_absolute() else settings.outputs_dir.parent / path).resolve())
    url_job: dict[str, str] = {}                         # a record that names both its url and its job links the two names of one job
    for _, recs in parsed:
        for r in recs:
            if r.get("level") == "progress" and r.get("url") and r.get("job"):
                if r.get('job_dir'):
                    url_job[r['url']] = directory(r['job_dir'])
                else:
                    url_job.setdefault(r['url'], f"{r['company']}:{r['job']}" if r.get('company') else f"url:{r['url']}")
    for receipt in receipts:
        url_job[receipt['url']] = directory(receipt['job_dir'])
    identities, descriptions = {}, {}
    for _, recs in parsed:
        for r in recs:
            if r.get('level') != 'progress' or not r.get('job'):
                continue
            identity = url_job.get(r.get('url', '')) or (directory(r['job_dir']) if r.get('job_dir') else None)
            if identity:
                identities.setdefault(r['job'], set()).add(identity)
                descriptions.setdefault(identity, {}).update({k:r[k] for k in ('title','company','job_dir','url') if r.get(k)})
    for receipt in receipts:
        identities.setdefault(Path(receipt['job_dir']).name, set()).add(directory(receipt['job_dir']))
    jobs: dict[str, dict] = {}
    for p, recs in parsed:
        state = _log_state(p, recs, now)
        for r in recs:
            if r.get("level") != "progress" or not r.get("stage"):
                continue
            known = identities.get(r.get('job'), set())
            identity = url_job.get(r.get('url', '')) or (directory(r['job_dir']) if r.get('job_dir') else None) or (
                next(iter(known)) if len(known) == 1 and not r.get('company') else None) or (
                f"{r.get('company')}:{r['job']}" if r.get('company') and r.get('job') else r.get('job')) or f"url:{r.get('url', '')}"
            key = (identity, r.get('execution_id', 'legacy'))
            j = jobs.setdefault(key, {"job": r.get("job", ""), "url": "", "title": "", "company": "", "plan": [], "stages": {}, "trail": [],
                                      "first": r["ts"], "last": r["ts"], "active": False,
                                      'identity': identity, 'execution_id': r.get('execution_id', ''), 'job_dir': r.get('job_dir', '')})
            for k in ("job", "url", "title", "company", 'browser_mode', 'job_dir'):
                if r.get(k):
                    j[k] = r[k]
            if r.get("plan"):
                j["plan"] = r["plan"]
            j["active"] = j["active"] or state in ("running", "waiting")
            j["first"], j["last"] = min(j["first"], r["ts"]), max(j["last"], r["ts"])
            j["trail"].append({"ts": r["ts"], "stage": r["stage"], "status": r.get("status", ""), "label": r.get("label", ""), "i": r.get("i"),
                               "n": r.get("n"), "detail": r.get("detail", ""), 'time_ns': r.get('time_ns', 0)})
    labels = dict(STAGES)
    out = []
    pending = hitl_pending(settings)
    for j in jobs.values():
        for key, value in descriptions.get(j['identity'], {}).items():
            if not j.get(key):
                j[key] = value
        j["trail"].sort(key=lambda e: (e["ts"], e['time_ns']))
        for e in j["trail"]:
            j["stages"][e["stage"]] = e                  # the latest record of each stage wins
        plan = [s for s in (j["plan"] or [k for k, _ in STAGES if k in j["stages"]]) if s in labels]
        for s in list(j["stages"]):
            if s not in plan and s in labels:
                plan.append(s)
        plan.sort(key=lambda s: [k for k, _ in STAGES].index(s))
        stages = []
        questions = [q['id'] for q in pending if isinstance(q.get('context'), dict) and j['execution_id'] and
                     q['context'].get('execution_id') == j['execution_id']]
        for s in plan:
            e = j["stages"].get(s)
            st = e["status"] if e else "pending"
            if st == "waiting" and questions:
                j['active'] = True
            if st in ('running', 'waiting') and not j["active"]:
                st = "stalled"                           # nothing is writing any more and it never said it finished
            stages.append({"id": s, "label": labels[s], "status": st, "step": (e or {}).get("label", ""), "i": (e or {}).get("i"),
                           "n": (e or {}).get("n"), "detail": (e or {}).get("detail", ""), "ts": (e or {}).get("ts", "")})
        done = sum(1 for s in stages if s["status"] in DONE_STATES)
        cur = next((s for s in stages if s["status"] in ("running", "waiting", "stalled", "failed", "blocked")), None)
        frac = 0.0
        if cur and cur["status"] == "running":
            frac = min(cur["i"] / cur["n"], 0.95) if cur.get("i") and cur.get("n") else 0.4
        bad = next((s for s in stages if s["status"] in ("failed", "blocked", "stalled")), None)
        overall = ("failed" if bad and bad["status"] == "failed" else "blocked" if bad and bad["status"] == "blocked" else "stalled" if bad else
                   "done" if stages and done == len(stages) else 'waiting' if cur and cur['status'] == 'waiting' else "running" if j["active"] else "waiting")
        j['active'] = bool(j['active'] and ((cur and cur['status'] in ('running', 'waiting')) or
                          (cur is None and any(s['status'] == 'pending' for s in stages))))
        started = datetime.fromisoformat(j["first"].replace("Z", "+00:00")).timestamp()
        ended = now if j["active"] else datetime.fromisoformat(j["last"].replace("Z", "+00:00")).timestamp()
        out.append({"job": j["job"], "url": j["url"], "title": j["title"] or j["job"] or j["url"], "company": j["company"], "stages": stages,
                    "current": cur["id"] if cur else None, "percent": round(100 * (done + frac) / len(stages)) if stages else 0, "state": overall,
                    "active": j["active"], "started": j["first"], "updated": j["last"], "elapsed_s": int(max(0, ended - started)),
                    "trail": j["trail"][-14:], 'identity': j['identity'], 'execution_id': j['execution_id'],
                    'job_dir': j['job_dir'], 'question_ids': questions, 'browser_mode': j.get('browser_mode', ''),
                    '_last_ns': j['trail'][-1]['time_ns']})
    out.sort(key=lambda x: (x["updated"], x['_last_ns']), reverse=True)
    # A new attempt must not inherit a prior attempt's failures or completion.
    latest = {}
    for item in out:
        item.pop('_last_ns', None)
        latest.setdefault(item['identity'], item)
    out = list(latest.values())
    # Logs describe attempts. A durable sent receipt determines the current
    # job's submission state, including commands predating lifecycle events.
    sent = {directory(r['job_dir']):r for r in receipts}
    for item in out:
        receipt = sent.get(item['identity'])
        if not receipt:
            continue
        stamp = receipt['confirmed_at'] or receipt['submitted_at']
        label = 'Email confirmed' if receipt['state'] == 'confirmed' else 'Portal received the application'
        item.update(state='done', active=False, current=None, percent=100,
                    company=receipt['company'], title=receipt['title'], url=receipt['url'],
                    job_dir=directory(receipt['job_dir']), submission_state=receipt['state'],
                    updated=max(item['updated'],stamp), question_ids=[],
                    stages=[{'id':'submit','label':'Submit','status':'done','step':label,
                             'i':None,'n':None,'detail':label,'ts':stamp}])
    out.sort(key=lambda x:x['updated'],reverse=True)
    out.sort(key=lambda x: not x["active"])              # active jobs first, each group newest first
    return out[:limit]


def run_log(settings, stem: str, tail: int = 300) -> list[dict] | None:
    """The last `tail` records of one run's log; None for a name that is not a log file here (no path can escape the logs folder)."""
    if not STEM.match(stem or ""):
        return None
    p = settings.state_dir / "logs" / f"{stem}.jsonl"
    if not p.is_file():
        return None
    return [{"ts": r.get("ts", ""), "level": r.get("level", ""), "step": r.get("step", ""), "msg": str(r.get("msg", ""))[:2000]}
            for r in _parse_log(p)[-tail:]]


def outcomes(settings, limit: int = 40) -> dict:
    conn = ro_conn(settings)
    if conn is None:
        return {"counts": {}, "recent": []}
    try:
        return {"counts": _count(conn, "SELECT stage, COUNT(*) FROM outcome_events WHERE needs_review = 0 GROUP BY stage"),
                "recent": _rows(conn, "SELECT o.stage, o.received_at, o.subject, a.company, a.title FROM outcome_events o "
                                      "JOIN applications a ON a.id = o.application_id WHERE o.needs_review = 0 "
                                      "ORDER BY o.received_at DESC LIMIT ?", (limit,))}
    finally:
        conn.close()


# --- analytics ---------------------------------------------------------------------------------------------------------------------

def _day(text: str | None) -> str:
    return (text or "")[:10]


def _median(values: list[float]) -> float | None:
    v = sorted(values)
    return None if not v else (v[len(v) // 2] if len(v) % 2 else (v[len(v) // 2 - 1] + v[len(v) // 2]) / 2)


def _hours(a: str | None, b: str | None) -> float | None:
    try:
        d = datetime.strptime(b, "%Y-%m-%dT%H:%M:%SZ") - datetime.strptime(a, "%Y-%m-%dT%H:%M:%SZ")
        return d.total_seconds() / 3600 if d.total_seconds() >= 0 else None
    except (TypeError, ValueError):
        return None


def _skip_kind(note: str | None) -> str:
    n = (note or "").lower()
    if n.startswith("location:"):
        return "Outside the United States"
    if n.startswith("role: no target role"):
        return "No target role fits the title"
    if n.startswith("role: the title matches an e"):
        return "Title excluded (seniority and similar)"
    return "Other"


def _group_rows(groups) -> list[dict]:
    return [{"key": g.key, "n": g.n, "resolved": g.resolved, "positive": g.positive, "negative": g.negative, "ghosted": g.ghosted, "pending": g.pending,
             "response_rate": g.response_rate, "response_ci": g.response_ci, "positive_rate": g.positive_rate, "positive_ci": g.positive_ci,
             "median_days": g.median_days, "by_30d": g.by_30d, "too_few": g.too_few} for g in groups]


def analytics(settings, days: int = 45) -> dict:
    """The numbers behind the Analytics tab: how many postings were found, filtered out and worked, how many applications went where, what came
    back (outcome labels that count silence as unknown, not as rejection), how long each step takes, and how the resumes scored. Rates are
    reported with their sample size and a confidence interval, and a group with too few resolved applications is flagged, never ranked."""
    from jobagent.tools import outcome_stats as OS
    empty = {"generated": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "funnel": [], "skips": [], "skip_places": [], "jobs_by_status": {}, "jobs_by_company": [],
             "jobs_by_role": [], "score_hist": [], "timeline": [], "outcomes": {"counts": {}, "labels": {}, "resolved": 0, "responded": 0, "rate": None,
             "median_days": None, "cutoff_days": None, "cutoff_basis": "", "min_group": OS.MIN_GROUP, "by_portal": [], "by_role": [], "by_ats": []},
             "ats": {"hist": [], "by_site": [], "jobs": 0}, "latency": {"discovery_to_fill_h": None, "fill_to_submit_h": None, "n_fill": 0, "n_submit": 0},
             "totals": {"jobs": 0, "applications": 0, "submitted": 0}}
    conn = ro_conn(settings)
    if conn is None:
        return empty
    out = dict(empty)
    try:
        jobs = _rows(conn, "SELECT id, company, status, notes, role_id, match_score, discovered_at FROM jobs")
        apps = _rows(conn, "SELECT id, job_id, state, portal, filled_at, submitted_at, decided_at FROM applications")
        events = _rows(conn, "SELECT application_id, stage, received_at FROM outcome_events WHERE needs_review = 0")
        total = len(jobs)
        skipped = [j for j in jobs if j["status"] == "skipped"]
        kept = [j for j in jobs if j["status"] != "skipped"]
        filled_states = ("filled", "approved", "submission_in_progress", "submission_unknown", "submitted", "confirmed")
        reached = [a for a in apps if a["state"] in filled_states]
        submitted = [a for a in apps if a["submitted_at"]]
        by_app: dict[int, set[str]] = {}
        for e in events:
            by_app.setdefault(e["application_id"], set()).add(e["stage"])
        confirmed = [a for a in submitted if a["state"] == "confirmed" or "confirmation" in by_app.get(a["id"], set())]
        responded = [a for a in submitted if by_app.get(a["id"], set()) & OS.RESPONSES]
        further = [a for a in submitted if by_app.get(a["id"], set()) & {"interview", "offer"}]
        out["funnel"] = [
            {"id": "discovered", "label": "Postings found", "n": total},
            {"id": "passed", "label": "Postings not filtered out", "n": len(kept)},
            {"id": "reached", "label": "Application filled to review", "n": len(reached)},
            {"id": "submitted", "label": "Submitted", "n": len(submitted)},
            {"id": "confirmed", "label": "Receipt confirmed", "n": len(confirmed)},
            {"id": "responded", "label": "Got a response", "n": len(responded)},
            {"id": "interview", "label": "Interview or offer", "n": len(further)}]
        kinds: dict[str, int] = {}
        places: dict[str, int] = {}
        for j in skipped:
            kinds[_skip_kind(j["notes"])] = kinds.get(_skip_kind(j["notes"]), 0) + 1
            m = re.match(r"(?i)location:\s*(.+?) is outside", j["notes"] or "")
            if m:
                places[m.group(1)] = places.get(m.group(1), 0) + 1
        out["skips"] = sorted(({"reason": k, "n": v} for k, v in kinds.items()), key=lambda x: -x["n"])
        out["skip_places"] = sorted(({"place": k, "n": v} for k, v in places.items()), key=lambda x: -x["n"])[:8]
        st: dict[str, int] = {}
        for j in jobs:
            st[j["status"]] = st.get(j["status"], 0) + 1
        out["jobs_by_status"] = st
        comp: dict[str, dict] = {}
        for j in jobs:
            c = comp.setdefault(j["company"] or "(unknown)", {"company": j["company"] or "(unknown)", "found": 0, "kept": 0})
            c["found"] += 1
            c["kept"] += j["status"] != "skipped"
        out["jobs_by_company"] = sorted(comp.values(), key=lambda c: -c["found"])[:10]
        roles: dict[str, int] = {}
        for j in kept:
            roles[j["role_id"] or "(none)"] = roles.get(j["role_id"] or "(none)", 0) + 1
        out["jobs_by_role"] = sorted(({"role": k, "n": v} for k, v in roles.items()), key=lambda x: -x["n"])
        bins = [0] * 6                                   # match score 0.5-1.0 in steps of 0.1 (the last bin is 1.0 only)
        for j in kept:
            if j["match_score"] is not None and 0.5 <= j["match_score"] <= 1.0:
                bins[min(5, int(Decimal(str(j['match_score'])) * 10) - 5)] += 1
        out["score_hist"] = [{"label": f"{0.5 + i / 10:.1f}" + ("" if i == 5 else f"–{0.6 + i / 10:.1f}"), "n": n} for i, n in enumerate(bins)]
        # activity per day over the last `days`
        today = datetime.now(UTC).date()
        window = [(today - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
        line = {d: {"day": d, "found": 0, "filled": 0, "submitted": 0, "responses": 0} for d in window}
        for j in jobs:
            if _day(j["discovered_at"]) in line:
                line[_day(j["discovered_at"])]["found"] += 1
        for a in apps:
            for key, col in (("filled", "filled_at"), ("submitted", "submitted_at")):
                if _day(a[col]) in line:
                    line[_day(a[col])][key] += 1
        for e in events:
            if _day(e["received_at"]) in line and e["stage"] in OS.RESPONSES:
                line[_day(e["received_at"])]["responses"] += 1
        out["timeline"] = list(line.values())
        # how long the human-in-the-loop steps take
        job_found = {j["id"]: j["discovered_at"] for j in jobs}
        d2f = [h for a in reached if (h := _hours(job_found.get(a["job_id"]), a["filled_at"])) is not None]
        f2s = [h for a in submitted if (h := _hours(a["filled_at"], a["submitted_at"])) is not None]
        out["latency"] = {"discovery_to_fill_h": _median(d2f), "fill_to_submit_h": _median(f2s), "n_fill": len(d2f), "n_submit": len(f2s)}
        # resume scores from the checker sites
        from jobagent.tools import ats_result
        runs = _rows(conn, "SELECT DISTINCT job_dir FROM ats_runs WHERE score IS NOT NULL")
        per_job: dict[str, float] = {}
        per_site: dict[str, list[float]] = {}
        seen = set()
        incomplete = 0
        for row in runs:
            d = Path(row['job_dir'])
            if not d.is_absolute():
                d = settings.outputs_dir.parent / d
            d = d.resolve()
            if d in seen:
                continue
            seen.add(d)
            if not d.is_relative_to(settings.outputs_dir.resolve()):
                incomplete += 1
                continue
            summary = ats_result.gather(d, conn=conn)
            for site, score in summary['scores'].items():
                per_site.setdefault(site, []).append(score)
            if summary['complete'] and summary['average'] is not None:
                per_job[str(d)] = summary['average']
            else:
                incomplete += 1
        hist = [0] * 6                                    # <50, 50-60, ... 90-100
        for m in per_job.values():
            hist[0 if m < 50 else min(5, int((m - 40) // 10))] += 1
        out["ats"] = {"hist": [{"label": l, "n": n} for l, n in zip(["under 50", "50–60", "60–70", "70–80", "80–90", "90–100"], hist, strict=True)],
                      "by_site": sorted(({"site": s, "mean": round(sum(v) / len(v), 1), "n": len(v)} for s, v in per_site.items()), key=lambda x: -x["mean"]),
                      "jobs": len(per_job), 'incomplete': incomplete}
        # outcomes, with the agent's own delay-aware labels (silence is pending/ghosted, never silently a rejection)
        evs = OS.load(conn)
        as_of = datetime.now(UTC)
        sts = OS.statuses(evs, as_of)
        labels: dict[str, int] = {}
        for s in sts.values():
            labels[s.label] = labels.get(s.label, 0) + 1
        resolved = labels.get("positive", 0) + labels.get("negative", 0) + labels.get("ghosted", 0)
        replied = labels.get("positive", 0) + labels.get("negative", 0)
        cutoff, basis = OS.ghost_cutoff_info(evs, as_of)
        curve = OS.km_curve(OS.observations(evs, sts)) if evs else []
        med = OS.km_median(curve) if curve else None
        out["outcomes"] = {
            "counts": {k: sum(1 for e in events if e["stage"] == k) for k in sorted({e["stage"] for e in events})}, "labels": labels,
            "resolved": resolved, "responded": replied, "rate": ({"k": replied, "n": resolved, "lo": OS.wilson(replied, resolved)[0], "hi": OS.wilson(replied, resolved)[1]} if resolved else None),
            "median_days": med, "cutoff_days": cutoff, "cutoff_basis": basis, "min_group": OS.MIN_GROUP,
            "by_portal": _group_rows(OS.group_by(evs, sts, lambda a: a.portal)), "by_role": _group_rows(OS.group_by(evs, sts, lambda a: a.role_id)),
            "by_ats": _group_rows(OS.group_by(evs, sts, lambda a: OS.band(a.ats_average)))}
        out["totals"] = {"jobs": total, "applications": len(reached), "submitted": len(submitted)}
    except sqlite3.Error:
        return empty
    finally:
        conn.close()
    return out
