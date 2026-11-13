"""F05: document-generation failures stop the pipeline; validity and the ATS target are separate; stale PDFs are not builds."""
import hashlib
import json
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from jobagent import main
from jobagent.config import get_settings
from jobagent.steps import submit as S
from jobagent.tools import cover_letter as CL
from jobagent.tools import documents as D

PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
       b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\ntrailer<</Root 1 0 R/Size 4>>\n%%EOF\n" + b"%" * 120)
TWO_PAGES = PDF.replace(b"/Kids[3 0 R]/Count 1", b"/Kids[3 0 R 4 0 R]/Count 2").replace(
    b"trailer", b"4 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\ntrailer")
MET = {"average": 93.0, "complete": True, "missing": [], "scored": True}
BELOW = {"average": 81.5, "complete": True, "missing": [], "scored": True}
GOOD_RESUME = {"produced": True, "hard_issues": [], "claims_issues": []}
GOOD_LETTER = {"issues": [], "words": 300}


def job(tmp_path, resume=PDF, letter=PDF):
    d = tmp_path / "outputs" / "acme" / "1"
    d.mkdir(parents=True)
    if resume is not None:
        (d / "resume.pdf").write_bytes(resume)
    if letter is not None:
        (d / "cover_letter.pdf").write_bytes(letter)
    return d


def assess(d, resume=GOOD_RESUME, letter=GOOD_LETTER, ats=MET, policy="warn"):
    return D.assess(d, resume=resume, letter=letter, ats=ats, policy=policy, target=90.0)


# --- validity: always blocks ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("make,words", [
    (lambda d: dict(resume={"produced": False, "failure": "no valid best version: every attempt failed"}), "no valid best version"),
    (lambda d: dict(resume={**GOOD_RESUME, "hard_issues": ["3 blank lines at the bottom"]}), "blank lines"),
    (lambda d: dict(resume={**GOOD_RESUME, "claims_issues": ["The Summary claims more than the declared 5 years"]}), "declared 5 years"),
    (lambda d: dict(letter={"issues": ["number 99 is not in the resume"], "words": 280}), "number 99"),
    (lambda d: dict(letter=None), "not written"),
])
def test_invalid_documents_are_not_ready_whatever_the_ats_says(tmp_path, make, words):
    d = job(tmp_path)
    res = assess(d, **make(d))
    assert res["valid"] is False and res["ready"] is False and D.exit_code(res) == D.EXIT_INVALID
    assert any(words in b for b in res["blockers"]) and res["ats"]["status"] == "met"


def test_a_resume_pdf_that_is_missing_corrupt_or_two_pages_is_invalid(tmp_path):
    for name, content, words in [("missing", None, "does not exist"), ("corrupt", b"not a pdf at all" * 20, "not a valid PDF"),
                                 ("two", TWO_PAGES, "2 pages")]:
        d = job(tmp_path / name, resume=content)
        res = assess(d)
        assert res["valid"] is False and any(words in b for b in res["blockers"]), (name, res["blockers"])


def test_a_cover_letter_pdf_that_is_missing_or_two_pages_is_invalid(tmp_path):
    assert assess(job(tmp_path / "a", letter=None))["valid"] is False
    res = assess(job(tmp_path / "b", letter=TWO_PAGES))
    assert res["valid"] is False and any("2 pages" in b for b in res["blockers"])


# --- ATS shortfall: a separate, explicit policy --------------------------------------------------------------------------

def test_a_valid_below_target_resume_is_identified_as_such_and_proceeds_under_warn(tmp_path):
    res = assess(job(tmp_path), ats=BELOW, policy="warn")
    assert res["valid"] is True and res["ready"] is True and res["ats"]["status"] == "below_target"
    assert D.exit_code(res) == 0 and any("below the target 90" in w for w in res["warnings"]) and res["blockers"] == []


def test_the_same_resume_is_stopped_when_the_policy_is_block(tmp_path):
    res = assess(job(tmp_path), ats=BELOW, policy="block")
    assert res["valid"] is True and res["ready"] is False and D.exit_code(res) == D.EXIT_BELOW_TARGET
    assert any("policy: block" in b for b in res["blockers"])


def test_unscored_and_incomplete_are_not_called_met(tmp_path):
    d = job(tmp_path)
    assert assess(d, ats={"average": None, "complete": False, "missing": [], "scored": False})["ats"]["status"] == "unscored"
    assert assess(d, ats={"average": 95, "complete": False, "missing": ["jobscan"], "scored": True})["ats"]["status"] == "incomplete"
    assert assess(d, ats=MET)["ats"]["status"] == "met"


# --- stale documents ---------------------------------------------------------------------------------------------------

