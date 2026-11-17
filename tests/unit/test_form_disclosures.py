"""Hidden sections are opened through their UI; submit controls and answers stay untouched."""
from test_generic_forms import page
from jobagent.tools import disclosures, forms


def test_expand_all_link_opens_form_without_clicking_default_submit_headings(page):
    page.set_content('''<form onsubmit="window.sent=true;return false">
      <a role=button onclick="panel.hidden=false;heading.setAttribute('aria-expanded','true')">Expand all sections</a>
      <section><h2><button id=heading aria-expanded=false onclick="window.headingClicked=true">Profile Information</button></h2>
      <div id=panel hidden><label>Email<input type=email value=owner@example.test></label></div></section>
      <button type=submit>Apply</button></form>''')
    before = forms.read_page(page)
    assert not before.coverage.complete and before.coverage.collapsed
    assert disclosures.expand(page, before)
    after = forms.read_page(page)
    assert after.coverage.complete and after.fields[0].value == 'owner@example.test'
    assert not page.evaluate('!!window.sent || !!window.headingClicked')
    assert not disclosures.expand(page, after)


def test_standard_accordion_and_details_are_opened_without_toggling_answers(page):
    page.set_content('''<form><h2><button type=button aria-expanded=false aria-controls=panel
      onclick="panel.hidden=false;this.setAttribute('aria-expanded','true')">Contact</button></h2>
      <div id=panel hidden><label>Email<input type=email value=owner@example.test></label></div>
      <details><summary>Questions</summary><label>Consent<input type=checkbox></label></details></form>''')
    for _ in range(3):
        if not disclosures.expand(page, forms.read_page(page)):break
    view = forms.read_page(page)
    assert view.coverage.complete and len(view.fields) == 2
    assert not page.locator('input[type=checkbox]').is_checked()


def test_a_default_submit_heading_remains_blocked_without_a_safe_disclosure_control(page):
    page.set_content('''<form onsubmit="window.sent=true;return false"><section>
      <h2><button aria-expanded=false>Profile</button></h2>
      <div hidden><label>Email<input type=email required></label></div></section></form>''')
    view = forms.read_page(page)
    assert not disclosures.expand(page, view)
    assert not forms.read_page(page).coverage.complete
    assert not page.evaluate('!!window.sent')


def test_navigation_language_is_ignored_but_candidate_language_question_remains(page):
    page.set_content('''<div class=userInfoMenu><a role=combobox aria-label=Language tabindex=0>English US</a></div>
      <section><label for=language>Primary language</label><input id=language></section>''')
    assert [f.label for f in forms.read_page(page).fields] == ['Primary language']


def test_stale_collapsed_attribute_does_not_close_a_visible_panel(page):
    page.set_content('''<button type=button aria-expanded=false aria-controls=panel
      onclick="panel.hidden=true;window.closed=true">Contact</button>
      <div id=panel><label>Email<input type=email value=owner@example.test></label></div>''')
    view = forms.read_page(page)
    assert view.coverage.complete and not disclosures.expand(page, view)
    assert not page.evaluate('!!window.closed')


def test_icon_uploads_use_their_own_label_references_and_submit_buttons_are_not_upload_actions(page):
    page.set_content('''<form><div><span id=resumeLabel>Resume *</span>
      <span id=resumeAction hidden>Upload a Resume Opens a dialog</span>
      <span role=button tabindex=0 aria-labelledby="resumeLabel resumeAction">+</span></div>
      <div><span id=coverLabel>Cover letter</span><span id=coverAction hidden>Attach a Cover Letter Opens a dialog</span>
      <span role=button tabindex=0 aria-labelledby="coverLabel coverAction">+</span></div>
      <button type=submit aria-label="Upload a Resume">Finish</button></form>''')
    files=[f for f in forms.read_page(page).fields if f.kind=='file']
    assert len(files)==2 and files[0].required and not files[1].required
    assert 'Resume' in files[0].label and 'Cover letter' in files[1].label
