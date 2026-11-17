"""F15: finished stages are kept and reused, an interrupted stage restarts, and an outside effect is never blindly replayed."""
import json

import pytest
from typer.testing import CliRunner

from jobagent import main
from jobagent.config import get_settings
from jobagent.tools import workflow as W
from test_apply_exit_codes import BASE, fake_world


def make(tmp_path):
    return W.Workflow(tmp_path)


def stage_fn(tmp_path, name, content="v1", calls=None):
    def fn():
        if calls is not None:
            calls.append(name)
        (tmp_path / name).write_text(content)
        return {"made": name}
    return fn


# --- the engine ------------------------------------------------------------------------------------------------------------

def test_a_finished_stage_is_reused_with_its_stored_result(tmp_path):
    calls = []
    res, how = make(tmp_path).run("resume", stage_fn(tmp_path, "resume.pdf", calls=calls), inputs={"jd": "a"}, outputs=["resume.pdf"])
    assert (res, how) == ({"made": "resume.pdf"}, "ran") and calls == ["resume.pdf"]
    res, how = make(tmp_path).run("resume", stage_fn(tmp_path, "resume.pdf", calls=calls), inputs={"jd": "a"}, outputs=["resume.pdf"])
    assert (res, how) == ({"made": "resume.pdf"}, "reused") and calls == ["resume.pdf"]                   # not run again


def test_a_changed_input_an_edited_output_or_a_missing_output_reruns_the_stage(tmp_path):
    calls = []
    run = lambda inputs: make(tmp_path).run("resume", stage_fn(tmp_path, "resume.pdf", calls=calls), inputs=inputs, outputs=["resume.pdf"])   # noqa: E731
    run({"jd": "a"})
    assert run({"jd": "a"})[1] == "reused"
    assert run({"jd": "b"})[1] == "ran"                                           # the posting changed
    (tmp_path / "resume.pdf").write_text("edited by hand")
    assert run({"jd": "b"})[1] == "ran"                                           # the file is not the one that was produced
    (tmp_path / "resume.pdf").unlink()
    assert run({"jd": "b"})[1] == "ran" and len(calls) == 4


def test_a_crash_in_a_later_stage_keeps_the_earlier_stage_and_restarts_only_the_later_one(tmp_path):
    calls = []

    def pipeline(crash):
        wf = make(tmp_path)
        wf.run("resume", stage_fn(tmp_path, "resume.pdf", calls=calls), inputs={"jd": "a"}, outputs=["resume.pdf"], log=lambda m: None)

        def cover():
            calls.append("cover")
            if crash:
                raise KeyboardInterrupt("killed")
            (tmp_path / "cover.pdf").write_text("c")
        wf.run("cover_letter", cover, inputs={"r": "1"}, outputs=["cover.pdf"], log=lambda m: None)
    with pytest.raises(KeyboardInterrupt):
        pipeline(True)
    assert make(tmp_path).stage("cover_letter")["status"] == "interrupted"
    pipeline(False)
    assert calls == ["resume.pdf", "cover", "cover"]                              # the resume was NOT built a second time
    assert [r["status"] for r in make(tmp_path).summary()] == ["done", "done"]


def test_a_stage_left_running_by_a_killed_process_is_interrupted_not_trusted(tmp_path):
    wf = make(tmp_path)
    wf.mark("apply", "running")
    again = make(tmp_path)
    assert again.stage("apply")["status"] == "interrupted" and "ended while" in again.stage("apply")["note"]
    assert make(tmp_path).id == wf.id                                            # one workflow id for the job


def test_a_stage_that_does_not_produce_its_files_fails_and_is_not_reusable(tmp_path):
    make(tmp_path).run("resume", lambda: None, inputs={}, outputs=["resume.pdf"])
    assert make(tmp_path).stage("resume")["status"] == "unverified" and not make(tmp_path).reusable("resume", {}, ["resume.pdf"])
    wf = make(tmp_path)
    res, how = wf.run("resume", lambda: {"produced": False}, inputs={}, outputs=["resume.pdf"], check=lambda r: "no valid resume")
    assert how == "ran" and wf.stage("resume")["status"] == "failed" and not wf.reusable("resume", {}, ["resume.pdf"])


# --- outside effects --------------------------------------------------------------------------------------------------------

def interrupted_submit(tmp_path):
    wf = make(tmp_path)
    wf.mark("submit", "running", side_effect=True)
    return make(tmp_path)                                                         # a new process: the stage is now "interrupted"


