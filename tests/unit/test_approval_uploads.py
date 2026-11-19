"""F03 follow-up (independent verification): approving must re-check required uploads against what is attached NOW."""
import json
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from jobagent import db
from jobagent.steps import submit as S
from jobagent.tools import forms

JD = {"company": "Acme", "title": "Engineer", "apply_url": "https://example.test/apply", "source_url": "https://example.test/apply"}

# Codex's fixture: a prefilled required email, a required resume upload, and a JavaScript-driven final button
CODEX = ("<h1>Job application</h1><form>"
         "<label>Email <input type=\"email\" name=\"email\" value=\"me@example.org\" required></label>"
         "<label>Resume <input type=\"file\" name=\"resume\" required></label>"
         "<button type=\"button\" onclick=\"window.sent = true\">Submit application</button></form>")
OPTIONAL_UPLOAD = CODEX.replace(" required></label><button", "></label><button").replace('name="resume" required', 'name="resume"')
# an uploader that replaces its input with the attached file's name once a file is chosen
WIDGET = ("<h1>Job application</h1><form><label>Email <input type=\"email\" name=\"email\" value=\"me@example.org\" required></label>"
          "<div id=\"up\"><span>Resume / CV *</span> <input type=\"file\" id=\"f\" required "
          "onchange=\"const s=document.createElement('span');s.textContent=this.files[0].name;this.replaceWith(s)\"></div>"
          "<button type=\"button\" onclick=\"window.sent = true\">Submit application</button></form>")
# a chip that shows the filename of a previously attached file next to the caption
ATTACHED_CHIP = ("<h1>Job application</h1><form><label>Email <input type=\"email\" name=\"email\" value=\"me@example.org\" required></label>"
                 "<div><span>Resume / CV *</span> <span class=chip>my_resume.pdf</span></div>"
                 "<button type=\"button\" onclick=\"window.sent = true\">Submit application</button></form>")


class Env:
    def __init__(self, tmp_path, monkeypatch, html, answer="approve", routes=None):
        self.pw = sync_playwright().start()
        try:
            self.browser = self.pw.chromium.launch(channel="chrome", headless=True)
        except Exception:
            self.pw.stop()
            pytest.skip("no Chrome here")
        self.page = self.browser.new_page()
        served = routes or {}
        self.page.route("**/*", lambda r: r.fulfill(status=200, content_type="text/html", body=served[r.request.url])
                        if r.request.url in served else r.abort())
        self.page.set_content(html)
        if served:
            self.page.wait_for_timeout(600)                  # let the iframes load
        self.job = tmp_path / "outputs" / "acme" / "1"
        (self.job / "application").mkdir(parents=True)
        self.conn = db.connect(tmp_path / "jobs.sqlite")
        self.prompts = []
        env = self

        class Hitl:
            def ask(self, prompt, timeout_s=0):
                env.prompts.append(prompt)
                if env.before_answer:
                    env.before_answer()          # the candidate acts in noVNC before answering
                return answer
        self.hitl = Hitl()
        self.before_answer = None
        real_wait = self.page.wait_for_timeout
        self.page.wait_for_timeout = lambda ms: real_wait(min(ms, 100))
        monkeypatch.setattr(S, "submitted_page", lambda page, before, shot, timeout_s=60, **kwargs: (True, "thanks", []))
        monkeypatch.setattr(S, "wait_for_confirmation", lambda *a, **k: None)
        self.hook = S.make_at_review(self.job, JD, self.hitl, None, self.conn, lambda m: None, mail_s=0)

    def review(self, records, ready=False, blockers=("Resume (page 1): required, could not be filled",)):
        result = {"state": "review", "fields": records, "verified": 1, "pages": [], "url": "u",
                  "ready_to_submit": ready, "blockers": list(blockers)}
        return self.hook(self.page, forms.read_page(self.page), result), result

    def sent(self):
        return self.page.evaluate("!!window.sent")

    def attach(self, name="my_resume.pdf", where="input[type=file]", frame=None):
        pdf = self.job / name
        pdf.write_bytes(b"%PDF-1.4 fixture")
        (frame or self.page).locator(where).first.set_input_files(str(pdf))

    def close(self):
        self.browser.close()
        self.pw.stop()


