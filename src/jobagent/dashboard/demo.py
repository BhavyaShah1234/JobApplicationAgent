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
        p.write_text("".join(json.dumps({"ts": _ts(ago), "run": stem[-8:], "job": job, "level": lv, "step": step, "msg": m}) + "\n" for lv, m in lines))
        t = datetime.now().timestamp() - ago * 60
        os.utime(p, (t, t))
    return {"state_dir": state, "outputs_dir": outputs, "data_dir": data}
