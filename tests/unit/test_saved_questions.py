"""Job-application questions asked through HITL are saved per application with their full identity; an answer (live, or later on
the dashboard) is given back by Hitl.ask the next time the same application asks exactly that question, with nobody prompted,
present or away. Bot checks, CAPTCHAs, passwords and submission approval are never saved."""
import json
import shutil
import threading
import time

import pytest

from jobagent import progress
from jobagent.tools import presence
from jobagent.tools import saved_questions as SQ
from jobagent.tools.hitl import CandidateAway, Hitl

QUESTIONS = [{"id": 2, "label": "Do you have experience with modern web development with modern JS and TS frameworks?",
              "section": "Questions", "required": True, "kind": "radio", "options": ["Yes", "No"], "format": "", "help": ""},
             {"id": 5, "label": "What is your desired salary for this role?", "section": "Questions", "required": True,
              "kind": "text", "options": [], "format": "", "help": ""}]
HEAD = ("FinThrive application needs these answers. Reply with a JSON object mapping IDs to answers; use null to skip. "
        "For one question, plain text is also accepted.")


def batch(questions):
    return HEAD + "\n" + json.dumps(questions)


@pytest.fixture
def job(tmp_path):
    d = tmp_path / "outputs" / "finthrive" / "6502"
    (d / "application").mkdir(parents=True)
    (d / "jd.json").write_text(json.dumps({"company": "FinThrive", "title": "Software Engineer"}))
    return d


def answering(h, value, seen=None):
    def go():
        for _ in range(300):
            reqs = h.list_pending()
            if reqs:
                if seen is not None:
                    seen.append(reqs[0].prompt)
                h.answer(reqs[0].id, value)
                return
            time.sleep(0.01)
    threading.Thread(target=go, daemon=True).start()


def no_prompt(_):
    pytest.fail("a saved answer must not prompt the candidate")


# --- what counts as an application question -----------------------------------------------------------------------------

@pytest.mark.parametrize("prompt", [
    "A human check is showing (hcaptcha) on https://e.test. Solve it in the browser window, then answer done.",
    "The page needs account credentials that could not be completed from the vault: Password. Type them only in the browser window",
    "Review outputs/acme/1/application/application.md, then answer 'approve' to SUBMIT, anything else to stop.",
    "Acme application needs these answers. Reply with a JSON object\nnot json at all",
])
def test_puzzles_passwords_and_approvals_are_not_application_questions(prompt):
    assert SQ.parse(prompt) is None


def test_batches_and_the_original_fillers_single_question_are(job):
    assert [q["label"] for q in SQ.parse(batch(QUESTIONS))] == [q["label"] for q in QUESTIONS]
    single = SQ.parse('Acme application asks: "Are you willing to relocate?" (options: Yes | No) Reply with the answer')
    assert single[0]["label"] == "Are you willing to relocate?"


# --- saving --------------------------------------------------------------------------------------------------------------

def test_a_live_answer_is_saved_with_the_question_and_answers_md_is_untouched(tmp_path, job):
    h = Hitl(tmp_path / "state" / "hitl", lambda m: None)
    answering(h, json.dumps({"2": "Yes", "5": None}))
    with progress.execution(new=True, job_dir=str(job)):
        h.ask(batch(QUESTIONS), poll_s=0.01, timeout_s=5)
    rows = {r["label"]: r for r in SQ.load(job)}
    assert rows[QUESTIONS[0]["label"]]["status"] == "answered" and rows[QUESTIONS[0]["label"]]["answer"] == "Yes"
    assert rows[QUESTIONS[1]["label"]]["status"] == "open"                         # skipped: still waiting for an answer
    assert rows[QUESTIONS[0]["label"]]["options"] == ["Yes", "No"]
    assert not (job / "application" / "answers.md").exists()


def test_while_away_the_questions_are_saved_open_and_listed_for_the_dashboard(tmp_path, job):
    state = tmp_path / "state"
    presence.set_available(False, state_dir=state)
    with progress.execution(new=True, url="https://e.test/6502", job_dir=str(job)):
        with pytest.raises(CandidateAway):
            Hitl(state / "hitl", no_prompt).ask(batch(QUESTIONS))
    assert [r["status"] for r in SQ.load(job)] == ["open", "open"]
    listed = SQ.open_questions(tmp_path / "outputs")
    assert {q["job"] for q in listed} == {"finthrive/6502"} and listed[0]["company"] == "FinThrive"


def test_a_secret_or_a_terminal_only_request_is_never_saved_or_replayed(tmp_path, job):
    h = Hitl(tmp_path / "state" / "hitl", lambda m: None)
    SQ.record(job, SQ.parse(batch(QUESTIONS)), {SQ.key_of(SQ.parse(batch(QUESTIONS))[1]): "135980"})
    before = json.dumps(SQ.load(job), sort_keys=True)
    for kw in ({"secret": True}, {"terminal_only": True}):
        answering(h, "typed by hand")
        with progress.execution(new=True, job_dir=str(job)):
            assert h.ask(batch(QUESTIONS), poll_s=0.01, timeout_s=5, **kw) == "typed by hand"
    assert json.dumps(SQ.load(job), sort_keys=True) == before


