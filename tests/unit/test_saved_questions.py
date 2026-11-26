"""Job-application questions asked through HITL are saved per application; an answer (live or later on the dashboard) goes into
that application's answers.md, so a rerun fills it without the candidate. Bot checks, CAPTCHAs, passwords and submission
approval are never saved."""
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
BATCH = ("FinThrive application needs these answers. Reply with a JSON object mapping IDs to answers; use null to skip. "
         "For one question, plain text is also accepted.\n" + json.dumps(QUESTIONS))


@pytest.fixture
def job(tmp_path):
    d = tmp_path / "outputs" / "finthrive" / "6502"
    (d / "application").mkdir(parents=True)
    (d / "jd.json").write_text(json.dumps({"company": "FinThrive", "title": "Software Engineer"}))
    return d


def answer_soon(h, value):
    def go():
        for _ in range(300):
            reqs = h.list_pending()
            if reqs:
                h.answer(reqs[0].id, value)
                return
            time.sleep(0.01)
    threading.Thread(target=go, daemon=True).start()


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
    assert [q["label"] for q in SQ.parse(BATCH)] == [q["label"] for q in QUESTIONS]
    single = SQ.parse('Acme application asks: "Are you willing to relocate?" (options: Yes | No) Reply with the answer')
    assert single[0]["label"] == "Are you willing to relocate?"


# --- saving through Hitl.ask -----------------------------------------------------------------------------------------------

def test_a_live_answer_is_saved_for_this_application_and_written_to_its_answer_sheet(tmp_path, job):
    h = Hitl(tmp_path / "state" / "hitl", lambda m: None)
    answer_soon(h, json.dumps({"2": "Yes", "5": None}))
    with progress.execution(new=True, job_dir=str(job)):
        h.ask(BATCH, poll_s=0.01, timeout_s=5)
    rows = {r["label"]: r for r in SQ.load(job)}
    assert rows[QUESTIONS[0]["label"]]["status"] == "answered" and rows[QUESTIONS[0]["label"]]["answer"] == "Yes"
    assert rows[QUESTIONS[1]["label"]]["status"] == "open"                         # skipped: still waiting for an answer
    assert rows[QUESTIONS[0]["label"]]["times_asked"] == 1
    sheet = (job / "application" / "answers.md").read_text()
    assert f"| 1 | {QUESTIONS[0]['label']} | Yes |" in sheet and "desired salary" not in sheet


def test_while_away_the_questions_are_saved_open_for_later(tmp_path, job):
    state = tmp_path / "state"
    presence.set_available(False, state_dir=state)
    h = Hitl(state / "hitl", lambda m: None)
    with progress.execution(new=True, url="https://e.test/6502", job_dir=str(job)):
        with pytest.raises(CandidateAway):
            h.ask(BATCH)
    assert [r["status"] for r in SQ.load(job)] == ["open", "open"]
    listed = SQ.open_questions(tmp_path / "outputs")
    assert {q["job"] for q in listed} == {"finthrive/6502"} and listed[0]["company"] == "FinThrive"


def test_a_secret_or_a_terminal_only_request_is_never_saved(tmp_path, job):
    h = Hitl(tmp_path / "state" / "hitl", lambda m: None)
    for kw in ({"secret": True}, {"terminal_only": True}):
        answer_soon(h, "x")
        with progress.execution(new=True, job_dir=str(job)):
            h.ask(BATCH, poll_s=0.01, timeout_s=5, **kw)
    assert SQ.load(job) == [] and not (job / "application" / "answers.md").exists()


# --- answering later -------------------------------------------------------------------------------------------------------

def test_a_later_answer_updates_its_own_row_and_never_the_candidates_rows(job):
    (job / "application" / "answers.md").write_text("My notes for FinThrive.\n\n| # | Question | Answer |\n|---|---|---|\n"
                                                    "| 1 | Preferred start date | 2027-01-04 |\n")
    SQ.record(job, QUESTIONS)
    key = SQ.key_of(QUESTIONS[1])
    SQ.answer(job, key, "135980")
    SQ.answer(job, key, "140000")                                                    # changed their mind: same row
    sheet = (job / "application" / "answers.md").read_text()
    assert "My notes for FinThrive." in sheet and "| 1 | Preferred start date | 2027-01-04 |" in sheet
    assert "| 2 | What is your desired salary for this role? | 140000 |" in sheet and "135980" not in sheet
    from jobagent.steps.apply import sheet_rows
    assert ("What is your desired salary for this role?", "140000") in sheet_rows(sheet)
    with pytest.raises(LookupError):
        SQ.answer(job, "no|such|question", "x")


def test_the_dashboard_cannot_reach_outside_the_outputs_folder(tmp_path, job):
    out = tmp_path / "outputs"
    assert SQ.job_dir_for(out, "finthrive/6502") == job.resolve()
    for bad in ("../state", "finthrive/../../x", "/etc/passwd", "finthrive", "finthrive/missing"):
        with pytest.raises((ValueError, LookupError)):
            SQ.job_dir_for(out, bad)


# --- the rerun ---------------------------------------------------------------------------------------------------------------

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


