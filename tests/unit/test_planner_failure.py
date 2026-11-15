"""F03: a failed planner must never look like a successful review, and review readiness is more than a Submit button."""
import json
import re

import pytest
from playwright.sync_api import sync_playwright

from jobagent.steps import apply as A
from jobagent.tools import forms

ONE_REQUIRED = ("<h1>Job application</h1><form><label>Email <input name=email type=email required></label>"
                "<button type=submit>Submit application</button></form>")
REQUIRED_AND_OPTIONAL = ("<h1>Job application</h1><form><label>Email <input name=email type=email required></label>"
                         "<label>Nickname <input name=nick type=text></label>"
                         "<button type=submit>Submit application</button></form>")
JD = {"company": "Acme", "title": "Engineer", "apply_url": "https://example.test/apply", "source_url": "https://example.test/apply"}


def browser_page(html):
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(channel="chrome", headless=True)
    except Exception:
        pw.stop()
        pytest.skip("no Chrome here")
    page = browser.new_page()
    page.set_content(html)
    page.wait_for_timeout = lambda ms, _real=page.wait_for_timeout: _real(min(ms, 150))
    return pw, browser, page


def failing_plan(*a, **k):
    raise RuntimeError("the model is down")


def answer_email_only(todo, ctx, model, log, feedback="", adds=(), filled=()):
    """A planner that answers the required email field and leaves every other field unanswered."""
    return {f.id: A.Answer(id=f.id, why="from applicant", value="me@example.org", source="applicant")
            for f in todo if f.kind == "email"}, []


def quiet_pipeline(monkeypatch, plan):
    """Replace the model-backed stages of the page pipeline; keep the browser, the field reader and the read-back real."""
    monkeypatch.setattr(A, "plan", plan)
    monkeypatch.setattr(A, "_tie_break", lambda model, log: None)
    for name in ("enforce", "check", "apply_variants", "consult_learned"):
        monkeypatch.setattr(A, name, lambda *a, **k: None)
    monkeypatch.setattr(A, "resolve", lambda todo, answers, ctx, model, env, log, deferred=None: None)


def fill(page, record, **env_extra):
    ctx = {"_jd": JD, "_applicant": {}}
    try:
        return A.fill_page(page, ctx, {}, object(), lambda m: None, record, 1,
                           {"hitl": None, "company": "Acme", "host": "x", **env_extra})
    except Exception as e:                      # the old code returned normally; the new code raises PlannerFailed
        return e


def run_review(page, tmp_path, monkeypatch, at_review=None):
    job = tmp_path / "outputs" / "acme" / "1"
    job.mkdir(parents=True)
    monkeypatch.setattr(A, "context", lambda job_dir, company: {"_jd": JD, "_applicant": {}})
    monkeypatch.setattr(A, "_start", lambda page, url, *a, **k: forms.read_page(page))
    # These tests replace A.plan/check/resolve: they intentionally exercise the original engine.
    res = A._run(page, job, "Acme", log=lambda m: None, model=object(), hitl=None, at_review=at_review,
                 filling_strategy="original")
    return res, job


# --- reproduction: a failing planner ----------------------------------------------------------------------------------

def test_a_failing_planner_leaves_a_record_for_every_visible_field(monkeypatch):
    quiet_pipeline(monkeypatch, failing_plan)
    pw, browser, page = browser_page(ONE_REQUIRED)
    try:
        record: list = []
        fill(page, record)
        assert len(record) == 1, "the visible required field has no record"
        e = record[0]
        assert e["label"].lower().startswith("email") and e["required"] is True
        assert e["verified"] is False and "planner" in (e["flag"] or "").lower() and e["outcome"] == "unplanned"
    finally:
        browser.close()
        pw.stop()


def test_a_failing_planner_ends_the_run_blocked_not_in_review(monkeypatch, tmp_path):
    quiet_pipeline(monkeypatch, failing_plan)
    pw, browser, page = browser_page(ONE_REQUIRED)
    reviewed = []
    try:
        res, job = run_review(page, tmp_path, monkeypatch, at_review=lambda *a: reviewed.append(a) or {"state": "x"})
        assert res["state"] == "blocked" and res["blocked_by"] == "planner" and "model is down" in res["reason"]
        assert res["ready_to_submit"] is False and reviewed == [], "a blocked run must not reach the approval prompt"
        assert len(res["fields"]) == 1 and res["verified"] == 0 and len(res["flags"]) == 1 and res["outcomes"] == {"unplanned": 1}
        md = (job / "application" / "application.md").read_text()
        assert "blocked" in md.lower() and "planner" in md.lower() and "0/0" not in md
        saved = json.loads((job / "application" / "application.json").read_text())
        assert saved["state"] == "blocked" and saved["blocked_by"] == "planner"
        assert A.exit_code(res) == A.EXIT_BLOCKED
    finally:
        browser.close()
        pw.stop()


# --- honest outcomes and readiness ------------------------------------------------------------------------------------