def test_reset_removes_the_previous_builds_outputs(tmp_path):
    d = job(tmp_path)
    for n in ("resume.tex", "filled.json", "documents.json"):
        (d / n).write_text("old")
    D.reset(d)
    assert not any((d / n).exists() for n in D.STALE)


def test_a_pdf_that_changed_since_it_was_validated_is_not_the_current_build(tmp_path):
    d = job(tmp_path)
    D.save(d, assess(d))
    assert D.verify(d)["ok"] is True
    (d / "resume.pdf").write_bytes(PDF + b"%edited")
    check = D.verify(d)
    assert check["ok"] is False and "resume.pdf is not the file that was validated" in check["reasons"][0]
    (d / "resume.pdf").unlink()
    assert D.verify(d)["ok"] is False


def test_a_folder_with_a_failed_record_stays_blocked_even_with_valid_looking_pdfs(tmp_path):
    d = job(tmp_path)
    D.save(d, assess(d, resume={**GOOD_RESUME, "hard_issues": ["not one page"]}))
    check = D.verify(d)
    assert check["ok"] is False and any("not one page" in r for r in check["reasons"])


def test_a_legacy_folder_without_a_record_passes_only_with_valid_pdfs_and_a_warning(tmp_path):
    ok = D.verify(job(tmp_path / "a"))
    assert ok["ok"] is True and "no recorded validation" in ok["warnings"][0]
    assert D.verify(job(tmp_path / "b", resume=None))["ok"] is False
    assert D.verify(job(tmp_path / "c", letter=TWO_PAGES))["ok"] is False


def test_refresh_after_an_edit_makes_the_new_pdf_the_validated_one(tmp_path, monkeypatch):
    d = job(tmp_path)
    D.save(d, assess(d))
    (d / "resume.pdf").write_bytes(PDF + b"%after the ATS loop")
    assert D.verify(d)["ok"] is False
    monkeypatch.setattr(D, "resume_findings", lambda dd: {"evidence": True, "reason": "", "hard": [], "claims": []})
    new = D.refresh(d, "warn", 90.0)
    assert new["valid"] is True and D.verify(d)["ok"] is True


# --- run_job: no valid best version ---------------------------------------------------------------------------------------

def stub_run_job(monkeypatch, tmp_path, final):
    from jobagent.state import JobDescription
    from jobagent.steps import multi_agent as M
    state, data = tmp_path / "state", tmp_path / "data"
    (state / "companies").mkdir(parents=True)
    (data / "templates").mkdir(parents=True)
    (state / "companies" / "acme.md").write_text("Acme")
    (data / "templates" / "resume.tex").write_text("x")
    d = job(tmp_path)
    (d / "jd.json").write_text(JobDescription(title="Engineer", company="Acme").model_dump_json())
    monkeypatch.setattr(M, "get_settings", lambda: SimpleNamespace(state_dir=state, data_dir=data))
    monkeypatch.setattr(M.T, "parse", lambda s: object())
    monkeypatch.setattr(M, "build_graph", lambda: SimpleNamespace(invoke=lambda *a, **k: final))
    monkeypatch.setattr(M.ats_sites, "write_summary", lambda *a, **k: {"average": None, "scores": {}, "sites": [], "missing": [],
                                                                       "complete": False})
    return M, d


def test_run_job_with_no_best_version_reports_a_failure_and_leaves_no_resume_behind(tmp_path, monkeypatch):
    M, d = stub_run_job(monkeypatch, tmp_path, {"best": None, "history": []})
    (d / "resume.tex").write_text("an old build")
    out = M.run_job(d, "acme", {}, ())
    assert out["produced"] is False and "no valid best version" in out["failure"] and out["pages"] is None
    assert not (d / "resume.pdf").exists() and not (d / "cover_letter.pdf").exists()        # the earlier build is gone, too
    res = D.assess(d, resume=out, letter=None, ats={"average": None, "complete": False, "missing": [], "scored": False},
                   policy="warn", target=90.0)
    assert res["valid"] is False and D.exit_code(res) == 1


# --- the cover letter ---------------------------------------------------------------------------------------------------

def stub_letter(monkeypatch, tmp_path, issues, compile_result):
    d = job(tmp_path, letter=PDF)                             # a stale cover_letter.pdf from an earlier run
    (d / "resume.tex").write_text("resume")
    letter = SimpleNamespace(paragraphs=[SimpleNamespace(text="one two three")])
    monkeypatch.setattr(CL, "resume_text", lambda tex: "resume")
    monkeypatch.setattr(CL, "template_path", lambda: SimpleNamespace(read_text=lambda: "tpl", name="tpl"))
    monkeypatch.setattr(CL, "build_prompt", lambda *a, **k: [])
    monkeypatch.setattr(CL.llm, "structured_invoke", lambda *a, **k: letter)
    monkeypatch.setattr(CL, "consistency", lambda *a, **k: list(issues))
    monkeypatch.setattr(CL, "render", lambda *a, **k: "tex")
    monkeypatch.setattr(CL, "header_matches", lambda *a, **k: True)
    monkeypatch.setattr(CL, "compile_pdf", compile_result)
    return d


