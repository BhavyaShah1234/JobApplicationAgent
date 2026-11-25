"""Search filters and job alerts do not become applicant questions or hide a real application."""
import pytest
from playwright.sync_api import sync_playwright

from jobagent.filling import discovery
from jobagent.steps import apply
from jobagent.tools import forms, portal_agent


@pytest.fixture
def page():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=True)
        try:
            yield browser.new_page()
        finally:
            browser.close()


UTILITY = '''<form role=search><input name=q placeholder=Keyword>
  <a role=button aria-expanded=false aria-controls=filters>Show More Options</a>
  <div id=filters hidden><select aria-label=Department><option>Engineering</option></select></div>
  <button>SEARCH JOBS</button></form>
  <form><label>Alert frequency<input type=number required></label><button>Create Alert</button></form>'''


def test_readers_and_hidden_inventory_share_the_same_application_scope(page):
    page.set_content(UTILITY + '<h1>Apply</h1><form><label>First name<input required></label><button>Submit Application</button></form>')
    view = forms.read_page(page)
    assert [f.label for f in view.fields] == ['First name']
    assert view.coverage.complete
    assert [f['label'] for f in page.evaluate(discovery.INVENTORY)] == ['First name']
    auth = page.evaluate(portal_agent.SNAPSHOT_JS)
    assert not any(item['label'] in ('Keyword', 'SEARCH JOBS', 'Create Alert', 'Alert frequency') for item in auth['items'])


def test_a_search_role_never_hides_a_form_with_an_application_action(page):
    page.set_content('<form role=search><label>Your name<input required></label><button>Apply</button></form>')
    view = forms.read_page(page)
    assert [f.label for f in view.fields] == ['Your name']
    assert view.coverage.complete


def test_a_real_collapsed_application_section_remains_a_coverage_blocker(page):
    page.set_content(UTILITY + '<button type=button aria-expanded=false aria-controls=work>Work experience</button>'
                     '<div id=work hidden><input placeholder=Employer required></div>')
    view = forms.read_page(page)
    assert not view.coverage.complete and view.coverage.collapsed == ['Work experience']


def test_fieldless_public_application_chooser_keeps_client_navigation_state(page):
    source = 'https://jobs.example.test/job/123/'
    target = 'https://jobs.example.test/application/123/'
    requests = []
    def route(request):
        requests.append(request.request.url)
        html = UTILITY + '''<h1>Software Engineer</h1>
          <a hidden href="/talentcommunity/apply/123/">Apply now »</a>
          <div class=dropdown><button aria-haspopup=true aria-expanded=false href="/talentcommunity/apply/123/"
            onclick="this.setAttribute('aria-expanded','true');document.getElementById('options').hidden=false">Apply now</button>
          <div id=options class=dropdown-menu hidden><a href="#" onclick="event.preventDefault();location.href='/application/123/'">Apply Now</a></div></div>'''
        if request.request.url == target:
            html = '<h1>Application</h1><form><input placeholder=Name><input type=email placeholder=Email><input type=tel placeholder=Phone><button>Submit Application</button></form>'
        request.fulfill(status=200, content_type='text/html', body=html)
    page.route('https://jobs.example.test/**', route)
    wait = page.wait_for_timeout
    page.wait_for_timeout = lambda ms: wait(min(ms, 10))
    view = apply._start(page, source, lambda url: None, log=lambda message: None)
    assert len(view.fields) == 3 and page.url == target
    assert requests == [source, target]  # no direct navigation to a menu's inert fallback