# --- the rerun ---------------------------------------------------------------------------------------------------------------

def test_a_rerun_gets_the_saved_answers_with_nobody_prompted_even_while_away(tmp_path, job):
    state = tmp_path / "state"
    qs = SQ.parse(batch(QUESTIONS))
    SQ.record(job, qs, {SQ.key_of(qs[0]): "Yes", SQ.key_of(qs[1]): "135980"})
    presence.set_available(False, state_dir=state)
    rerun = [{**QUESTIONS[0], "id": 11}, {**QUESTIONS[1], "id": 12}]               # the page numbers its fields anew
    with progress.execution(new=True, url="https://e.test/6502", job_dir=str(job)):
        reply = Hitl(state / "hitl", no_prompt).ask(batch(rerun), timeout_s=0.1)
    assert json.loads(reply) == {"11": "Yes", "12": "135980"}
    assert not (state / "hitl" / "deferrals.jsonl").exists()


def test_only_the_unanswered_questions_are_asked_and_the_reply_has_all_of_them(tmp_path, job):
    qs = SQ.parse(batch(QUESTIONS))
    SQ.record(job, qs, {SQ.key_of(qs[0]): "Yes"})
    h = Hitl(tmp_path / "state" / "hitl", lambda m: None)
    seen = []
    answering(h, "140000", seen)
    with progress.execution(new=True, job_dir=str(job)):
        reply = h.ask(batch(QUESTIONS), poll_s=0.01, timeout_s=5)
    asked = json.loads(seen[0].partition("\n")[2])
    assert [q["label"] for q in asked] == [QUESTIONS[1]["label"]]                    # the saved one was not asked again
    assert json.loads(reply) == {"2": "Yes", "5": "140000"}
    assert {r["label"]: r["answer"] for r in SQ.load(job)}[QUESTIONS[1]["label"]] == "140000"


@pytest.mark.parametrize("change", [{"section": "Work Experience 2"}, {"options": ["Yes", "No", "Some"]},
                                    {"help": "Including internships"}])
def test_a_changed_question_is_a_new_question(tmp_path, job, change):
    qs = SQ.parse(batch(QUESTIONS[:1]))
    SQ.record(job, qs, {SQ.key_of(qs[0]): "Yes"})
    presence.set_available(False, state_dir=tmp_path / "state")
    with progress.execution(new=True, job_dir=str(job), url="https://e.test/6502"):
        with pytest.raises(CandidateAway):
            Hitl(tmp_path / "state" / "hitl", no_prompt).ask(batch([{**QUESTIONS[0], **change}]))


def test_an_answer_given_later_on_the_dashboard_is_used_by_the_next_run(tmp_path, job):
    state = tmp_path / "state"
    presence.set_available(False, state_dir=state)
    with progress.execution(new=True, url="https://e.test/6502", job_dir=str(job)):
        with pytest.raises(CandidateAway):
            Hitl(state / "hitl", no_prompt).ask(batch(QUESTIONS))
    for row in SQ.load(job):
        SQ.answer(job, row["qid"], "Yes" if row["kind"] == "radio" else "135980")
    with progress.execution(new=True, url="https://e.test/6502", job_dir=str(job)):
        assert json.loads(Hitl(state / "hitl", no_prompt).ask(batch(QUESTIONS))) == {"2": "Yes", "5": "135980"}


def test_a_required_question_needs_an_answer_and_an_optional_one_may_be_left_blank(job):
    req, opt = SQ.parse(batch([QUESTIONS[1], {**QUESTIONS[1], "id": 6, "label": "Anything else?", "required": False}]))
    SQ.record(job, [req, opt])
    with pytest.raises(ValueError):
        SQ.answer(job, req["qid"], "  ")
    assert SQ.answer(job, opt["qid"], "")["status"] == "answered"
    with pytest.raises(LookupError):
        SQ.answer(job, "no-such-question", "x")


def test_the_dashboard_cannot_reach_outside_the_outputs_folder(tmp_path, job):
    out = tmp_path / "outputs"
    assert SQ.job_dir_for(out, "finthrive/6502") == job.resolve()
    for bad in ("../state", "finthrive/../../x", "/etc/passwd", "finthrive", "finthrive/missing"):
        with pytest.raises((ValueError, LookupError)):
            SQ.job_dir_for(out, bad)


@pytest.fixture
def page():
    if not shutil.which("google-chrome"):
        pytest.skip("Chrome required")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        p = browser.new_page()
        p.context.jobagent_policy = lambda _: None
        yield p
        browser.close()