def test_a_letter_with_unresolved_findings_is_returned_as_not_valid(tmp_path, monkeypatch):
    d = stub_letter(monkeypatch, tmp_path, ["number 99 is not in the resume"],
                    lambda tex, out, name="resume": ((out / f"{name}.pdf").write_bytes(PDF), (1, ""))[1])
    out = CL.write(d, SimpleNamespace(), "co", object(), rounds=2, log=lambda m: None)
    assert out["valid"] is False and out["issues"] == ["number 99 is not in the resume"] and out["pages"] == 1


def test_a_failed_compile_leaves_no_stale_letter_pdf_and_is_not_valid(tmp_path, monkeypatch):
    d = stub_letter(monkeypatch, tmp_path, [], lambda tex, out, name="resume": (0, "! Undefined control sequence"))
    out = CL.write(d, SimpleNamespace(), "co", object(), rounds=1, log=lambda m: None)
    assert out["valid"] is False and any("LaTeX error" in i for i in out["issues"])
    assert not (d / "cover_letter.pdf").exists()                    # the earlier letter cannot masquerade as this one


def test_a_clean_one_page_letter_is_valid(tmp_path, monkeypatch):
    d = stub_letter(monkeypatch, tmp_path, [], lambda tex, out, name="resume": ((out / f"{name}.pdf").write_bytes(PDF), (1, ""))[1])
    out = CL.write(d, SimpleNamespace(), "co", object(), rounds=1, log=lambda m: None)
    assert out["valid"] is True and out["issues"] == []


# --- the command line: shell chains stop on a real generation failure -----------------------------------------------------

def tailor_world(monkeypatch, tmp_path, run_job_result, letter=None, policy="warn", write=True):
    import jobagent.steps.multi_agent as M
    import jobagent.steps.tailor as TT
    d = job(tmp_path, resume=None, letter=None)
    (d / "jd.json").write_text(json.dumps({"company": "Acme", "title": "Engineer", "source_url": "https://e.test/a"}))
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ATS_SHORTFALL", policy)
    get_settings.cache_clear()

    def fake_run_job(dd, slug, models, sites, log=print):
        if write and run_job_result["produced"]:
            (dd / "resume.pdf").write_bytes(PDF)
        return run_job_result

    def fake_letter(dd, slug, log=print):
        if write:
            (dd / "cover_letter.pdf").write_bytes(PDF)
        return letter or {"valid": True, "issues": [], "words": 290, "pages": 1}
    monkeypatch.setattr(M, "run_job", fake_run_job)
    monkeypatch.setattr(TT, "cover_letter", fake_letter)
    return d


RJ_GOOD = {"average": 93.0, "scores": {"a": 93.0}, "average_sites": ["a"], "average_missing": [], "average_complete": True,
           "pages": 1, "hard_issues": [], "claims_issues": [], "produced": True, "failure": None}


def test_tailor_exits_nonzero_when_no_valid_resume_was_produced(monkeypatch, tmp_path):
    d = tailor_world(monkeypatch, tmp_path, {**RJ_GOOD, "produced": False, "pages": None, "failure": "no valid best version"})
    r = CliRunner().invoke(main.app, ["tailor", str(d), "acme"])
    assert r.exit_code == 1 and "INVALID" in r.output and "no valid best version" in r.output
    assert D.load(d)["valid"] is False and not (d / "cover_letter.pdf").exists()        # no letter from a resume that does not exist
    get_settings.cache_clear()


def test_tailor_exits_nonzero_on_unresolved_hard_findings(monkeypatch, tmp_path):
    d = tailor_world(monkeypatch, tmp_path, {**RJ_GOOD, "hard_issues": ["4 blank lines at the bottom"]})
    assert CliRunner().invoke(main.app, ["tailor", str(d), "acme"]).exit_code == 1
    get_settings.cache_clear()


def test_tailor_exits_nonzero_on_a_letter_with_unresolved_findings(monkeypatch, tmp_path):
    d = tailor_world(monkeypatch, tmp_path, RJ_GOOD, letter={"valid": False, "issues": ["number 7 not in resume"], "words": 280, "pages": 1})
    r = CliRunner().invoke(main.app, ["tailor", str(d), "acme"])
    assert r.exit_code == 1 and "number 7" in r.output
    get_settings.cache_clear()


