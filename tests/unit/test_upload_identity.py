"""F28: each upload widget gets its own document, whatever the page does between actions; another widget's file name never verifies it."""
import pytest
from playwright.sync_api import sync_playwright

from jobagent.tools import forms

WIDGETS = """<html><body><h1>Application</h1><div id=list>Documents on file: cover_letter.pdf</div><div id=ws>
<div class=w id=w1><label>Resume/CV</label> <button type=button>Choose file</button> <span class=name></span></div>
<div class=w id=w2><label>Cover Letter</label> <button type=button>Choose file</button> <span class=name></span></div></div>
<script>
const swallow = %(swallow)s;
document.querySelectorAll('.w button').forEach(b => b.onclick = () => {
  const w = b.closest('.w');
  const input = document.createElement('input'); input.type = 'file'; input.style.display = 'none';
  document.body.appendChild(input);                           // the transient input lives OUTSIDE the widget
  input.onchange = () => setTimeout(() => {
    if (swallow && w.id === 'w2') { input.remove(); return; }  // this uploader accepts the file and never shows it
    w.querySelector('.name').textContent = input.files[0].name; input.remove();
    const ws = document.getElementById('ws'); ws.insertBefore(ws.lastElementChild, ws.firstElementChild);   // re-render: the order flips
  }, 300);
  input.click();
});
</script></body></html>"""


def with_widgets(tmp_path, swallow="false"):
    resume, cover = tmp_path / "resume.pdf", tmp_path / "cover_letter.pdf"
    resume.write_bytes(b"%PDF-1.4 resume")
    cover.write_bytes(b"%PDF-1.4 cover")
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(channel="chrome", headless=True)
    except Exception:
        pw.stop()
        pytest.skip("no Chrome here")
    page = browser.new_page()
    page.route("**/*", lambda r: r.fulfill(status=200, content_type="text/html", body=WIDGETS % {"swallow": swallow})
               if r.request.resource_type == "document" else r.abort())
    page.goto("https://app.test/")
    return pw, browser, page, str(resume), str(cover)


def shown(page):
    return {page.locator(f"#{i} label").inner_text(): page.locator(f"#{i} .name").inner_text() for i in ("w1", "w2")}


def test_two_lazy_widgets_with_transient_inputs_and_a_rerender_never_swap_documents(tmp_path):
    pw, browser, page, resume, cover = with_widgets(tmp_path)
    try:
        view = forms.read_page(page)
        uploads = [f for f in view.fields if f.kind == "file"]
        assert len(uploads) == 2 and len({f.label for f in uploads}) == 2                    # two widgets, two identities, no duplicates
        by_role = {("resume" if "esume" in f.label else "cover"): f for f in uploads}
        # fill the COVER LETTER first, with the view read before the page re-ordered itself, then the resume with the same stale view
        forms.fill_field(page, view, by_role["cover"], cover, {})
        forms.fill_field(page, view, by_role["resume"], resume, {})
        assert shown(page) == {"Resume/CV": "resume.pdf", "Cover Letter": "cover_letter.pdf"}
    finally:
        browser.close()
        pw.stop()


def test_a_name_shown_elsewhere_on_the_page_does_not_verify_a_widget_that_swallowed_its_file(tmp_path):
    pw, browser, page, resume, cover = with_widgets(tmp_path, swallow="true")
    try:
        view = forms.read_page(page)
        cover_field = next(f for f in view.fields if f.kind == "file" and "Cover" in f.label)
        # "cover_letter.pdf" is printed in the page's document list, but the Cover Letter widget never shows it
        real = page.wait_for_timeout
        page.wait_for_timeout = lambda ms: real(min(ms, 500))      # longer than the page's 300 ms upload timer
        with pytest.raises(forms.FillError, match="never showed it"):
            forms.fill_field(page, view, cover_field, cover, {})
    finally:
        browser.close()
        pw.stop()


def test_an_upload_that_is_not_exactly_one_control_now_is_not_attached(tmp_path):
    pw, browser, page, resume, cover = with_widgets(tmp_path)
    try:
        view = forms.read_page(page)
        cover_field = next(f for f in view.fields if f.kind == "file" and "Cover" in f.label)
        page.evaluate("document.getElementById('w2').remove()")                           # the widget vanished since the read
        with pytest.raises(forms.FillError, match="not exactly one control"):
            forms.fill_field(page, view, cover_field, cover, {})
        assert page.locator("#w1 .name").inner_text() == ""                                  # and nothing went into the other widget
    finally:
        browser.close()
        pw.stop()


def with_retained_inputs(tmp_path):
    """The widgets fixture, but the chooser-created input STAYS attached outside its widget after the upload."""
    resume, cover = tmp_path / "resume.pdf", tmp_path / "cover_letter.pdf"
    resume.write_bytes(b"%PDF-1.4 resume")
    cover.write_bytes(b"%PDF-1.4 cover")
    html = (WIDGETS % {"swallow": "false"}).replace("input.remove();", "")
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(channel="chrome", headless=True)
    except Exception:
        pw.stop()
        pytest.skip("no Chrome here")
    page = browser.new_page()
    page.route("**/*", lambda r: r.fulfill(status=200, content_type="text/html", body=html) if r.request.resource_type == "document" else r.abort())
    page.goto("https://app.test/")
    return pw, browser, page, str(resume), str(cover)


def test_a_retained_native_input_is_the_same_upload_as_its_trigger(tmp_path):
    pw, browser, page, resume, cover = with_retained_inputs(tmp_path)
    try:
        view = forms.read_page(page)
        records = lambda: sorted((f.label, f.slot) for f in forms.read_page(page).fields if f.kind == "file")   # noqa: E731
        assert records() == [("Cover Letter", 0), ("Resume/CV", 0)]
        forms.fill_field(page, view, next(f for f in view.fields if f.kind == "file" and "Resume" in f.label), resume, {})
        assert page.locator("input[type=file]").count() == 1                                  # the page did keep the hidden input
        assert records() == [("Cover Letter", 0), ("Resume/CV", 0)]                            # ... and it is still two uploads, not three
        view = forms.read_page(page)                                                           # (the page reordered its widgets meanwhile)
        forms.fill_field(page, view, next(f for f in view.fields if f.kind == "file" and "Cover" in f.label), cover, {})
        assert records() == [("Cover Letter", 0), ("Resume/CV", 0)]
        assert shown(page) == {"Resume/CV": "resume.pdf", "Cover Letter": "cover_letter.pdf"}
    finally:
        browser.close()
        pw.stop()


def test_merging_needs_exactly_one_trigger_with_the_caption():
    mk = lambda i, linked=False, label="Attachment": forms.Field(id=i, kind="file", label=label, frame=0, linked=linked)   # noqa: E731
    two_triggers = [mk(1), mk(2), mk(3, linked=True)]
    assert len(forms.merge_linked_uploads(two_triggers)) == 3          # the linked input's owner is unknown: nothing merged
    one_trigger = [mk(1), mk(3, linked=True), mk(4, label="Cover Letter")]
    assert [g.id for g in forms.merge_linked_uploads(one_trigger)] == [1, 4]
    only_native = [mk(3, linked=True)]                                  # the trigger is gone (re-rendered): the input IS the widget
    assert forms.merge_linked_uploads(only_native) == only_native
