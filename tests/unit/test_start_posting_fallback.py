"""A fieldless error shell behind an extracted link falls back once, without sending any form."""
from playwright.sync_api import sync_playwright

from jobagent.steps import apply


def test_invalid_extracted_apply_link_reopens_the_original_posting_then_only_opens_the_form():
    bad = 'https://jobs.example.test/truncated/'
    source = 'https://jobs.example.test/posting/123/'
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=True)
        try:
            page = browser.new_page()
            requests = []
            def route(request):
                if request.request.resource_type == 'document':
                    requests.append(request.request.url)
                html = '<h1>Careers</h1>' if request.request.url == bad else '''
                  <h1>Software Engineer</h1><a id=open href="#">Apply Now</a><script>
                  document.getElementById('open').onclick=e=>{e.preventDefault();document.body.innerHTML=`<h1>Application</h1>
                    <form onsubmit="window.sent=true;return false"><input placeholder=Name>
                    <input placeholder=Email type=email><input placeholder=Phone type=tel>
                    <button>Submit Application</button></form>`};</script>'''
                request.fulfill(status=200, content_type='text/html', body=html)
            page.route('https://jobs.example.test/**', route)
            wait = page.wait_for_timeout
            page.wait_for_timeout = lambda ms: wait(min(ms, 10))
            view = apply._start(page, bad, lambda url: None, log=lambda msg: None, fallback=source)
            assert len(view.fields) == 3
            assert requests == [bad, source]
            assert not page.evaluate('window.sent === true')
        finally:
            browser.close()


def test_responsive_apply_button_uses_its_real_anchor_address_without_running_a_handler():
    source = 'https://jobs.example.test/posting/123/'
    target = 'https://jobs.example.test/apply/123/'
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=True)
        try:
            page = browser.new_page()
            requests = []
            def route(request):
                requests.append(request.request.url)
                html = '''<h1>Software Engineer</h1>
                  <button aria-expanded=false aria-controls=filters>Show More Options</button>
                  <div id=filters hidden><input placeholder=Keyword></div>
                  <form><input type=number required placeholder="Alert frequency"><button>Create Alert</button></form>
                  <a hidden href="/apply/123/">Apply now »</a>
                  <button aria-haspopup=true href="/apply/123/" onclick="window.badHandler=true">Apply now</button>'''
                if request.request.url == target:
                    html = '<h1>Application</h1><input placeholder=Name><input type=email placeholder=Email><input type=tel placeholder=Phone><button>Submit Application</button>'
                request.fulfill(status=200, content_type='text/html', body=html)
            page.route('https://jobs.example.test/**', route)
            wait = page.wait_for_timeout
            page.wait_for_timeout = lambda ms: wait(min(ms, 10))
            view = apply._start(page, source, lambda url: None, log=lambda msg: None)
            assert page.url == target and len(view.fields) == 3
            assert requests == [source, target]
            assert not page.evaluate('window.badHandler === true')
        finally:
            browser.close()