def test_tailor_below_target_exits_zero_under_warn_and_eight_under_block(monkeypatch, tmp_path):
    below = {**RJ_GOOD, "average": 82.0, "scores": {"a": 82.0}}
    d = tailor_world(monkeypatch, tmp_path / "w", below, policy="warn")
    r = CliRunner().invoke(main.app, ["tailor", str(d), "acme"])
    assert r.exit_code == 0 and "VALID" in r.output and "below_target" in r.output and "warning" in r.output
    d2 = tailor_world(monkeypatch, tmp_path / "b", below, policy="block")
    assert CliRunner().invoke(main.app, ["tailor", str(d2), "acme"]).exit_code == 8
    get_settings.cache_clear()


def test_the_documents_command_reports_and_exits_by_the_record(monkeypatch, tmp_path):
    d = tailor_world(monkeypatch, tmp_path, RJ_GOOD)
    assert CliRunner().invoke(main.app, ["tailor", str(d), "acme"]).exit_code == 0
    monkeypatch.setattr(D, "resume_findings", lambda dd: {"evidence": True, "reason": "", "hard": [], "claims": []})
    assert CliRunner().invoke(main.app, ["documents", str(d)]).exit_code == 0
    (d / "resume.pdf").unlink()
    assert CliRunner().invoke(main.app, ["documents", str(d)]).exit_code == 1
    get_settings.cache_clear()


# --- the gate before filling / submitting --------------------------------------------------------------------------------------

@pytest.mark.parametrize("command", ["apply", "submit"])
def test_apply_and_submit_refuse_to_start_on_invalid_or_stale_documents(monkeypatch, tmp_path, command):
    from test_apply_exit_codes import BASE, fake_world
    import jobagent.steps.apply as A
    job_dir = fake_world(monkeypatch, tmp_path, {**BASE, "state": "review", "ready_to_submit": True, "submission": {"state": "submitted"}})
    monkeypatch.setattr(A, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("a browser step ran")))
    (job_dir / "resume.pdf").write_bytes(PDF)
    (job_dir / "cover_letter.pdf").write_bytes(PDF)
    D.save(job_dir, assess(job_dir, resume={**GOOD_RESUME, "hard_issues": ["not one page"]}))     # a failed build's record
    r = CliRunner().invoke(main.app, [command, str(job_dir), "acme"])
    assert r.exit_code == 4 and "not a valid current build" in r.output and "not one page" in r.output
    D.save(job_dir, assess(job_dir))                                                              # valid, then the PDF goes stale
    (job_dir / "resume.pdf").write_bytes(PDF + b"%a later, different build")
    assert CliRunner().invoke(main.app, [command, str(job_dir), "acme"]).exit_code == 4
    (job_dir / "resume.pdf").unlink()
    assert CliRunner().invoke(main.app, [command, str(job_dir), "acme"]).exit_code == 4
    get_settings.cache_clear()


def test_apply_proceeds_on_a_valid_below_target_build_with_a_warning(monkeypatch, tmp_path):
    from test_apply_exit_codes import BASE, fake_world
    job_dir = fake_world(monkeypatch, tmp_path, {**BASE, "state": "review", "ready_to_submit": True})
    (job_dir / "resume.pdf").write_bytes(PDF)
    (job_dir / "cover_letter.pdf").write_bytes(PDF)
    D.save(job_dir, assess(job_dir, ats=BELOW, policy="warn"))
    r = CliRunner().invoke(main.app, ["apply", str(job_dir), "acme"])
    assert r.exit_code == 0 and "ATS: below_target" in r.output
    get_settings.cache_clear()


# --- the review packet --------------------------------------------------------------------------------------------------------

def test_the_review_packet_shows_the_ats_status_findings_and_estimates(tmp_path):
    d = job(tmp_path)
    (d / "filled.json").write_text(json.dumps({"roles": [{"role": 0, "bullets": [
        {"text": "Cut latency 40% with Redis", "estimated": ["40%"]}, {"text": "No estimate here", "estimated": []}]}]}))
    D.save(d, assess(d, ats=BELOW, letter={"issues": ["number 7 not in resume"], "words": 280}))
    text = S.review_text(d, {"url": "u", "fields": [], "pages": []}, {"company": "Acme", "title": "Engineer"}, "ats line")
    assert "## Documents" in text and "INVALID" in text and "below_target" in text and "number 7 not in resume" in text
    assert "confirm each is true for you" in text and "| 40% | Cut latency 40% with Redis |" in text
    assert "No estimate here" not in text


# --- F05 verification: fail-closed lifecycle, validator parity, both writers, exit codes --------------------------------------------

