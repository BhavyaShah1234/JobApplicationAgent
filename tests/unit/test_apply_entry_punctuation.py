"""A published Apply Now! link opens the form; a final Apply Now! button never sends it."""
import pytest
from playwright.sync_api import sync_playwright
from jobagent.steps import apply


@pytest.mark.parametrize('fields',[0,1,2])
def test_bang_in_apply_label_does_not_change_navigation_or_submission_authority(fields):
    source='https://jobs.example.test/posting'
    target='https://jobs.example.test/application'
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True)
        try:
            page=browser.new_page();requests=[]
            def route(request):
                requests.append((request.request.method,request.request.url))
                if request.request.url==target:
                    html='<form><input placeholder=Name><input placeholder=Email><input placeholder=Phone><button>Submit Application</button></form>'
                elif not fields:html='<h1>Software Engineer</h1><a href="/application">Apply Now!</a>'
                else:html='<h1>Application</h1><form onsubmit="window.sent=true;return false">'+''.join(f'<input aria-label="Question {i}" value=Known>' for i in range(fields))+'<button>Apply Now!</button></form>'
                request.fulfill(status=200,content_type='text/html',body=html)
            page.route('https://jobs.example.test/**',route)
            wait=page.wait_for_timeout;page.wait_for_timeout=lambda ms:wait(min(ms,10))
            view=apply._start(page,source,None,lambda msg:None)
            assert len(view.fields)==(fields or 3)
            assert requests==[('GET',source)]+([] if fields else [('GET',target)])
            assert not page.evaluate('window.sent===true')
        finally:browser.close()