def test_optional_unanswered_fields_are_represented_honestly_and_do_not_block(monkeypatch, tmp_path):
    def plan_all(todo, ctx, model, log, feedback="", adds=(), filled=()):
        return {f.id: A.Answer(id=f.id, why="a", value="me@example.org" if f.kind == "email" else "", source="applicant")
                for f in todo}, []
    quiet_pipeline(monkeypatch, plan_all)
    pw, browser, page = browser_page(REQUIRED_AND_OPTIONAL)
    try:
        res, job = run_review(page, tmp_path, monkeypatch)
        outcomes = {e["label"].split()[0].lower(): e["outcome"] for e in res["fields"]}
        assert outcomes == {"email": "verified", "nickname": "left empty (optional)"}
        assert res["verified"] == 1 and res["outcomes"] == {"left empty (optional)": 1, "verified": 1}   # not 2/2 "verified"
        assert "1 left empty (optional)" in (job / "application" / "application.md").read_text()
        assert res["state"] == "review" and res["blockers"] == [] and res["ready_to_submit"] is True
        assert A.exit_code(res) == A.EXIT_OK
    finally:
        browser.close()
        pw.stop()


def test_a_required_field_nobody_answered_is_a_blocker_even_though_a_submit_button_exists(monkeypatch, tmp_path):
    def plan_none(todo, ctx, model, log, feedback="", adds=(), filled=()):
        return {}, []                                   # the planner skipped everything (not an exception)
    quiet_pipeline(monkeypatch, plan_none)
    pw, browser, page = browser_page(ONE_REQUIRED)
    try:
        res, job = run_review(page, tmp_path, monkeypatch)
        assert res["state"] == "review"                              # the review screen was reached...
        assert res["ready_to_submit"] is False                       # ...but the application is not ready
        assert res["blockers"] and "email" in res["blockers"][0].lower() and "no answer was planned" in res["blockers"][0]
        assert res["fields"][0]["outcome"] == "unplanned"
        assert "NOT ready" in (job / "application" / "application.md").read_text()
        assert A.exit_code(res) == A.EXIT_NOT_READY
    finally:
        browser.close()
        pw.stop()


def test_the_review_prompt_hook_still_runs_when_not_ready_so_the_candidate_can_fix_things(monkeypatch, tmp_path):
    quiet_pipeline(monkeypatch, lambda *a, **k: ({}, []))
    pw, browser, page = browser_page(ONE_REQUIRED)
    seen = []
    try:
        res, _ = run_review(page, tmp_path, monkeypatch, at_review=lambda p, v, r: seen.append(r["blockers"]) or {"state": "skipped"})
        assert seen and seen[0], "the hook receives the blockers"
    finally:
        browser.close()
        pw.stop()


# --- exit codes: what shell chains and the queue see --------------------------------------------------------------------

@pytest.mark.parametrize("result,code", [
    ({"state": "review", "ready_to_submit": True}, 0),
    ({"state": "review", "ready_to_submit": False}, 5),
    ({"state": "blocked", "blocked_by": "planner"}, 4),
    ({"state": "blocked", "blocked_by": "challenge"}, 4),
    ({"state": "stuck"}, 1), ({"state": "failed"}, 1), ({"state": "something-new"}, 1), ({}, 1),
])
def test_exit_codes(result, code):
    assert A.exit_code(result) == code


def test_the_queue_maps_apply_exit_codes_to_distinct_states():
    from jobagent import cli_discovery as C
    assert C.apply_outcome(0) == ("filled", "") and C.apply_outcome(4)[0] == "blocked" and C.apply_outcome(5)[0] == "needs_attention"
    assert C.apply_outcome(1)[0] == "failed" and C.apply_outcome(2)[0] == "failed"
    assert C.apply_outcome(3) is None, "exit 3 means the posting is unavailable: step 7 / the stop already recorded it"


# --- the review hook: readiness is checked again, on the live page, when the candidate approves -------------------------

def hook_env(tmp_path, monkeypatch, view, answer="approve"):
    from datetime import datetime  # noqa: F401
    from jobagent import db
    from jobagent.steps import submit as S
    job = tmp_path / "outputs" / "acme" / "1"
    (job / "application").mkdir(parents=True)
    conn = db.connect(tmp_path / "jobs.sqlite")
    prompts = []

    class Hitl:
        def ask(self, prompt, timeout_s=0):
            prompts.append(prompt)
            return answer
    monkeypatch.setattr(S, "screenshot", lambda *a, **k: None)
    monkeypatch.setattr(S.forms, "read_page", lambda page: view)
    clicks = []
    monkeypatch.setattr(S, "click_submit", lambda page, v, approval: clicks.append(approval) or None)
    hook = S.make_at_review(job, JD, Hitl(), None, conn, lambda m: None)
    return hook, conn, prompts, clicks, S


def view_with(*fields, errors=()):
    return forms.PageView(list(fields), ["Submit application"], list(errors), ["Review"], {}, [])