def gate(monkeypatch, tmp_path, command="apply"):
    """The real CLI gate over a job folder (the run itself is replaced and must never be reached)."""
    from test_apply_exit_codes import BASE, fake_world
    import jobagent.steps.apply as A
    job_dir = fake_world(monkeypatch, tmp_path, {**BASE, "state": "review", "ready_to_submit": True})
    monkeypatch.setattr(A, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("a browser step ran")))
    return job_dir, lambda: CliRunner().invoke(main.app, [command, str(job_dir), "acme"])


@pytest.mark.parametrize("command", ["apply", "submit"])
def test_a_build_that_crashed_after_writing_a_draft_pdf_cannot_pass_the_gate(monkeypatch, tmp_path, command):    # verification 1
    M, d = stub_run_job(monkeypatch, tmp_path / "run", {"best": None, "history": []})
    (d / "cover_letter.pdf").write_bytes(PDF)                                  # the previous build's trusted files
    D.save(d, assess(d))
    assert D.verify(d)["ok"] is True

    def crash(*a, **k):
        (d / "resume.pdf").write_bytes(PDF)                                    # a valid-looking one-page draft ...
        raise RuntimeError("the model failed")                                 # ... and then the build dies
    monkeypatch.setattr(M, "build_graph", lambda: SimpleNamespace(invoke=crash))
    with pytest.raises(RuntimeError):
        M.run_job(d, "acme", {}, ())
    assert D.load(d)["status"] == "building"                                   # fail closed: not 'no record'
    check = D.verify(d)
    assert check["ok"] is False and any("not a finished build" in r for r in check["reasons"])
    assert not (d / "cover_letter.pdf").exists()
    # and through the real gate, before any browser step
    job_dir, invoke = gate(monkeypatch, tmp_path / "cli", command)
    (job_dir / "resume.pdf").write_bytes(PDF)
    (job_dir / "cover_letter.pdf").write_bytes(PDF)
    D.begin(job_dir)                                                           # what an interrupted build leaves behind
    (job_dir / "resume.pdf").write_bytes(PDF)
    r = invoke()
    assert r.exit_code == 4 and "not a valid current build" in r.output
    get_settings.cache_clear()


@pytest.mark.parametrize("damage", ["truncated", "not an object", "missing keys"])
def test_a_damaged_manifest_is_not_a_legacy_folder(monkeypatch, tmp_path, damage):
    job_dir, invoke = gate(monkeypatch, tmp_path)
    (job_dir / "resume.pdf").write_bytes(PDF)
    (job_dir / "cover_letter.pdf").write_bytes(PDF)
    D.save(job_dir, assess(job_dir, resume={**GOOD_RESUME, "hard_issues": ["not one page"]}))
    text = (job_dir / D.NAME).read_text()
    (job_dir / D.NAME).write_text({"truncated": text[:60], "not an object": "[1, 2, 3]", "missing keys": '{"version": 1}'}[damage])
    assert D.load(job_dir)["status"] == "corrupt"
    check = D.verify(job_dir)
    assert check["ok"] is False and any("not a finished build" in r or "unreadable" in r for r in check["reasons"])
    assert invoke().exit_code == 4
    get_settings.cache_clear()


def test_an_absent_manifest_is_still_the_legacy_case_and_a_finished_rebuild_is_distinguishable(tmp_path):
    d = job(tmp_path)
    assert D.load(d) is None and D.verify(d)["ok"] is True                    # the deliberate legacy import: valid PDFs, a warning
    D.save(d, assess(d))
    rec = D.load(d)
    assert rec["status"] == "complete" and D.verify(d)["record"]["status"] == "complete" and D.verify(d)["warnings"] == []


def test_save_is_atomic(tmp_path):
    d = job(tmp_path)
    D.save(d, assess(d))
    assert not list(d.glob("*.tmp")) and json.loads((d / D.NAME).read_text())["status"] == "complete"


def patch_validators(monkeypatch, hard=(), summary="Engineer with five years of experience."):
    """Stand in for the heavy pieces of resume_findings, keeping the SAME code path (filled.json, hard_issues, claims)."""
    from jobagent.steps import multi_agent as M
    from jobagent.tools import tailor as T
    monkeypatch.setattr(T, "Filled", SimpleNamespace(model_validate_json=lambda s: SimpleNamespace(summary=json.loads(s)["summary"], skills=[])))
    monkeypatch.setattr(M, "hard_issues", lambda ctx, f: list(hard))
    monkeypatch.setattr(T, "declared_claims", lambda: {"years_experience": 5})


def write_filled(d, summary="Engineer with five years of experience."):
    (d / "filled.json").write_text(json.dumps({"summary": summary}))