def upload_record(outcome, label="Resume / Resume / resume", required=True, **extra):
    return {"page": 1, "label": label, "kind": "file", "required": required, "outcome": outcome, "verified": outcome == "verified",
            "filled": "", "answer": "resume", "flag": "no resume file in the job folder", "error": "", "readback": "", **extra}


@pytest.fixture
def env_for(tmp_path, monkeypatch):
    made = []

    def make(html, **kw):
        e = Env(tmp_path, monkeypatch, html, **kw)
        made.append(e)
        return e
    yield make
    for e in made:
        e.close()


# --- Codex's two reproductions -------------------------------------------------------------------------------------------

def test_a_missing_resume_file_in_the_job_folder_cannot_be_approved_past(env_for):
    e = env_for(CODEX)
    out, result = e.review([upload_record("mismatch")])
    assert out["state"] == "not_ready" and any("resume" in b.lower() for b in out["blockers"])
    assert e.sent() is False, "the final button's handler ran with no resume attached"
    assert "NOT READY" in e.prompts[0]


def test_a_failed_upload_cannot_be_approved_past(env_for):
    e = env_for(CODEX)
    out, _ = e.review([upload_record("failed", error="FillError: fixture upload rejected", flag="")])
    assert out["state"] == "not_ready" and e.sent() is False


# --- what clears a blocker, and what never was one -------------------------------------------------------------------------

def test_a_successful_manual_upload_after_the_failure_clears_the_blocker(env_for):
    e = env_for(CODEX)
    e.before_answer = e.attach                       # the candidate attaches the file in noVNC, then answers approve
    out, result = e.review([upload_record("failed", error="FillError: fixture upload rejected")])
    assert out["state"] == "submitted" and e.sent() is True
    assert result["ready_to_submit"] is True and result["blockers"] == []        # what was actually sent is described truthfully


def test_the_old_record_is_not_trusted_if_the_file_was_removed_afterwards(env_for):
    e = env_for(CODEX)
    # the record says verified, but nothing is attached now (the candidate removed it, or the page cleared it)
    out, _ = e.review([upload_record("verified")], ready=True, blockers=())
    assert out["state"] == "not_ready" and e.sent() is False


def test_an_optional_empty_upload_does_not_block(env_for):
    e = env_for(OPTIONAL_UPLOAD)
    out, _ = e.review([upload_record("left empty (optional)", required=False)], ready=True, blockers=())
    assert out["state"] == "submitted" and e.sent() is True


def test_an_uploader_that_replaced_its_input_with_the_filename_counts_as_attached(env_for):
    e = env_for(WIDGET)
    e.before_answer = e.attach
    out, _ = e.review([upload_record("failed", error="x")])
    assert out["state"] == "submitted" and e.sent() is True


def test_an_uploader_that_still_shows_an_empty_input_blocks(env_for):
    e = env_for(WIDGET)
    out, _ = e.review([upload_record("failed", error="x")])          # nothing attached, input still there
    assert out["state"] == "not_ready" and e.sent() is False


def test_an_attachment_chip_next_to_the_caption_is_evidence_even_without_an_input(env_for):
    e = env_for(ATTACHED_CHIP)
    out, _ = e.review([upload_record("verified", filled="/app/outputs/acme/1/resume.pdf")], ready=True, blockers=())
    assert out["state"] == "submitted" and e.sent() is True


def test_a_filename_elsewhere_on_the_page_is_not_evidence_for_this_upload(env_for):
    html = ATTACHED_CHIP.replace("<span class=chip>my_resume.pdf</span>", "") + "<p><a href=#>privacy-policy.pdf</a></p>"
    e = env_for(html)
    out, _ = e.review([upload_record("failed", error="x")])
    assert out["state"] == "not_ready" and e.sent() is False


