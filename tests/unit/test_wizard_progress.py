"""F16: a wizard step is recognised by its address, step marker and fields - never by its heading alone."""
import pytest
from playwright.sync_api import sync_playwright

from jobagent.tools import forms, wizard
from jobagent.tools.wizard import PageState, judge


def st(url="https://x.test/apply", step=(), fields=(), buttons=("next",), headings=("Application",)):
    return PageState(url, tuple(step), frozenset(("text", f, "") for f in fields), frozenset(buttons), tuple(headings))


# --- the decision, on its own ---------------------------------------------------------------------------------------------

def test_the_same_heading_and_address_with_new_questions_is_a_new_step():
    assert judge(st(fields=["first name", "last name"]), st(fields=["email", "phone"]))[0] == "advanced"


def test_a_single_question_replaced_by_another_is_a_new_step():
    assert judge(st(fields=["name"]), st(fields=["email"]))[0] == "advanced"


def test_a_conditional_question_appearing_or_vanishing_is_the_same_step():
    base = ["a", "b", "c", "d"]
    assert judge(st(fields=base), st(fields=base + ["why other?"]))[0] == "same"
    assert judge(st(fields=base + ["why other?"]), st(fields=base))[0] == "same"


def test_a_changed_heading_over_the_same_questions_is_not_progress():
    assert judge(st(fields=["a", "b"], headings=["Your details"]), st(fields=["a", "b"], headings=["Please fix the errors below"]))[0] == "same"


def test_the_address_or_the_step_marker_changing_is_progress_even_with_the_same_questions():
    assert judge(st(fields=["a"]), st(url="https://x.test/apply?step=2", fields=["a"]))[0] == "advanced"
    assert judge(st(url="https://x.test/apply#/one", fields=["a"]), st(url="https://x.test/apply#/two", fields=["a"]))[0] == "advanced"
    assert judge(st(step=["current:Step 1"], fields=["a"]), st(step=["current:Step 2"], fields=["a"]))[0] == "advanced"


def test_questions_vanishing_into_a_review_page_advances_and_into_nothing_is_still_loading():
    assert judge(st(fields=["a", "b"]), st(fields=[], buttons=("submit application",)))[0] == "advanced"
    assert judge(st(fields=["a", "b"]), st(fields=[], buttons=()))[0] == "loading"


def test_tracking_noise_does_not_look_like_a_new_address():
    assert wizard.url_key("https://X.test/apply/") == wizard.url_key("https://x.test/apply")
    assert wizard.url_key("https://x.test/a?b=1&c=2") == wizard.url_key("https://x.test/a?c=2&b=1")
    assert wizard.url_key("https://x.test/a?step=1") != wizard.url_key("https://x.test/a?step=2")


# --- real pages ------------------------------------------------------------------------------------------------------------

def test_error_summary_does_not_rename_unchanged_questionnaire_fields():
    """A conditional question/error summary is not a new application page."""
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='chrome', headless=True, args=['--no-sandbox'])
        page = browser.new_page()
        page.set_content('''<h1>Application</h1><h2>Application Questions</h2>
            <div id="errors"></div>
            <label>Work authorization<input id="authorization"></label>
            <label>Citizenship<input id="citizenship"></label>
            <div id="followup"></div><button>Save and Continue</button>''')
        before = wizard.state_of(page, forms.read_page(page))
        page.locator('#errors').evaluate("e => e.innerHTML='<h2>Errors Found</h2><p>Please answer the follow-up</p>'")
        page.locator('#followup').evaluate("e => e.innerHTML='<label>Visa status<input id=visa required></label>'")
        view = forms.read_page(page)
        assert {f.section for f in view.fields} == {'Application Questions'}
        assert judge(before, wizard.state_of(page, view))[0] == 'same'
        page.set_content('<h2>Error Analysis Experience</h2><label>Describe your work<textarea></textarea></label>')
        assert forms.read_page(page).fields[0].section == 'Error Analysis Experience'
        browser.close()


WIZARD = """<html><body><h1>Job application</h1><div id=steps></div><div id=err style="color:red"></div>
<script>
const steps = [
  [['First name','text'],['Last name','text']],
  [['Email address','email'],['Phone number','tel']],
  [['Cover note','text'],['Portfolio link','url']],
];
let i = 0, mode = %(mode)r;
function render() {
  document.getElementById('steps').innerHTML = steps[i].map(([l,t]) => '<label>'+l+' <input type='+t+' name='+l.replace(/ /g,'_')+'></label>').join('<br>')
    + '<br><button id=next type=button>Next</button>';
  document.getElementById('next').onclick = () => {
    if (mode === 'refuse') { document.getElementById('err').textContent = 'Please complete the required fields'; return; }
    setTimeout(() => { i = Math.min(i + 1, steps.length - 1); if (mode === 'path') history.pushState({}, '', '/step' + (i+1)); render(); }, 150);
  };
}
render();
</script></body></html>"""


def with_page(html, fn):
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(channel="chrome", headless=True)
        except Exception:
            pytest.skip("no Chrome here")
        try:
            page = browser.new_page()
            page.route("**/*", lambda r: r.fulfill(status=200, content_type="text/html", body=html) if r.request.resource_type == "document" else r.abort())
            page.goto("https://wiz.test/apply")
            return fn(page)
        finally:
            browser.close()


def click_next_and_wait(page):
    view = forms.read_page(page)
    before = wizard.state_of(page, view)
    forms.click_button(page, view, forms.NEXT)
    return wizard.wait_for_progress(page, before, attempts=4, wait_ms=250, settle_ms=100)


def test_three_steps_with_one_heading_and_one_address_are_each_recognised_as_progress():
    def go(page):
        urls = {page.url}
        out = []
        for _ in range(2):
            verdict, view, _, why = click_next_and_wait(page)
            out.append((verdict, sorted(f.label for f in view.fields)))
            urls.add(page.url)
        assert len(urls) == 1                                            # the address never changed
        return out
    got = with_page(WIZARD % {"mode": "plain"}, go)
    assert got[0][0] == "advanced" and "Email address" in " ".join(got[0][1])
    assert got[1][0] == "advanced" and any("Cover note" in x for x in got[1][1])


def test_a_wizard_that_changes_the_path_per_step_is_recognised_too():
    verdict, view, _, why = with_page(WIZARD % {"mode": "path"}, click_next_and_wait)
    assert verdict == "advanced"


def test_a_refused_next_is_reported_as_the_same_step():
    verdict, view, _, why = with_page(WIZARD % {"mode": "refuse"}, click_next_and_wait)
    assert verdict == "same" and "same" in why
