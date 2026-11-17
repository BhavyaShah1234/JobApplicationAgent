"""Made-up data for developing and testing the dashboard without touching a real state folder: `seed(root)` fills `root/state`, `root/outputs`
and `root/data` with invented companies, a queue, two applications at review, a question, runs and outcomes."""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path


def _ts(minutes_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def seed(root: Path, secret_question: bool = True) -> dict:
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
    return {"state_dir": state, "outputs_dir": outputs, "data_dir": data}


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

    def meta(job, title, company, url):
        return {"plan": plan, "url": url, "title": title, "company": company, "job": job}
    a, b, c, d, e = (meta("101", "Robotics Engineer", "Example Robotics", "https://jobs.example.test/1"),
                     meta("102", "Backend Engineer", "Sample Cloud Co", "https://jobs.example.test/2"),
                     meta("103", "ML Engineer", "Demo Labs", "https://jobs.example.test/3"),
                     meta("104", "Software Engineer", "Fictional Systems", "https://jobs.example.test/4"),
                     meta("105", "Platform Engineer", "Cleared Works", "https://jobs.example.test/5"))
    write("2026-11-17T080000-run-aaaa0101", "run", "", [prog(14, "scrape", "running", **a), prog(13.5, "scrape", "done", **a), prog(13, "research", "running", "Company research", **a),
                                                       prog(11, "research", "done", **a), prog(10.5, "documents", "running", "Resume and cover letter", **a)], 0.5, False)
    write("2026-11-17T080600-tailor-101-aaaa0102", "tailor", "101", [prog(10, "documents", "running", "Resume"), prog(9.5, "documents", "running", "First draft: experience, projects, skills, summary"),
                                                                    prog(6, "documents", "running", "Layout and quality check", 1, 4, "1 page(s), 3 finding(s)"),
                                                                    prog(5, "documents", "running", "Fixing findings", detail="experience (2), skills (1)"),
                                                                    prog(3.5, "documents", "running", "Layout and quality check", 2, 4, "1 page(s), 0 finding(s)"),
                                                                    prog(3, "documents", "running", "ATS baseline", 0, 3, "average 79.4 (target 90)"),
                                                                    prog(0.4, "documents", "running", "ATS round", 2, 3, "scoring on the checker sites")], 0.4, False)
    write("2026-11-17T081000-run-bbbb0201", "run", "", [prog(40, "scrape", "done", **b), prog(38, "research", "done", **b), prog(30, "documents", "done", **b),
                                                       prog(2, "apply", "running", "Filling the form", **b)], 0.6, False)
    write("2026-11-17T081500-apply-102-bbbb0202", "apply", "102", [prog(1.5, "apply", "running", "Form page", 1, 15, "Contact information"),
                                                                  prog(1.0, "apply", "running", "Form page", 2, 15, "Work history"),
                                                                  prog(0.3, "apply", "running", "Form page", 3, 15, "Questions")], 0.3, False)
    write("2026-11-17T070000-run-cccc0301", "run", "", [prog(120, "scrape", "done", **c), prog(118, "research", "done", **c), prog(100, "documents", "done", **c),
                                                       prog(90, "apply", "done", detail="filled", **c)], 90, True)
    write("2026-11-17T071000-run-dddd0401", "run", "", [prog(200, "scrape", "done", **d), prog(199, "research", "failed", detail="research failed", **d)], 199, True)
    write("2026-11-17T072000-run-eeee0501", "run", "", [prog(300, "scrape", "done", **e), prog(299, "research", "blocked", detail="eligibility: security clearance required", **e)], 299, True)