def test_the_exit_code_for_an_approved_but_not_ready_application_is_5(env_for, monkeypatch):
    from jobagent.steps.apply import EXIT_NOT_READY, exit_code
    e = env_for(CODEX)
    out, result = e.review([upload_record("mismatch")])
    result["submission"] = out
    assert out["state"] == "not_ready" and exit_code(result) == EXIT_NOT_READY


# --- required-ness the page only shows with a star lookalike (Lever marks its fields with U+2731) -------------------------

LEVER_STYLE = ("<h1>Job application</h1><form><label>Email <input type=\"email\" name=\"email\" value=\"me@example.org\" required></label>"
               "<div><span>Resume/CV ✱</span> <input type=\"file\" name=\"resume\"></div>"
               "<button type=\"button\" onclick=\"window.sent = true\">Submit application</button></form>")


@pytest.mark.parametrize("mark", ["✱", "*", "∗", "＊", "(required)"])
def test_a_star_lookalike_marks_an_upload_required(env_for, mark):
    e = env_for(LEVER_STYLE.replace("✱", mark))
    up = next(f for f in forms.read_page(e.page).fields if f.kind == "file")
    assert up.required is True
    out, _ = e.review([], ready=True, blockers=())                       # no record at all: the live page decides
    assert out["state"] == "not_ready" and e.sent() is False


def test_a_field_without_any_required_mark_stays_optional(env_for):
    e = env_for(LEVER_STYLE.replace(" ✱", ""))
    assert next(f for f in forms.read_page(e.page).fields if f.kind == "file").required is False
    out, _ = e.review([], ready=True, blockers=())
    assert out["state"] == "submitted"


# --- F03 second verification: evidence belongs to ONE upload control (Codex's two-upload fixtures) ---------------------------

TWO_SEPARATE = ("<h1>Job application</h1><form><label>Email <input type=\"email\" value=\"me@example.org\" required></label>"
                "<div class=resume><label>Resume <input type=\"file\" name=\"resume\" required></label></div>"
                "<div class=cover><label>Cover letter <input type=\"file\" name=\"cover_letter\" required></label></div>"
                "<button type=\"button\" onclick=\"window.sent = true\">Submit application</button></form>")
# the resume is shown (a name next to it); the cover letter's input is empty
SHARED_DIV = ("<h1>Job application</h1><form><label>Email <input type=\"email\" value=\"me@example.org\" required></label>"
              "<div class=\"documents\"><div><label>Resume <input type=\"file\" name=\"resume\" required></label>"
              "<span>candidate_resume.pdf</span></div>"
              "<div><label>Cover letter <input type=\"file\" name=\"cover_letter\" required></label></div></div>"
              "<button type=\"button\" onclick=\"window.sent = true\">Submit application</button></form>")
BOTH_RECORDS = lambda: [upload_record("verified", label="Resume / Resume / resume", filled="/app/outputs/acme/1/resume.pdf"),  # noqa: E731
                        upload_record("failed", label="Cover letter / Cover letter / cover_letter", error="FillError: rejected")]


def test_the_resume_does_not_stand_in_for_an_empty_cover_letter_when_its_name_is_in_the_job_folder(env_for):
    e = env_for(TWO_SEPARATE)
    (e.job / "resume.pdf").write_bytes(b"%PDF-1.4 fixture")                    # a known job-folder PDF
    e.page.evaluate("document.querySelector('.resume label').insertAdjacentHTML('afterend', '<span>resume.pdf</span>')")
    e.attach(where="input[name=resume]")
    out, result = e.review(BOTH_RECORDS())
    assert out["state"] == "not_ready" and e.sent() is False
    assert result["ready_to_submit"] is False
    assert len(out["blockers"]) == 1 and "cover letter" in out["blockers"][0].lower()      # only the cover letter
    assert "resume" not in out["blockers"][0].lower()