def test_approving_does_not_send_while_a_required_field_is_empty(tmp_path, monkeypatch):
    empty = forms.Field(id=1, kind="email", label="Email", required=True, value="")
    hook, conn, prompts, clicks, S = hook_env(tmp_path, monkeypatch, view_with(empty))
    result = {"state": "review", "fields": [], "verified": 0, "pages": [], "url": "u", "ready_to_submit": False,
              "blockers": ["Email (page 1): required, no answer was planned"]}
    out = hook(None, view_with(empty), result)
    assert out["state"] == "not_ready" and "Email: required and empty" in out["blockers"][0]
    assert clicks == [], "nothing may be clicked while a required field is empty"
    assert "NOT READY" in prompts[0] and "no answer was planned" in prompts[0]
    assert conn.execute("SELECT state FROM applications").fetchone()[0] == "filled"
    assert "NOT ready" in (tmp_path / "outputs" / "acme" / "1" / "application" / "review.md").read_text()


def test_a_page_error_also_blocks_the_approved_send(tmp_path, monkeypatch):
    ok = forms.Field(id=1, kind="email", label="Email", required=True, value="me@example.org")
    hook, conn, prompts, clicks, S = hook_env(tmp_path, monkeypatch, view_with(ok, errors=["Please enter a valid phone"]))
    out = hook(None, view_with(ok), {"state": "review", "fields": [], "verified": 0, "pages": [], "url": "u",
                                     "ready_to_submit": True, "blockers": []})
    assert out["state"] == "not_ready" and clicks == []


def test_a_complete_page_proceeds_to_the_approved_send_path(tmp_path, monkeypatch):
    ok = forms.Field(id=1, kind="email", label="Email", required=True, value="me@example.org")
    hook, conn, prompts, clicks, S = hook_env(tmp_path, monkeypatch, view_with(ok))
    out = hook(None, view_with(ok), {"state": "review", "fields": [], "verified": 1, "pages": [], "url": "u",
                                     "ready_to_submit": True, "blockers": []})
    assert len(clicks) == 1 and isinstance(clicks[0], forms.SubmitApproval)       # the approval object reached the click
    assert out["state"] == "failed" and "NOT READY" not in prompts[0]             # (the stub found no button to click)


def test_skip_leaves_the_application_untouched_whatever_the_readiness(tmp_path, monkeypatch):
    empty = forms.Field(id=1, kind="email", label="Email", required=True, value="")
    hook, conn, prompts, clicks, S = hook_env(tmp_path, monkeypatch, view_with(empty), answer="skip")
    out = hook(None, view_with(empty), {"state": "review", "fields": [], "verified": 0, "pages": [], "url": "u",
                                        "ready_to_submit": False, "blockers": ["x"]})
    assert out["state"] == "skipped" and clicks == []


# --- a dead model in the step before the planner, and failures nobody anticipated ---------------------------------------

def test_a_failing_address_preparation_is_a_planner_failure_with_a_record_per_field(monkeypatch, tmp_path):
    quiet_pipeline(monkeypatch, lambda *a, **k: ({}, []))             # never reached
    monkeypatch.setattr(A, "match_entries", lambda todo, company, data: {f.id: ("Address", {"question": "Current address"}) for f in todo})

    def boom(ctx, out_dir, model, log):
        raise RuntimeError("the model is down")
    monkeypatch.setattr(A.application_address, "prepare", boom)
    pw, browser, page = browser_page(ONE_REQUIRED)
    try:
        record: list = []
        exc = fill(page, record, out_dir=tmp_path)
        assert isinstance(exc, A.PlannerFailed) and "address preparation failed" in str(exc)
        assert len(record) == 1 and record[0]["outcome"] == "unplanned" and record[0]["verified"] is False
    finally:
        browser.close()
        pw.stop()


def test_an_unexpected_error_leaves_a_failed_result_on_disk_and_still_fails_loudly(monkeypatch, tmp_path):
    def explode(*a, **k):
        raise ValueError("something nobody anticipated")
    monkeypatch.setattr(A, "_run", explode)
    pw, browser, page = browser_page(ONE_REQUIRED)
    job = tmp_path / "outputs" / "acme" / "1"
    job.mkdir(parents=True)
    try:
        with pytest.raises(ValueError):
            A.run(page, job, "Acme", log=lambda m: None, hitl=None)
        saved = json.loads((job / "application" / "application.json").read_text())
        assert saved["state"] == "failed" and "ValueError" in saved["reason"] and saved["ready_to_submit"] is False
        assert A.exit_code(saved) == A.EXIT_FAILED
        assert "failed" in (job / "application" / "application.md").read_text()
    finally:
        browser.close()
        pw.stop()


def test_a_bot_check_still_ends_blocked_not_failed(monkeypatch, tmp_path):
    from jobagent.tools import challenge

    def blocked(*a, **k):
        raise challenge.Blocked("a cloudflare check blocks https://x.test")
    monkeypatch.setattr(A, "_run", blocked)
    pw, browser, page = browser_page(ONE_REQUIRED)
    job = tmp_path / "outputs" / "acme" / "1"
    job.mkdir(parents=True)
    try:
        res = A.run(page, job, "Acme", log=lambda m: None, hitl=None)
        assert res["state"] == "blocked" and res["blocked_by"] == "challenge" and A.exit_code(res) == A.EXIT_BLOCKED
    finally:
        browser.close()
        pw.stop()