def test_an_interrupted_side_effect_is_never_replayed_blindly(tmp_path):
    sent = []
    wf = interrupted_submit(tmp_path)
    with pytest.raises(W.NeedsReconciliation, match="not known whether"):
        wf.run("submit", lambda: sent.append("SENT"), inputs={}, side_effect=True, force=True)
    assert sent == []
    wf = interrupted_submit(tmp_path)
    with pytest.raises(W.NeedsReconciliation):
        wf.run("submit", lambda: sent.append("SENT"), inputs={}, side_effect=True, reconcile=lambda: "unknown", force=True)
    assert sent == []


def test_reconciliation_decides_between_done_and_run(tmp_path):
    sent = []
    _, how = interrupted_submit(tmp_path).run("submit", lambda: sent.append("SENT"), inputs={}, side_effect=True,
                                              reconcile=lambda: "done", force=True)
    assert how == "reconciled" and sent == []                                     # the portal/attempts say it happened: nothing to redo
    _, how = interrupted_submit(tmp_path).run("submit", lambda: sent.append("SENT"), inputs={}, side_effect=True,
                                              reconcile=lambda: "not_done", force=True)
    assert how == "ran" and sent == ["SENT"]


# --- the commands -----------------------------------------------------------------------------------------------------------

@pytest.fixture
def tailor_world(monkeypatch, tmp_path):
    import jobagent.steps.multi_agent as M
    import jobagent.steps.tailor as ST
    import jobagent.tools.documents as D
    job = tmp_path / "outputs" / "acme" / "1"
    job.mkdir(parents=True)
    (job / "jd.json").write_text(json.dumps({"company": "Acme", "title": "Engineer", "source_url": "https://e.test/a"}))
    (job / "jd.txt").write_text("the posting")
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    data = tmp_path / "data"
    (data / "templates").mkdir(parents=True)
    (data / "templates/resume.tex").write_text("fixture template v1")
    monkeypatch.setenv("DATA_DIR", str(data))
    get_settings.cache_clear()
    calls = {"resume": 0, "cover": 0, "crash_cover": False}

    def run_job(d, slug, models, sites, log=print):
        calls["resume"] += 1
        for n in ("resume.pdf", "resume.tex", "filled.json"):
            (d / n).write_text(f"{n} #{calls['resume']}")
        return {"produced": True, "average": 91.0, "scores": {"jobscan": 91}, "pages": 1, "hard_issues": [], "average_complete": True,
                "average_missing": [], "average_sites": ["jobscan"], "claims_issues": [],
                "template_sha256": D.template_fingerprint()}

    def cover_letter(d, slug, log=print):
        calls["cover"] += 1
        if calls["crash_cover"]:
            raise RuntimeError("model unavailable")
        (d / "cover_letter.pdf").write_text("cl")
        (d / "cover_letter.tex").write_text("cl")
        return {"words": 300, "issues": []}
    monkeypatch.setattr(M, "run_job", run_job)
    monkeypatch.setattr(ST, "cover_letter", cover_letter)
    monkeypatch.setattr(D, "assess", lambda *a, **k: {"valid": True})
    monkeypatch.setattr(D, "save", lambda *a, **k: None)
    monkeypatch.setattr(D, "exit_code", lambda res: 0)
    monkeypatch.setattr(main, "_print_documents", lambda res: None)
    monkeypatch.setattr(main, "_stop_if_unavailable", lambda *a: None)
    yield job, calls
    get_settings.cache_clear()


def test_tailor_reuses_finished_stages_and_force_rebuilds_them(tailor_world):
    job, calls = tailor_world
    runner = CliRunner()
    r = runner.invoke(main.app, ["tailor", str(job), "acme"])
    assert r.exit_code == 0, r.output
    assert (calls["resume"], calls["cover"]) == (1, 1)
    r = runner.invoke(main.app, ["tailor", str(job), "acme"])
    assert r.exit_code == 0 and (calls["resume"], calls["cover"]) == (1, 1) and "reused" in r.output       # nothing was rebuilt
    r = runner.invoke(main.app, ["tailor", str(job), "acme", "--force"])
    assert (calls["resume"], calls["cover"]) == (2, 2)