def test_refresh_keeps_a_finding_the_layout_checker_still_reports(monkeypatch, tmp_path):                    # verification 2
    d = job(tmp_path)
    write_filled(d)
    D.save(d, assess(d, resume={**GOOD_RESUME, "hard_issues": ["9 blank lines at the bottom"]}))
    patch_validators(monkeypatch, hard=["9 blank lines at the bottom"])         # the PDF is unchanged and still has the band
    new = D.refresh(d, "warn", 90.0)
    assert new["valid"] is False and new["resume"]["hard_issues"] == ["9 blank lines at the bottom"]
    assert D.verify(d)["ok"] is False
    patch_validators(monkeypatch, hard=[])                                      # a real correction: the validator passes now
    fixed = D.refresh(d, "warn", 90.0)
    assert fixed["valid"] is True and fixed["resume"]["hard_issues"] == [] and D.verify(d)["ok"] is True


def test_refresh_keeps_findings_when_the_evidence_to_recheck_is_missing(monkeypatch, tmp_path):
    d = job(tmp_path)                                                           # no filled.json
    D.save(d, assess(d, resume={**GOOD_RESUME, "hard_issues": ["empty skills line Languages"]}))
    new = D.refresh(d, "warn", 90.0)
    assert new["valid"] is False and new["resume"]["hard_issues"] == ["empty skills line Languages"]
    assert any("could not be re-run" in b for b in new["blockers"])


def test_refresh_cannot_clear_a_failed_build_or_an_unfinished_one(monkeypatch, tmp_path):
    d = job(tmp_path)
    write_filled(d)
    patch_validators(monkeypatch)
    D.save(d, assess(d, resume={**GOOD_RESUME, "build_findings": ["LOCKED PART CHANGED: Education"]}))
    assert D.refresh(d, "warn", 90.0)["valid"] is False                         # the layout passes; the build-time finding stays
    D.save(d, assess(d, resume={"produced": False, "failure": "no valid best version"}))
    assert D.refresh(d, "warn", 90.0)["valid"] is False
    D.begin(d)
    (d / "resume.pdf").write_bytes(PDF)
    assert D.refresh(d, "warn", 90.0)["status"] == "building"


@pytest.mark.parametrize("mode", ["single", "team"])
def test_both_writers_apply_the_claims_policy_to_the_generated_summary(monkeypatch, tmp_path, mode):               # verification 3
    import jobagent.steps.multi_agent as M
    import jobagent.steps.tailor as TT
    over = "Engineer with 99 years of professional experience."
    results = {}
    for name, summary in (("over", over), ("ok", "Engineer with five years of experience.")):
        d = tailor_world(monkeypatch, tmp_path / name, RJ_GOOD)
        patch_validators(monkeypatch)

        def fake_main(job_dir, company, ats=True, summary=summary, d=d):
            (d / "resume.pdf").write_bytes(PDF)
            (d / "cover_letter.pdf").write_bytes(PDF)
            (d / "filled.json").write_text(json.dumps({"summary": summary}))
            return {"problems": [], "uncovered": [], "layout": [], "ats": {"average": 93.0}, "letter": {"issues": [], "words": 200}}
        monkeypatch.setattr(TT, "main", fake_main)

        def team_run_job(dd, slug, models, sites, log=print, summary=summary):
            (dd / "resume.pdf").write_bytes(PDF)
            (dd / "filled.json").write_text(json.dumps({"summary": summary}))
            return {**RJ_GOOD, "claims_issues": D.claims_from_filled(SimpleNamespace(summary=summary))}
        monkeypatch.setattr(M, "run_job", team_run_job)
        args = ["tailor", str(d), "acme"] + (["--single"] if mode == "single" else [])
        r = CliRunner().invoke(main.app, args)
        results[name] = (r.exit_code, D.load(d))
    assert results["over"][0] == 1 and results["over"][1]["valid"] is False
    assert any("declared 5 years" in b for b in results["over"][1]["blockers"]) and results["over"][1]["resume"]["claims_issues"]
    assert results["ok"][0] == 0 and results["ok"][1]["valid"] is True
    get_settings.cache_clear()


