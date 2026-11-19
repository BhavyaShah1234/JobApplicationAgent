"""Embedded form scrolling and media controls exercise real DOM behavior."""
import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from test_generic_forms import page
from jobagent.tools import forms,scrape


def test_media_shadow_controls_are_excluded_without_hiding_an_applicant_slider(page):
    page.set_content('''<media-controller id=player></media-controller>
      <label for=travel>Willingness to travel<input id=travel type=range></label>
      <script>player.attachShadow({mode:'open'}).innerHTML=
        '<media-time-range id=time></media-time-range>';
        player.shadowRoot.getElementById('time').attachShadow({mode:'open'}).innerHTML=
        '<input type=range aria-label="Progress scrubber">';</script>''')
    fields=forms.read_page(page).fields
    assert [f.label for f in fields]==['Willingness to travel']
    assert fields[0].kind=='range'


def test_primary_heading_retains_grade_and_separates_a_nested_location_heading(page):
    page.set_content('''<h1><span>Software Engineer 3<span style="display:none">Old title</span><h3>New York City</h3></span></h1>
      <h1 style="display:none">Old Software Engineer 2</h1>
      <article><p>We seek a Software Engineer.</p></article>''')
    assert scrape._posting_headings(page)==['Software Engineer 3']


def test_nested_iframe_below_the_viewport_can_open_options_and_fill_without_submitting(page):
    outer='''<style>html{scroll-behavior:smooth}</style><div style="height:1600px">Posting</div>
      <iframe src="https://form.test/application" style="width:700px;height:2200px"></iframe>'''
    inner='''<style>html{scroll-behavior:smooth}</style><div style="height:1100px"></div>
      <form onsubmit="window.sent=true;return false"><label for=email>Email</label><input id=email>
      <label for=country>Country</label><input id=country role=combobox onclick="menu.hidden=false">
      <div id=menu role=listbox hidden><div role=option onclick="country.value='United States';menu.hidden=true">United States</div></div>
      <button type=submit>Submit</button></form>'''
    page.route('**/*',lambda r:r.fulfill(content_type='text/html',body=inner if r.request.url.startswith('https://form.test/') else outer))
    page.goto('https://posting.test/job');page.frame_locator('iframe').locator('#country').wait_for(state='attached')
    view=forms.read_page(page);country=next(f for f in view.fields if f.label=='Country')
    assert forms._open_options(view.frames[country.frame],forms.locate(view,country),page)==['United States']
    page.keyboard.press('Escape')
    email=next(f for f in view.fields if f.label=='Email')
    forms.fill_field(page,view,email,'owner@example.test',{})
    assert forms.locate(view,email).input_value()=='owner@example.test'
    assert not view.frames[country.frame].evaluate('!!window.sent')


def test_scroll_preparation_does_not_force_or_reveal_a_hidden_control(page):
    page.set_content('<input id=hidden style="display:none" value="untouched">')
    with pytest.raises(PlaywrightTimeout):forms._scroll_to_control(page.locator('#hidden'))
    assert not page.locator('#hidden').is_visible()
    assert page.locator('#hidden').input_value()=='untouched'