def test_an_attachment_in_a_neighbouring_widget_under_a_shared_div_is_not_the_cover_letters(env_for):
    e = env_for(SHARED_DIV)                                                    # no PDFs in the job folder at all
    e.attach("candidate_resume.pdf", where="input[name=resume]")
    out, _ = e.review(BOTH_RECORDS())
    assert out["state"] == "not_ready" and e.sent() is False
    assert len(out["blockers"]) == 1 and "cover letter" in out["blockers"][0].lower()


def test_the_evidence_function_itself_refuses_the_neighbours_name(env_for):
    e = env_for(SHARED_DIV)
    e.attach("candidate_resume.pdf", where="input[name=resume]")
    view = forms.read_page(e.page)
    cover = next(f for f in view.fields if f.kind == "file" and "cover" in f.label.lower())
    resume = next(f for f in view.fields if f.kind == "file" and "resume" in f.label.lower())
    others = {"cover": ["resume"], "resume": ["cover letter"]}
    assert forms.attachment_evidence(e.page, view, cover, others["cover"]) == ""
    assert forms.attachment_evidence(e.page, view, resume, others["resume"]) != ""


def test_attaching_the_cover_letter_manually_clears_only_its_blocker_and_allows_the_send(env_for):
    e = env_for(SHARED_DIV)
    e.attach("candidate_resume.pdf", where="input[name=resume]")
    e.before_answer = lambda: e.attach("my_cover.pdf", where="input[name=cover_letter]")
    out, result = e.review(BOTH_RECORDS())
    assert out["state"] == "submitted" and e.sent() is True
    assert result["ready_to_submit"] is True and result["blockers"] == []


def test_both_uploads_missing_reports_both_blockers_separately(env_for):
    e = env_for(TWO_SEPARATE)
    out, _ = e.review(BOTH_RECORDS())
    assert out["state"] == "not_ready" and len(out["blockers"]) == 2


def test_an_attachment_in_a_different_frame_is_not_evidence_for_a_control_in_this_one(env_for):
    frame_html = ("<h1>Documents</h1><label>Resume <input type=\"file\" name=\"resume\" required></label>"
                  "<span>candidate_resume.pdf</span>")
    main = ("<h1>Job application</h1><form><label>Email <input type=\"email\" value=\"me@example.org\" required></label>"
            "<iframe src=\"https://docs.example.test/resume\" width=\"500\" height=\"120\"></iframe>"
            "<label>Cover letter <input type=\"file\" name=\"cover_letter\" required></label>"
            "<button type=\"button\" onclick=\"window.sent = true\">Submit application</button></form>")
    e = env_for(main, routes={"https://docs.example.test/resume": frame_html})
    frame = next(f for f in e.page.frames if "docs.example.test" in f.url)
    e.attach("candidate_resume.pdf", where="input[name=resume]", frame=frame)
    out, _ = e.review([upload_record("verified", label="Resume / Resume / resume"),
                       upload_record("failed", label="Cover letter / Cover letter / cover_letter", error="x")])
    assert out["state"] == "not_ready" and e.sent() is False
    assert len(out["blockers"]) == 1 and "cover letter" in out["blockers"][0].lower()


def test_when_the_association_cannot_be_established_the_blocker_stays(env_for):
    # two captions that both look like "Resume": the widget's own caption is ambiguous, so no name near either is evidence
    html = ("<h1>Job application</h1><form><label>Email <input type=\"email\" value=\"me@example.org\" required></label>"
            "<div><span>Resume</span></div><div><span>Resume</span> <span>x_resume.pdf</span></div>"
            "<button type=\"button\" onclick=\"window.sent = true\">Submit application</button></form>")
    e = env_for(html)
    out, _ = e.review([upload_record("failed", error="x")])               # the input is gone; two equal captions on the page
    assert out["state"] == "not_ready" and e.sent() is False


# --- F03 third verification: distinct uploads must never be merged by a shared native name -----------------------------------

SAME_NAME = ("<h1>Job application</h1><form><label>Email <input type=\"email\" value=\"me@example.org\" required></label>"
             "<div><label>Cover letter <input type=\"file\" name=\"file\" required></label></div>"
             "<div><label>Additional document <input type=\"file\" name=\"file\"></label></div>"
             "<button type=\"button\" onclick=\"window.sent = true\">Submit application</button></form>")