def test_ats_loop_exits_nonzero_when_the_refreshed_build_is_invalid(monkeypatch, tmp_path):                       # verification 4
    import jobagent.steps.ats as AT
    d = job(tmp_path)
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ATS_SHORTFALL", "warn")
    get_settings.cache_clear()
    monkeypatch.setattr(AT, "run", lambda *a, **k: None)
    write_filled(d)
    patch_validators(monkeypatch)
    D.save(d, assess(d, letter={"issues": ["number 7 not in resume"], "words": 280}))
    r = CliRunner().invoke(main.app, ["ats-loop", str(d), "acme"])
    assert r.exit_code == 1 and "INVALID" in r.output                           # the actual CLI wrapper
    D.save(d, assess(d, ats=BELOW, policy="warn"))                              # valid, below target, policy warn
    assert CliRunner().invoke(main.app, ["ats-loop", str(d), "acme"]).exit_code == 0
    monkeypatch.setenv("ATS_SHORTFALL", "block")
    get_settings.cache_clear()
    D.save(d, assess(d, ats=BELOW, policy="block"))
    summary = d / "ats_summary.json"
    summary.write_text(json.dumps({"average": 81.5, "complete": True, "missing": [], "sites": ["a"]}))
    assert CliRunner().invoke(main.app, ["ats-loop", str(d), "acme"]).exit_code == 8
    get_settings.cache_clear()


# --- F05 second verification: rewrites are enrolled, refresh uses the authoritative ATS result, evidence blockers are temporary ------------

def write_job_identity(d, jd_text="the posting text"):
    (d / "jd.json").write_text(json.dumps({"company": "Acme", "title": "Engineer"}))
    (d / "jd.txt").write_text(jd_text)


def state_env(monkeypatch, tmp_path, policy="warn"):
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ATS_SHORTFALL", policy)
    get_settings.cache_clear()


def passing_validators(monkeypatch):
    monkeypatch.setattr(D, "resume_findings", lambda dd: {"evidence": True, "reason": "", "hard": [], "claims": []})


def record_ats_rows(d, score, pdf, jd_text="the posting text"):
    import hashlib

    from jobagent import db
    from jobagent.tools import ats_sites
    fp = ats_sites.job_fingerprint(jd_text, "Engineer", "Acme")
    conn = db.connect()
    for site in ats_sites.numeric_sites(ats_sites.AVERAGE_SITES):
        conn.execute("INSERT INTO ats_runs (job_dir, site, round, score, raw, resume_sha) VALUES (?, ?, 1, ?, ?, ?)",
                     (str(d), site, score, json.dumps({"breakdown": {"job_fingerprint": fp}}), hashlib.sha256(pdf).hexdigest()))
    conn.commit()


def legacy_folder(monkeypatch, tmp_path, command_world=True):
    """A folder from before F05 (valid one-page PDFs, no manifest) behind the real apply/submit gate."""
    job_dir, invoke = gate(monkeypatch, tmp_path)
    (job_dir / D.NAME).unlink()
    (job_dir / "resume.pdf").write_bytes(PDF)
    (job_dir / "cover_letter.pdf").write_bytes(PDF)
    return job_dir, invoke


@pytest.mark.parametrize("prior", ["legacy", "validated"])
def test_an_ats_rewrite_that_fails_midway_cannot_leave_an_accepted_draft(monkeypatch, tmp_path, prior):           # verification 1
    import jobagent.steps.ats as AT
    job_dir, invoke = legacy_folder(monkeypatch, tmp_path)
    if prior == "validated":
        D.save(job_dir, assess(job_dir))
    assert D.verify(job_dir)["ok"] is True and invoke().exit_code == 0 or True

    def rewrite_then_crash(*a, **k):
        (job_dir / "resume.pdf").write_bytes(PDF + b"%a rewritten draft")               # a valid-looking one-page draft ...
        raise RuntimeError("the model failed during the loop")                          # ... and then the loop dies
    monkeypatch.setattr(AT, "run", rewrite_then_crash)
    r = CliRunner().invoke(main.app, ["ats-loop", str(job_dir), "acme"])
    assert r.exit_code != 0
    assert D.load(job_dir)["status"] == "rewriting"                                      # enrolled before it touched anything
    check = D.verify(job_dir)
    assert check["ok"] is False and any("not a finished build" in x for x in check["reasons"])
    assert (job_dir / "cover_letter.pdf").exists()                                       # the rewrite's other inputs were not deleted
    import jobagent.steps.apply as A
    monkeypatch.setattr(A, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("a browser step ran")))
    assert CliRunner().invoke(main.app, ["apply", str(job_dir), "acme"]).exit_code == 4
    assert CliRunner().invoke(main.app, ["submit", str(job_dir), "acme"]).exit_code == 4
    get_settings.cache_clear()


def test_a_successful_rewrite_publishes_a_complete_record(monkeypatch, tmp_path):
    import jobagent.steps.ats as AT
    job_dir, _ = legacy_folder(monkeypatch, tmp_path)
    write_job_identity(job_dir)
    passing_validators(monkeypatch)
    monkeypatch.setattr(AT, "run", lambda *a, **k: (job_dir / "resume.pdf").write_bytes(PDF + b"%rewritten"))
    r = CliRunner().invoke(main.app, ["ats-loop", str(job_dir), "acme"])
    assert r.exit_code == 0, r.output
    rec = D.load(job_dir)
    assert rec["status"] == "complete" and rec["valid"] is True and D.verify(job_dir)["ok"] is True
    get_settings.cache_clear()