def test_the_real_filler_reruns_an_application_away_and_fills_saved_answers_per_row(page, tmp_path, job):
    """The filler asks through Hitl exactly as the adapter does; the candidate is away; the two employer rows share the label
    City and get their own saved answers; nothing is prompted and nothing is deferred."""
    from jobagent.filling.planned import PlannedFiller
    from jobagent.filling.facts import compile_facts
    state = tmp_path / "state"
    form = '''<h1>Application</h1>
      <section><h2>Work Experience 1</h2>
        <label>Employer<input value="First Co"></label><label>Title<input value="First Title"></label>
        <label for=c1>City</label><input id=c1 required></section>
      <section><h2>Work Experience 2</h2>
        <label>Employer<input value="Second Co"></label><label>Title<input value="Second Title"></label>
        <label for=c2>City</label><input id=c2 required></section>
      <label for=s>What is your desired salary for this role?</label><input id=s required>'''
    facts=compile_facts({'Experience':[{'question':'Work history','answer':[
        {'company':'First Co','title':'First Title'}, {'company':'Second Co','title':'Second Title'}]}]},'')
    page.set_content(form)
    h = Hitl(state / "hitl", lambda m: None)

    def ask(questions):                                   # the adapter's own request shape (filling/adapter.py ask)
        return json.loads(h.ask(batch(questions), poll_s=0.01, timeout_s=5))

    replies = {}                                          # first run: the candidate answers live

    def first_ask(questions):
        by_label = {("Work Experience 1", "City"): "Tempe", ("Work Experience 2", "City"): "Phoenix"}
        answer = {str(q["id"]): by_label.get((q["section"], q["label"]), "135980") for q in questions}
        replies.update(answer)
        answering(h, json.dumps(answer))
        return ask(questions)
    with progress.execution(new=True, url="https://e.test/6502", job_dir=str(job)):
        PlannedFiller(facts, first_ask).fill(page, advance_pages=False)
    assert replies and page.locator("#c1").input_value() == "Tempe"

    page.set_content(form)                                # the rerun starts from an empty form
    presence.set_available(False, state_dir=state)
    h.notify = no_prompt
    with progress.execution(new=True, url="https://e.test/6502", job_dir=str(job)):
        result = PlannedFiller(facts, ask).fill(page, advance_pages=False)
    assert result["state"] == "page_complete", result
    assert [page.locator(x).input_value() for x in ("#c1", "#c2", "#s")] == ["Tempe", "Phoenix", "135980"]
    assert not (state / "hitl" / "deferrals.jsonl").exists()


# --- the dashboard ---------------------------------------------------------------------------------------------------------

def test_the_dashboard_lists_open_questions_and_saves_an_answer(tmp_path):
    from test_dashboard import call
    from jobagent.config import Settings
    from jobagent.dashboard.demo import seed as demo
    from jobagent.dashboard.server import make_server
    settings = Settings(**demo(tmp_path))
    d = settings.outputs_dir / "finthrive" / "6502"
    (d / "application").mkdir(parents=True)
    (d / "jd.json").write_text(json.dumps({"company": "FinThrive", "title": "Software Engineer"}))
    SQ.record(d, SQ.parse(batch(QUESTIONS)))
    srv = make_server(settings, "127.0.0.1", 0, popen=lambda *a, **k: None)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        code, listed = call(srv, "GET", "/api/saved-questions")
        mine = [q for q in listed if q["job"] == "finthrive/6502"]
        assert code == 200 and len(mine) == 2
        qid = mine[0]["qid"]
        code, out = call(srv, "POST", "/api/saved-questions/answer", {"job": "finthrive/6502", "qid": qid, "value": "Yes"})
        assert code == 200 and out["answered"] == qid
        assert {r["qid"]: r["answer"] for r in SQ.load(d)}[qid] == "Yes"
        assert call(srv, "POST", "/api/saved-questions/answer", {"job": "../state", "qid": qid, "value": "Yes"})[0] == 400
        assert call(srv, "POST", "/api/saved-questions/answer", {"job": "finthrive/6502", "qid": qid, "value": " "})[0] == 400
    finally:
        srv.shutdown()
        srv.server_close()


def test_two_fields_a_request_does_not_tell_apart_are_never_saved_or_replayed(tmp_path, job):
    """Two City fields whose rows the page does not name have one identity: an answer for one could land in the other."""
    twins = [{"id": 1, "label": "City", "section": "", "kind": "text", "required": True, "options": [], "format": "", "help": ""},
             {"id": 2, "label": "City", "section": "", "kind": "text", "required": True, "options": [], "format": "", "help": ""}]
    qs = SQ.parse(batch(twins))
    SQ.record(job, qs, SQ.values(qs, json.dumps({"1": "Tempe", "2": "Phoenix"})))
    assert [r["status"] for r in SQ.load(job)] == ["open"]
    assert SQ.saved_answers(job, qs) == {}
    presence.set_available(False, state_dir=tmp_path / "state")
    with progress.execution(new=True, job_dir=str(job), url="https://e.test/6502"):
        with pytest.raises(CandidateAway):
            Hitl(tmp_path / "state" / "hitl", no_prompt).ask(batch(twins))