SAME_NAME_REVERSED = SAME_NAME.replace(
    "<div><label>Cover letter <input type=\"file\" name=\"file\" required></label></div>"
    "<div><label>Additional document <input type=\"file\" name=\"file\"></label></div>",
    "<div><label>Additional document <input type=\"file\" name=\"file\"></label></div>"
    "<div><label>Cover letter <input type=\"file\" name=\"file\" required></label></div>")


def pair_records(*specs):
    return [{**upload_record("failed" if req else "left empty (optional)", label=f"{cap} / {cap} / file", required=req,
                             error="x" if req else ""), "frame": 0, "slot": i} for i, (cap, req) in enumerate(specs)]


def test_a_required_and_an_optional_upload_sharing_a_name_keep_their_own_flags(env_for):
    e = env_for(SAME_NAME)
    view = forms.read_page(e.page)
    ups = [f for f in view.fields if f.kind == "file"]
    assert [(f.slot, f.required) for f in ups] == [(0, True), (1, False)]
    out, _ = e.review(pair_records(("Cover letter", True), ("Additional document", False)))
    assert out["state"] == "not_ready" and e.sent() is False
    assert len(out["blockers"]) == 1 and "cover letter" in out["blockers"][0].lower()


def test_the_same_pair_in_reversed_dom_order(env_for):
    e = env_for(SAME_NAME_REVERSED)
    out, _ = e.review(pair_records(("Additional document", False), ("Cover letter", True)))
    assert out["state"] == "not_ready" and e.sent() is False and "cover letter" in out["blockers"][0].lower()


def test_attaching_the_optional_one_does_not_satisfy_the_required_one(env_for):
    e = env_for(SAME_NAME)
    e.attach("extra.pdf", where="div:nth-of-type(2) input[type=file]")
    out, _ = e.review(pair_records(("Cover letter", True), ("Additional document", False)))
    assert out["state"] == "not_ready" and e.sent() is False


def test_attaching_the_required_one_clears_it(env_for):
    e = env_for(SAME_NAME)
    e.before_answer = lambda: e.attach("cover.pdf", where="input[type=file]")
    out, result = e.review(pair_records(("Cover letter", True), ("Additional document", False)))
    assert out["state"] == "submitted" and e.sent() is True and result["blockers"] == []


def test_two_frames_with_the_same_native_name_both_stay_in_the_lookup(env_for):
    frame_html = "<h1>Documents</h1><label>Resume <input type=\"file\" name=\"file\" required></label><span>candidate_resume.pdf</span>"
    main = ("<h1>Job application</h1><form><label>Email <input type=\"email\" value=\"me@example.org\" required></label>"
            "<iframe src=\"https://docs.example.test/resume\" width=\"500\" height=\"120\"></iframe>"
            "<label>Cover letter <input type=\"file\" name=\"file\" required></label>"
            "<button type=\"button\" onclick=\"window.sent = true\">Submit application</button></form>")
    e = env_for(main, routes={"https://docs.example.test/resume": frame_html})
    frame = next(f for f in e.page.frames if "docs.example.test" in f.url)
    e.attach("candidate_resume.pdf", where="input[type=file]", frame=frame)
    view = forms.read_page(e.page)
    assert sorted((f.frame, f.slot) for f in view.fields if f.kind == "file") == [(0, 0), (1, 0)]
    recs = [{**upload_record("verified", label="Resume / Resume / file"), "frame": 1, "slot": 0},
            {**upload_record("failed", label="Cover letter / Cover letter / file", error="x"), "frame": 0, "slot": 0}]
    out, _ = e.review(recs)
    assert out["state"] == "not_ready" and e.sent() is False
    assert len(out["blockers"]) == 1 and "cover letter" in out["blockers"][0].lower()