def test_a_rerun_fills_a_saved_answer_without_asking(page, job):
    """The answer reaches the filler the way apply loads it: answers.md -> ANSWERS_FOR_THIS_APPLICATION -> answer_sheet_facts."""
    from jobagent.filling.planned import PlannedFiller
    from jobagent.filling.routing import answer_sheet_facts
    SQ.record(job, QUESTIONS)
    SQ.answer(job, SQ.key_of(QUESTIONS[1]), "135980")
    sheet = (job / "application" / "answers.md").read_text()
    page.set_content('<h1>Questions</h1><label for=s>What is your desired salary for this role?</label><input id=s required>')
    filler = PlannedFiller(answer_sheet_facts(sheet), lambda _: pytest.fail("a saved answer must not be asked again"))
    result = filler.fill(page, advance_pages=False)
    assert result["state"] == "page_complete" and page.locator("#s").input_value() == "135980"


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
    SQ.record(d, QUESTIONS)
    srv = make_server(settings, "127.0.0.1", 0, popen=lambda *a, **k: None)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        code, listed = call(srv, "GET", "/api/saved-questions")
        assert code == 200 and len([q for q in listed if q["job"] == "finthrive/6502"]) == 2
        key = SQ.key_of(QUESTIONS[0])
        code, out = call(srv, "POST", "/api/saved-questions/answer", {"job": "finthrive/6502", "key": key, "value": "Yes"})
        assert code == 200 and out["answered"] == key
        assert "| Yes |" in (d / "application" / "answers.md").read_text()
        assert call(srv, "POST", "/api/saved-questions/answer", {"job": "../state", "key": key, "value": "Yes"})[0] == 400
        assert call(srv, "POST", "/api/saved-questions/answer", {"job": "finthrive/6502", "key": key, "value": ""})[0] == 400
    finally:
        srv.shutdown()
        srv.server_close()


# --- answers a label-only sheet row could misapply stay out of answers.md -------------------------------------------------

@pytest.mark.parametrize("question,answer,reason", [
    ({"id": 1, "label": "City", "section": "Personal Information", "kind": "text"}, "Tempe", "short caption"),
    ({"id": 1, "label": "What was your reason for leaving this employer?", "section": "Employment History 1", "kind": "text"},
     "Contract ended", "employer, school or reference row"),
    ({"id": 1, "label": "Describe why you want this role in a few sentences?", "section": "Questions", "kind": "textarea"},
     "First line.\nSecond line.", "multi-line"),
    ({"id": 1, "label": "Which of these tools have you used in production?", "section": "Questions", "kind": "text"},
     "Go | Rust", "multi-line answer or a '|'"),
])
def test_answers_a_label_only_row_could_misapply_are_kept_but_not_written(job, question, answer, reason):
    SQ.record(job, [question])
    row = SQ.answer(job, SQ.key_of(question), answer)
    assert row["status"] == "answered" and row["answer"] == answer.strip()
    assert reason in row["sheet"]
    assert not (job / "application" / "answers.md").exists() or question["label"] not in (job / "application" / "answers.md").read_text()


def test_one_label_in_two_sections_and_an_owner_row_are_never_written(job):
    a = {"id": 1, "label": "Do you have a valid driver's license?", "section": "Questions", "kind": "radio"}
    b = {**a, "id": 2, "section": "Travel"}
    SQ.record(job, [a, b])
    assert "more than one section" in SQ.answer(job, SQ.key_of(a), "Yes")["sheet"]
    (job / "application" / "answers.md").write_text("| # | Question | Answer |\n|---|---|---|\n| 1 | Are you willing to relocate for this role? | No |\n")
    c = {"id": 3, "label": "Are you willing to relocate for this role?", "section": "Questions", "kind": "radio"}
    SQ.record(job, [c])
    assert "yours wins" in SQ.answer(job, SQ.key_of(c), "Yes")["sheet"]
    assert (job / "application" / "answers.md").read_text().count("relocate") == 1     # the owner's row only, unchanged


def test_a_row_past_what_apply_reads_is_not_written(job):
    (job / "application" / "answers.md").write_text("x" * 5990 + "\n")
    q = {"id": 1, "label": "What is your desired salary for this role?", "section": "Questions", "kind": "text"}
    SQ.record(job, [q])
    assert "too long" in SQ.answer(job, SQ.key_of(q), "135980")["sheet"]


def test_away_then_answered_on_the_dashboard_then_a_rerun_fills_it_with_no_question(page, tmp_path, job):
    """The whole path: the agent skips the question while away (saved open), the candidate answers it later, and the rerun reads
    answers.md exactly as apply does (first 6000 characters) and fills it; an employer row's City is NOT filled from anything."""
    from jobagent.filling.facts import Fact
    from jobagent.filling.planned import PlannedFiller
    from jobagent.filling.routing import answer_sheet_facts
    state = tmp_path / "state"
    presence.set_available(False, state_dir=state)
    with progress.execution(new=True, url="https://e.test/6502", job_dir=str(job)):
        with pytest.raises(CandidateAway):
            Hitl(state / "hitl", lambda m: None).ask(BATCH)
    city = {"id": 9, "label": "City", "section": "Employment History 1", "kind": "text"}
    SQ.record(job, [city])
    SQ.answer(job, SQ.key_of(QUESTIONS[1]), "135980")
    SQ.answer(job, SQ.key_of(city), "Tempe")
    assert SQ.load(job)[-1]["sheet"].startswith("not reused")
    sheet = (job / "application" / "answers.md").read_text()[:6000]                  # as steps/apply.py context() reads it
    page.set_content('''<h1>Questions</h1><label for=s>What is your desired salary for this role?</label><input id=s required>
      <section><h2>Employment History 1</h2><label for=c>City</label><input id=c></section>''')
    asked = []
    filler = PlannedFiller(answer_sheet_facts(sheet) + [Fact("emp-city", ["City"], "Austin", "applicant/employment/0/city",
                                                             section="Employment History 1")],
                           lambda qs: asked.append(qs) or {})
    filler.fill(page, advance_pages=False)
    assert not asked, asked
    assert page.locator("#s").input_value() == "135980" and page.locator("#c").input_value() == "Austin"
