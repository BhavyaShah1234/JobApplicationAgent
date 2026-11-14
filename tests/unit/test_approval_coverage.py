"""F17 / F04 follow-ups: an unread part of the form cannot be approved, legacy attachment links are not invisible, and the file
name a widget shows is part of the approval hash."""
import pytest
from playwright.sync_api import sync_playwright

from jobagent.steps import submit as S
from jobagent.tools import forms


def page_view(html):
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(channel="chrome", headless=True)
        except Exception:
            pytest.skip("no Chrome here")
        try:
            page = browser.new_page()
            page.route("**/*", lambda r: r.fulfill(status=200, content_type="text/html", body=html) if r.request.resource_type == "document" else r.abort())
            page.goto("https://app.test/")
            page.wait_for_timeout(200)
            return page, forms.read_page(page), None
        finally:
            browser.close()


def test_an_unreadable_frame_is_a_live_blocker_at_approval():
    view = forms.PageView([], [], [], [], {}, [], forms.Coverage(unreadable=[{"url": "https://fixture.test/frame", "reason": "unreadable"}]))
    blockers = S.live_blockers(None, view)
    assert blockers and "not fully read" in blockers[0] and "fixture.test/frame" in blockers[0]
    assert S.live_blockers(None, forms.PageView([], [], [], [], {}, [], forms.Coverage())) == []          # a clean page is unaffected


def test_a_closed_region_is_a_live_blocker_too():
    view = forms.PageView([], [], [], [], {}, [], forms.Coverage(opaque=["x-widget"]))
    assert any("cannot see into" in b for b in S.live_blockers(None, view))


def test_two_legacy_attach_links_are_two_upload_fields_not_zero():
    html = ("<h1>My Candidate Profile</h1><div><label>Resume</label><a href='#'>Click here to attach your Resume</a></div>"
            "<div><label>Cover Letter</label><a href='#'>Click here to attach your Cover Letter</a></div>")
    _, view, _ = page_view(html)
    uploads = [f for f in view.fields if f.kind == "file"]
    assert len(uploads) == 2 and uploads[0].label != uploads[1].label
    assert {"resume", "cover letter"} == {("resume" if "esume" in f.label else "cover letter") for f in uploads}


# --- through the real approval hook ---------------------------------------------------------------------------------------

from test_approval_uploads import env_for  # noqa: E402,F401  (fixture)
from test_submission_attempts import PLAIN, clicks, run_review  # noqa: E402,F401


def test_an_earlier_wizard_page_that_could_not_be_read_cannot_be_approved_through(env_for):
    e = env_for(PLAIN)
    blockers = ["page 1: the form was not fully read: a frame could not be read (https://x.test/frame: boom)"]
    out, _ = e.review([], ready=False, blockers=blockers)
    assert out["state"] == "not_ready" and "not fully read" in out["blockers"][0]
    assert clicks(e) == 0                                               # approving did not clear it and nothing was sent


def test_an_unreadable_frame_on_the_final_screen_cannot_reach_the_click(env_for, monkeypatch):
    e = env_for(PLAIN)
    real = forms.read_page

    def with_problem(page):
        v = real(page)
        v.coverage.unreadable.append({"url": "https://x.test/late", "reason": "unreadable"})
        return v
    monkeypatch.setattr(forms, "read_page", with_problem)
    out, _ = e.review([], ready=True, blockers=())
    assert out["state"] == "not_ready" and clicks(e) == 0


def test_the_file_name_a_widget_shows_is_part_of_the_approval_hash(monkeypatch):
    class Frame:
        def evaluate(self, *a, **k):
            raise RuntimeError("the input is gone")
    field = forms.Field(id=1, kind="file", label="Resume/CV", frame=0)
    view = forms.PageView([field], [], [], [], {0: Frame()}, [])
    names = {"now": "resume_v1.pdf"}
    monkeypatch.setattr(forms, "attachment_evidence", lambda *a, **k: names["now"])
    monkeypatch.setattr(forms, "widget_uploads", lambda *a, **k: [])
    first, payload = S.freeze_payload(None, view)
    assert payload["attachments"][0]["observed"] == "resume_v1.pdf" and payload["attachments"][0]["unverified"] == "bytes unreadable"
    names["now"] = "someone_elses_file.pdf"                              # the file under the widget was replaced
    assert S.freeze_payload(None, view)[0] != first