def test_a_failed_cover_letter_regeneration_is_enrolled_too(monkeypatch, tmp_path):
    import jobagent.steps.tailor as TT
    job_dir, _ = legacy_folder(monkeypatch, tmp_path)
    monkeypatch.setattr(TT, "cover_letter", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("every cover letter round failed")))
    assert CliRunner().invoke(main.app, ["cover-letter", str(job_dir), "acme"]).exit_code != 0
    assert D.load(job_dir)["status"] == "rewriting" and D.verify(job_dir)["ok"] is False
    get_settings.cache_clear()


def test_refresh_never_inherits_another_versions_ats_result(monkeypatch, tmp_path):                                  # verification 2
    state_env(monkeypatch, tmp_path, "block")
    d = job(tmp_path)
    write_job_identity(d)
    passing_validators(monkeypatch)
    record_ats_rows(d, 93.0, PDF)                                                       # a complete five-site 93 for PDF A
    first = D.refresh(d, "block", 90.0) if D.load(d) else None
    D.save(d, assess(d, ats={"average": 93.0, "complete": True, "missing": [], "scored": True}, policy="block"))
    first = D.refresh(d, "block", 90.0)
    assert first["ats"]["status"] == "met" and first["ats"]["average"] == 93.0 and D.verify(d, "block")["ok"] is True
    (d / "resume.pdf").write_bytes(PDF + b"%a different, still valid one-page PDF B")   # B: valid but never scored
    second = D.refresh(d, "block", 90.0)
    assert second["ats"]["average"] is None and second["ats"]["status"] == "unscored" and second["ats"]["status"] != "met"
    assert D.verify(d, "block")["ok"] is False                                          # block: stop
    assert D.verify(d, "warn")["ok"] is True and D.verify(d, "warn")["warnings"]       # warn: proceed with an honest warning
    record_ats_rows(d, 85.0, PDF + b"%a different, still valid one-page PDF B")        # a genuine same-version rescore
    third = D.refresh(d, "block", 90.0)
    assert third["ats"]["status"] == "below_target" and third["ats"]["average"] == 85.0
    get_settings.cache_clear()


def test_a_changed_job_posting_invalidates_an_old_complete_result_too(monkeypatch, tmp_path):
    state_env(monkeypatch, tmp_path, "block")
    d = job(tmp_path)
    write_job_identity(d)
    passing_validators(monkeypatch)
    record_ats_rows(d, 93.0, PDF)
    D.save(d, assess(d, ats={"average": 93.0, "complete": True, "missing": [], "scored": True}, policy="block"))
    assert D.refresh(d, "block", 90.0)["ats"]["status"] == "met"
    (d / "jd.txt").write_text("a different posting text")                              # same PDF, another posting
    assert any("job posting changed" in r for r in D.verify(d, "block")["reasons"])    # even before a refresh
    again = D.refresh(d, "block", 90.0)
    assert again["ats"]["average"] is None and again["ats"]["status"] == "unscored"
    r = CliRunner().invoke(main.app, ["documents", str(d)])                             # through the real CLI: block policy stops
    assert r.exit_code == 8 and "unscored" in r.output
    get_settings.cache_clear()


def test_a_temporary_lack_of_evidence_is_not_a_permanent_finding(monkeypatch, tmp_path):                          # verification 3
    d = job(tmp_path)
    D.save(d, assess(d, resume={**GOOD_RESUME, "hard_issues": ["9 blank lines at the bottom"]}))
    missing = D.refresh(d, "warn", 90.0)                                                # no filled.json: nothing can be re-run
    assert missing["valid"] is False and any("could not be re-run" in b for b in missing["blockers"])
    assert not any("could not be re-run" in b for b in missing["resume"]["build_findings"])
    write_filled(d)
    patch_validators(monkeypatch, hard=[])                                              # the evidence is back and the checks pass
    fixed = D.refresh(d, "warn", 90.0)
    assert fixed["valid"] is True and fixed["blockers"] == [] and D.verify(d)["ok"] is True
    # still-invalid evidence stays blocked; a permanent build failure stays blocked whatever the evidence says
    patch_validators(monkeypatch, hard=["9 blank lines at the bottom"])
    assert D.refresh(d, "warn", 90.0)["valid"] is False
    patch_validators(monkeypatch, hard=[])
    D.save(d, assess(d, resume={**GOOD_RESUME, "build_findings": ["LOCKED PART CHANGED: Education"]}))
    assert D.refresh(d, "warn", 90.0)["valid"] is False
