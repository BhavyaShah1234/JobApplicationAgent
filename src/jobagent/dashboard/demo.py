"""Made-up data for developing and testing the dashboard without touching a real state folder: `seed(root)` fills `root/state`, `root/outputs`
and `root/data` with invented companies, a queue, two applications at review, a question, runs and outcomes."""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path


def _ts(minutes_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def seed(root: Path, secret_question: bool = True, history: bool = False, structured: bool = False) -> dict:
    """Create the demo data under `root` and return the settings-style paths. Safe to call twice (existing rows are kept)."""
    from jobagent import db
    state, outputs, data = root / "state", root / "outputs", root / "data"
    for d in (state, outputs, data, state / "logs", state / "hitl" / "pending", state / "hitl" / "answers"):
        d.mkdir(parents=True, exist_ok=True)
    conn = db.connect(state / "jobs.sqlite")
    jobs = [("Example Robotics", "Robotics Engineer", "queued", "robotics", 0.91), ("Sample Cloud Co", "Backend Engineer", "queued", "swe", 0.84),
            ("Demo Labs", "ML Engineer", "needs_review", "ml", 0.62), ("Fictional Systems", "Software Engineer", "failed", "swe", 0.8),
            ("Example Robotics", "Perception Engineer", "closed", "robotics", 0.7)]
    with conn:
        for i, (co, title, status, role, score) in enumerate(jobs, 1):
            conn.execute("INSERT OR IGNORE INTO jobs (url, job_id, company, title, status, role_id, match_score, notes, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                         (f"https://jobs.example.test/{i}", str(i), co, title, status, role, score, "posting closed" if status == "closed" else "", _ts(i * 7)))
        for i, (co, title, state_, filled) in enumerate([("Example Robotics", "Robotics Engineer", "filled", 50), ("Sample Cloud Co", "Backend Engineer", "filled", 190),
                                                          ("Old Demo Inc", "Data Engineer", "confirmed", 4000)], 1):
            d = outputs / co.lower().replace(" ", "-") / str(100 + i)
            (d / "application").mkdir(parents=True, exist_ok=True)
            ready = i == 1
            (d / "application" / "application.json").write_text(json.dumps(
                {"ready_to_submit": ready, "blockers": [] if ready else ["Work authorization: the answer needs your confirmation"]}))
            conn.execute("INSERT OR IGNORE INTO applications (job_dir, company, title, url, portal, state, filled_at, submitted_at) VALUES (?,?,?,?,?,?,?,?)",
                         (str(d.relative_to(root)), co, title, f"https://jobs.example.test/{i}", "greenhouse", state_, _ts(filled),
                          _ts(filled) if state_ == "confirmed" else None))
        conn.execute("INSERT OR IGNORE INTO watchlist (company, company_key, max_jobs) VALUES ('Example Robotics', 'example-robotics', 3)")
        conn.execute("INSERT OR IGNORE INTO watchlist (company, company_key, max_jobs, enabled) VALUES ('Sample Cloud Co', 'sample-cloud', 5, 0)")
        app3 = conn.execute("SELECT id FROM applications WHERE state = 'confirmed'").fetchone()[0]
        conn.execute("INSERT OR IGNORE INTO outcome_events (application_id, stage, message_id, subject, received_at, classifier, confidence) VALUES (?,?,?,?,?,?,?)",
                     (app3, "confirmation", "<demo-1@example.test>", "We received your application", _ts(3900), "rule", 0.9))
        conn.execute("INSERT OR IGNORE INTO outcome_events (application_id, stage, message_id, subject, received_at, classifier, confidence) VALUES (?,?,?,?,?,?,?)",
                     (app3, "interview", "<demo-2@example.test>", "Let's schedule an interview", _ts(600), "model", 0.8))
    conn.close()
    pid = os.getpid()                                  # a question counts as pending only while its asking process is alive
    for rid, prompt, secret in (("dem0a1b2", "Are you willing to relocate to Boston?", False), ("dem0c3d4", "Portal password for Example Robotics", True)):
        if secret and not secret_question:
            continue
        (state / "hitl" / "pending" / f"{rid}.json").write_text(json.dumps(
            {"id": rid, "prompt": prompt, "secret": secret, "default": None, "vault_target": ["portal", "example"] if secret else None,
             "created": _ts(5), "pid": pid}))
    if structured:
        def q(id_, label, kind, options=(), **kw):
            """One demo question in the shape `tools.hitl.question_spec` produces."""
            return {"id": id_, "label": label, "kind": kind, "options": list(options), "required": kw.pop("required", True), "section": kw.pop("section", ""),
                    "help": kw.pop("help", ""), "multiple": kind == "checkboxes", "options_complete": True, "current": "", "quick": kw.pop("quick", [])}
        ctx = {"company": "Example Robotics", "title": "Robotics Engineer", "stage": "apply"}
        for rid, prompt, qs, fmt in (
                ("dem1sel1", 'Example Robotics application asks: "Country of residence"', [q("f1", "Country of residence", "select", ["United States", "Canada", "United Kingdom", "India", "Germany"], quick=["skip"])], "text"),
                ("dem1rad1", 'Example Robotics application asks: "Are you legally authorized to work in the US?"', [q("f2", "Are you legally authorized to work in the US?", "radio", ["Yes", "No"])], "text"),
                ("dem1bat1", "Example Robotics application needs these answers.", [
                    q("f3", "How did you hear about us?", "checkboxes", ["LinkedIn", "Referral", "Company website", "Job board"], section="About you"),
                    q("f4", "Earliest start date", "text", help="Month and year, as the form asks"),
                    q("f5", "Salary expectation (USD)", "number", required=False),
                    q("f6", "Preferred office", "select", ["Boston", "Pittsburgh", "Remote"])], "json")):
            (state / "hitl" / "pending" / f"{rid}.json").write_text(json.dumps(
                {"id": rid, "prompt": prompt, "secret": False, "default": None, "created": _ts(3), "pid": pid, "context": ctx, "questions": qs, "reply_format": fmt}))
    for stem, step, job, lines, ago in (("2026-11-16T101500-apply-example-robotics-aaaa1111", "apply", "example-robotics", [("info", "apply: page 1"), ("ok", "apply: reached the review screen")], 60),
                                         ("2026-11-16T103000-tailor-sample-cloud-bbbb2222", "tailor", "sample-cloud", [("info", "tailor: writing"), ("warn", "tailor: retrying with json mode"), ("error", "tailor: RuntimeError: model down")], 30),
                                         ("2026-11-16T104500-run-cccc3333", "run", "", [("info", "queue: Example Robotics - Robotics Engineer")], 0.2)):
        p = state / "logs" / f"{stem}.jsonl"
        recs = [(lv, m) for lv, m in lines] + ([("end", "finished")] if ago > 1 else [])          # the newest run has not finished
        p.write_text("".join(json.dumps({"ts": _ts(ago), "run": stem[-8:], "cmd": step, "job": job, "level": lv, "step": step, "msg": m}) + "\n"
                             for lv, m in recs))
        t = datetime.now().timestamp() - ago * 60
        os.utime(p, (t, t))
    _progress_logs(state)
    if history:
        _history(state / "jobs.sqlite")
    return {"state_dir": state, "outputs_dir": outputs, "data_dir": data}


def _history(db_path: Path) -> None:
    """Weeks of made-up activity for the Analytics tab: postings found and filtered, 24 submitted applications on three portals, their outcomes
    (confirmations, replies, rejections, silence) and checker-site scores."""
    import random

    from jobagent import db
    rnd = random.Random(7)
    conn = db.connect(db_path)
    reasons = ["location: London is outside the United States", "location: Toronto is outside the United States", "role: no target role fits the title",
               "role: the title matches an exclude pattern", "location: Dublin is outside the United States"]
    with conn:
        for i in range(160):
            status = rnd.choices(["skipped", "queued", "needs_review", "scraped", "closed"], [60, 15, 8, 12, 5])[0]
            conn.execute("INSERT OR IGNORE INTO jobs (url, job_id, company, title, status, notes, role_id, match_score, discovered_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (f"https://jobs.example.test/h{i}", f"h{i}", rnd.choice(["Example Robotics", "Sample Cloud Co", "Demo Labs", "Fictional Systems", "Acme Corp", "Globex"]),
                          f"Engineer {i}", status, rnd.choice(reasons) if status == "skipped" else "", None if status == "skipped" else rnd.choice(["swe", "ml", "robotics"]),
                          None if status == "skipped" else round(rnd.uniform(0.55, 0.99), 2), _ts(rnd.uniform(60, 40 * 24 * 60)), _ts(rnd.uniform(60, 40 * 24 * 60))))
        portals = ["greenhouse", "lever", "workday"]
        for i in range(24):
            days = rnd.uniform(1, 30)
            ats = round(rnd.gauss(80, 7), 1)
            cur = conn.execute("INSERT OR IGNORE INTO applications (job_dir, company, title, url, portal, state, filled_at, submitted_at, role_id, ats_average) VALUES (?,?,?,?,?,?,?,?,?,?)",
                               (f"outputs/hist/{i}", f"History Co {i % 8}", f"Engineer {i}", f"https://jobs.example.test/a{i}", rnd.choice(portals), "confirmed",
                                _ts(days * 1440 + 90), _ts(days * 1440), rnd.choice(["swe", "ml"]), ats))
            if not cur.rowcount:
                continue
            app = cur.lastrowid
            conn.execute("INSERT INTO outcome_events (application_id, stage, message_id, subject, received_at, classifier, confidence) VALUES (?,?,?,?,?,?,?)",
                         (app, "confirmation", f"<h{i}-c@example.test>", "We received your application", _ts(days * 1440 - 5), "rule", 0.9))
            roll = rnd.random() + (0.12 if ats >= 80 else 0)
            reply_in = rnd.uniform(2, 12) * 1440
            if days * 1440 > reply_in + 1440 and roll > 0.62:
                stage = rnd.choice(["interview", "recruiter_screen", "assessment"]) if roll > 0.8 else "rejection"
                conn.execute("INSERT INTO outcome_events (application_id, stage, message_id, subject, received_at, classifier, confidence) VALUES (?,?,?,?,?,?,?)",
                             (app, stage, f"<h{i}-r@example.test>", f"Update on your application ({stage})", _ts(days * 1440 - reply_in), "model", 0.85))
            for site in ("autoapplymax", "jobalytics", "hireflow"):
                conn.execute("INSERT INTO ats_runs (job_dir, site, round, score) VALUES (?,?,?,?)", (f"outputs/hist/{i}", site, 2, round(ats + rnd.uniform(-8, 8), 1)))
    conn.close()


def _progress_logs(state: Path) -> None:
    """Five jobs at different points: one in the ATS loop, one filling a form, one finished, one that failed, one stopped by the eligibility screen."""
    plan = ["scrape", "research", "documents", "apply"]

    def write(name: str, cmd: str, job: str, recs: list[dict], quiet_s: float, closed: bool) -> None:
        p = state / "logs" / f"{name}.jsonl"
        rows = list(recs) + ([{"level": "end", "step": cmd, "msg": "finished", "ago": 0}] if closed else [])
        p.write_text("".join(json.dumps({"ts": _ts(r.pop("ago")), "run": name[-8:], "cmd": cmd, "job": job, **r}) + "\n" for r in rows))
        t = datetime.now().timestamp() - quiet_s
        os.utime(p, (t, t))

    def prog(ago, stage, status, label="", i=None, n=None, detail="", **kw):
        return {"ago": ago, "level": "progress", "step": stage, "msg": f"{stage} {status}", "stage": stage, "status": status, "label": label,
                "i": i, "n": n, "detail": detail, **kw}

    def px101(*a, **k):                                   # the tailor run of job 101 is part of the same execution as its `run`
        return prog(*a, execution_id="x-101", **k)

    def px102(*a, **k):
        return prog(*a, execution_id="x-102", **k)

    def meta(job, title, company, url):
        # every command of one execution shares its id (jobagent.progress), which is how the dashboard puts them on one card
        return {"plan": plan, "url": url, "title": title, "company": company, "job": job, "execution_id": f"x-{job}"}
    a, b, c, d, e = (meta("101", "Robotics Engineer", "Example Robotics", "https://jobs.example.test/1"),
                     meta("102", "Backend Engineer", "Sample Cloud Co", "https://jobs.example.test/2"),
                     meta("103", "ML Engineer", "Demo Labs", "https://jobs.example.test/3"),
                     meta("104", "Software Engineer", "Fictional Systems", "https://jobs.example.test/4"),
                     meta("105", "Platform Engineer", "Cleared Works", "https://jobs.example.test/5"))
    write("2026-11-17T080000-run-aaaa0101", "run", "", [prog(14, "scrape", "running", **a), prog(13.5, "scrape", "done", **a), prog(13, "research", "running", "Company research", **a),
                                                       prog(11, "research", "done", **a), prog(10.5, "documents", "running", "Resume and cover letter", **a)], 0.5, False)
    write("2026-11-17T080600-tailor-101-aaaa0102", "tailor", "101", [px101(10, "documents", "running", "Resume"), px101(9.5, "documents", "running", "First draft: experience, projects, skills, summary"),
                                                                    px101(6, "documents", "running", "Layout and quality check", 1, 4, "1 page(s), 3 finding(s)"),
                                                                    px101(5, "documents", "running", "Fixing findings", detail="experience (2), skills (1)"),
                                                                    px101(3.5, "documents", "running", "Layout and quality check", 2, 4, "1 page(s), 0 finding(s)"),
                                                                    px101(3, "documents", "running", "ATS baseline", 0, 3, "average 79.4 (target 90)"),
                                                                    px101(0.4, "documents", "running", "ATS round", 2, 3, "scoring on the checker sites")], 0.4, False)
    write("2026-11-17T081000-run-bbbb0201", "run", "", [prog(40, "scrape", "done", **b), prog(38, "research", "done", **b), prog(30, "documents", "done", **b),
                                                       prog(2, "apply", "running", "Filling the form", **b)], 0.6, False)
    write("2026-11-17T081500-apply-102-bbbb0202", "apply", "102", [px102(1.5, "apply", "running", "Form page", 1, 15, "Contact information"),
                                                                  px102(1.0, "apply", "running", "Form page", 2, 15, "Work history"),
                                                                  px102(0.3, "apply", "running", "Form page", 3, 15, "Questions")], 0.3, False)
    write("2026-11-17T070000-run-cccc0301", "run", "", [prog(120, "scrape", "done", **c), prog(118, "research", "done", **c), prog(100, "documents", "done", **c),
                                                       prog(90, "apply", "done", detail="filled", **c)], 90, True)
    write("2026-11-17T071000-run-dddd0401", "run", "", [prog(200, "scrape", "done", **d), prog(199, "research", "failed", detail="research failed", **d)], 199, True)
    write("2026-11-17T072000-run-eeee0501", "run", "", [prog(300, "scrape", "done", **e), prog(299, "research", "blocked", detail="eligibility: security clearance required", **e)], 299, True)