def test_apply_keeps_one_outcome_record_per_discovered_upload():
    from jobagent.steps import apply as A
    mk = lambda i, slot: forms.Field(id=i, kind="file", label="Cover letter / Cover letter / file", required=True, slot=slot)  # noqa: E731
    keys = {A._key(mk(1, 0)), A._key(mk(2, 1))}
    assert len(keys) == 2
    assert A._blank_entry(mk(2, 1), 1, "x")["slot"] == 1


# --- F03 fourth verification: a position is not an identity; records must not join a different control --------------------------

STAR = SAME_NAME.replace("Cover letter <input", "Cover letter * <input")


def real_records(e):
    """Records as the fill step writes them: from the real field read, with frame and slot."""
    return [{**upload_record("failed" if f.required else "left empty (optional)", label=f.label, required=f.required,
                             error="x"), "frame": f.frame, "slot": f.slot} for f in forms.read_page(e.page).fields if f.kind == "file"]


def test_a_removed_first_input_does_not_let_the_optional_control_absorb_the_required_record(env_for):    # Codex's reproduction
    e = env_for(STAR)
    recs = real_records(e)
    assert [r["required"] for r in recs] == [True, False]
    e.before_answer = lambda: e.page.locator("input[type=file]").first.evaluate("x => x.remove()")
    out, result = e.review(recs)
    assert out["state"] == "not_ready" and e.sent() is False and result["ready_to_submit"] is False
    assert any("cover letter" in b.lower() for b in out["blockers"])


def test_the_required_input_replaced_by_a_widget_with_no_filename_keeps_the_blocker(env_for):
    e = env_for(STAR)
    recs = real_records(e)
    e.before_answer = lambda: e.page.locator("input[type=file]").first.evaluate(
        "x => x.replaceWith(Object.assign(document.createElement('button'), {textContent: 'Choose'}))")
    out, _ = e.review(recs)
    assert out["state"] == "not_ready" and e.sent() is False


def test_the_required_input_replaced_by_its_own_filename_clears_the_blocker(env_for):
    e = env_for(STAR)
    recs = real_records(e)
    e.before_answer = lambda: e.page.locator("input[type=file]").first.evaluate(
        "x => x.replaceWith(Object.assign(document.createElement('span'), {textContent: 'my_cover_letter.pdf'}))")
    out, result = e.review(recs)
    assert out["state"] == "submitted" and e.sent() is True and result["blockers"] == []


def test_the_page_reordering_the_two_controls_keeps_each_flag_with_its_own_control(env_for):
    e = env_for(STAR)
    recs = real_records(e)
    e.before_answer = lambda: e.page.evaluate(
        "() => { const f = document.querySelector('form'); const [a, b] = f.querySelectorAll('div'); f.insertBefore(b, a); }")
    out, _ = e.review(recs)
    assert out["state"] == "not_ready" and e.sent() is False and "cover letter" in out["blockers"][0].lower()


def test_a_same_named_input_inserted_before_the_original_does_not_shift_the_record_onto_it(env_for):
    e = env_for(STAR)
    recs = real_records(e)
    e.before_answer = lambda: e.page.evaluate(
        "() => document.querySelector('form div').insertAdjacentHTML('beforebegin', "
        "'<div><label>Portfolio <input type=file name=file></label></div>')")
    out, _ = e.review(recs)
    assert out["state"] == "not_ready" and e.sent() is False and "cover letter" in out["blockers"][0].lower()


def test_a_removed_input_before_the_original_leaves_it_judged_by_its_own_caption_and_attachment(env_for):
    e = env_for(SAME_NAME_REVERSED.replace("Cover letter <input", "Cover letter * <input"))
    recs = real_records(e)                                   # optional first (slot 0), required second (slot 1)
    e.page.locator("input[type=file]").nth(1).set_input_files(str(e.job / "x.pdf")) if (e.job / "x.pdf").write_bytes(b"%PDF") else None
    e.before_answer = lambda: e.page.locator("input[type=file]").first.evaluate("x => x.remove()")
    out, _ = e.review(recs)                                  # the attached required control moved to slot 0 and stays itself
    assert out["state"] == "submitted" and e.sent() is True