def test_pipeline_resolves_locations_before_real_tailor_fingerprints_and_reuses_documents(tailor_world):
    from types import SimpleNamespace
    from jobagent import db
    from jobagent.tools.pipeline import Pipeline
    from jobagent.tools import places
    job, calls = tailor_world
    s = get_settings()
    c = db.connect(s.state_dir / "pipeline.sqlite")
    url = "https://e.test/a"
    seen = []
    def invoke(stage, **kw):
        seen.append(stage)
        if stage == "scrape":
            with c:
                c.execute("INSERT INTO jobs(url,company,job_id,status) VALUES(?,'Acme','1','scraped')", (url,))
        elif stage == "research":
            p = s.state_dir / "companies"; p.mkdir(exist_ok=True)
            (p / "acme.json").write_text('{"name":"Acme"}')
            (p / "acme.md").write_text("Acme profile")
        elif stage == "tailor":
            assert kw["no_ats"] is True
            assert "locations_resolved" in json.loads((job / "jd.json").read_text())
            return Pipeline._invoke(stage, **kw)       # real registered tailor callback and its fingerprint/cache
        elif stage == "apply":
            before = (job / "jd.json").read_bytes()
            if "locations_resolved" not in json.loads(before):
                places.resolve_job(job, "acme", log=lambda _: None)  # the real apply.context side effect
            assert (job / "jd.json").read_bytes() == before
            out = job / "application"; out.mkdir(exist_ok=True)
            (out / "application.json").write_text(json.dumps({"state": "review", "ready_to_submit": True}))
        return 0
    pipeline = Pipeline(c, SimpleNamespace(state_dir=s.state_dir, outputs_dir=job.parents[1]), invoke=invoke, log=lambda _: None)
    assert pipeline.run(url, no_ats=True).state == "filled"
    assert pipeline.run(url, no_ats=True).state == "filled"
    assert (calls["resume"], calls["cover"]) == (1, 1)
    assert seen.count("scrape") == seen.count("research") == 1
    assert seen.count("apply") == 2


def test_a_crash_in_the_cover_letter_does_not_cost_the_resume(tailor_world):
    job, calls = tailor_world
    calls["crash_cover"] = True
    r = CliRunner().invoke(main.app, ["tailor", str(job), "acme"])
    assert r.exit_code != 0 and (calls["resume"], calls["cover"]) == (1, 1)
    calls["crash_cover"] = False
    r = CliRunner().invoke(main.app, ["tailor", str(job), "acme"])
    assert r.exit_code == 0 and (calls["resume"], calls["cover"]) == (1, 2)                              # only the letter was redone
    r = CliRunner().invoke(main.app, ["workflow", str(job)])
    assert "resume" in r.output and "cover_letter" in r.output and "done" in r.output


def test_a_changed_posting_rebuilds_the_resume(tailor_world):
    job, calls = tailor_world
    CliRunner().invoke(main.app, ["tailor", str(job), "acme"])
    (job / "jd.txt").write_text("a different posting")
    CliRunner().invoke(main.app, ["tailor", str(job), "acme"])
    assert calls["resume"] == 2


def test_a_changed_owner_template_rebuilds_both_documents_then_reuses_them(tailor_world):
    job, calls = tailor_world
    runner = CliRunner()
    assert runner.invoke(main.app, ["tailor", str(job), "acme"]).exit_code == 0
    assert (calls["resume"], calls["cover"]) == (1, 1)
    (get_settings().data_dir / "templates/resume.tex").write_text("fixture template v2")
    changed = runner.invoke(main.app, ["tailor", str(job), "acme"])
    assert changed.exit_code == 0, changed.output
    assert (calls["resume"], calls["cover"]) == (2, 2)
    unchanged = runner.invoke(main.app, ["tailor", str(job), "acme"])
    assert unchanged.exit_code == 0, unchanged.output
    assert (calls["resume"], calls["cover"]) == (2, 2)


def test_two_commands_cannot_work_on_one_job_at_once(tailor_world):
    from jobagent.tools import bundle
    job, calls = tailor_world
    with bundle.job_lock(job, "tailor"):
        r = CliRunner().invoke(main.app, ["tailor", str(job), "acme"])
    assert r.exit_code == 1 and "already running" in r.output and calls["resume"] == 0


def test_apply_and_submit_record_their_stage(monkeypatch, tmp_path):
    job = fake_world(monkeypatch, tmp_path, {**BASE, "state": "review", "ready_to_submit": True, "blockers": []})
    r = CliRunner().invoke(main.app, ["apply", str(job), "acme"])
    assert r.exit_code == 0, r.output
    wf = W.Workflow(job)
    assert wf.stage("apply")["status"] == "done" and wf.stage("apply")["result"]["ready"] is True
    get_settings.cache_clear()
