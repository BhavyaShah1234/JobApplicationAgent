"""Public-browser collection must keep filters, follow pages, and stop on refusals."""
from contextlib import contextmanager
import json
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest
from playwright.sync_api import sync_playwright

from jobagent.tools import listings as L
from jobagent.tools.listings_browser import _board, collect_workday


def test_a_visible_verification_on_http_403_reaches_the_human_controller(monkeypatch):
    from jobagent.tools import challenge
    from jobagent.tools.listings_browser import _navigate_json
    pending = SimpleNamespace(value=SimpleNamespace(status=200))
    state = {'blocked': True, 'handoffs': 0}

    class Page:
        @contextmanager
        def expect_response(self, *args, **kwargs):
            yield pending

        def goto(self, *args, **kwargs):
            return SimpleNamespace(status=403 if state['blocked'] else 200)

    class Controller:
        def wait(self, page, block):
            state['handoffs'] += 1
            state['blocked'] = False

    block = challenge.Challenge('cloudflare', 'https://example.test', 'Verification detected')
    monkeypatch.setattr(challenge, 'detect', lambda page: block if state['blocked'] else None)
    assert _navigate_json(Page(), 'https://example.test', lambda r: True, Controller()) is pending.value
    assert state['handoffs'] == 1


def test_a_careers_result_that_is_a_posting_opens_its_search_board():
    careers = {'url': 'https://acme.wd5.myworkdayjobs.com/en-US/Careers/job/US-Arizona-Phoenix/Engineer_1?locations=a#detail',
               'board': {'family': 'workday', 'token': 'acme/wd5/Careers'}}
    assert _board(careers)[3] == 'https://acme.wd5.myworkdayjobs.com/en-US/Careers?locations=a'


@pytest.mark.parametrize('locale', ['', 'en-US/'])
def test_filtered_search_survives_the_api_and_browser_handoff(monkeypatch, locale):
    url = 'https://acme.wd5.myworkdayjobs.com/' + locale + 'Careers?locations=a&locations=b&jobFamilyGroup=sw'
    careers = {'url': url, 'board': {'family': 'workday', 'token': 'acme/wd5/Careers'}}
    calls = []
    monkeypatch.setattr('jobagent.tools.listings_browser.collect_workday', lambda *a, **kw: calls.append((a, kw)) or ['browser result'])
    def refused(*a, **kw):
        assert a[2]['appliedFacets'] == {'locations': ['a', 'b'], 'jobFamilyGroup': ['sw']}
        assert a[2]['searchText'] == ''
        raise L.Blocked('catalog answered429')
    assert L.collect(careers, 'Acme', ['Software Engineer'], get_json=refused, log=lambda _: None) == ['browser result']
    assert len(calls) == 1 and calls[0][0][0]['url'] == url


HTML = '''<html><body><h1>Careers</h1><div id="jobs"></div>
<button aria-label="next" onclick="offset+=20;load()">Next</button><script>
let offset=0;
async function load(){
 const params=new URLSearchParams(location.search), facets={};
 for(const k of ['locations','jobFamilyGroup'])if(params.has(k))facets[k]=params.getAll(k);
 const response=await fetch('/wday/cxs/acme/Careers/jobs',{method:'POST',headers:{'Content-Type':'application/json'},
 body:JSON.stringify({appliedFacets:facets,limit:20,offset,searchText:params.get('q')||''})});
 if(response.ok){const d=await response.json();document.querySelector('#jobs').textContent=d.jobPostings.map(x=>x.title).join(' ');}
}
load();</script></body></html>'''


@pytest.mark.parametrize('status', [200, 429])
def test_real_browser_reads_its_own_filtered_pages_or_stops(status, monkeypatch):
    requests = []
    monkeypatch.setattr(L, 'PAUSE_S', 0)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=True)
        ctx = browser.new_context()
        ctx.new_page()
        def serve(route):
            request = route.request
            if urlparse(request.url).path.endswith('/jobs'):
                body = request.post_data_json
                requests.append(body)
                offset = body['offset']
                rows = [{'title': 'Software Engineer '+str(i), 'externalPath': '/job/US-Arizona-Phoenix/Engineer_R'+str(i),
                         'locationsText': 'US, Arizona, Phoenix'} for i in range(offset, min(21, offset+20))]
                route.fulfill(status=status, content_type='application/json', body=json.dumps({'total': 21, 'jobPostings': rows}))
            else:
                route.fulfill(status=200, content_type='text/html', body=HTML)
        ctx.route('**/*', serve)
        @contextmanager
        def factory():
            yield ctx
        careers = {'url': 'https://acme.wd5.myworkdayjobs.com/en-US/Careers?locations=a&locations=b&jobFamilyGroup=sw',
                   'board': {'family': 'workday', 'token': 'acme/wd5/Careers'}}
        try:
            if status == 429:
                with pytest.raises(L.Blocked, match='429'):
                    collect_workday(careers, 'Acme', ['Software Engineer'], context_factory=factory, log=lambda _: None)
                assert len(requests) == 1
            else:
                rows = collect_workday(careers, 'Acme', ['Software Engineer'], context_factory=factory, log=lambda _: None)
                assert len(rows) == 21 and len({x.url for x in rows}) == 21
                assert [r['offset'] for r in requests] == [0, 20]
                assert all(r['appliedFacets'] == {'locations': ['a', 'b'], 'jobFamilyGroup': ['sw']} for r in requests)
                assert all(r['searchText'] == '' for r in requests)
        finally:
            browser.close()


def test_location_detail_refusal_uses_the_same_postings_public_detail(monkeypatch):
    from jobagent.tools.jobqueue import Listing
    item = Listing('Acme', 'Software Engineer', 'https://acme.wd5.myworkdayjobs.com/en-US/Careers/job/US-Arizona-Phoenix/Engineer_1',
                   ['2 Locations'], portal='workday')
    called = []
    def detail(url):
        called.append(url)
        return {'jobPostingInfo': {'location': 'Phoenix, Arizona', 'country': {'descriptor': 'United States'},
                                   'additionalLocations': ['Austin, Texas, United States']}}
    monkeypatch.setattr('jobagent.tools.listings_browser.read_workday_detail', detail)
    def refused(*args):
        raise L.Blocked('detail answered429')
    locations = L.workday_locations(item, get_json=refused)
    assert called == [item.url]
    assert len(locations) == 2 and 'Phoenix' in locations[0] and 'Austin' in locations[1]


def test_public_detail_reader_uses_its_exact_get_response():
    from jobagent.tools.listings_browser import read_workday_detail
    url = 'https://acme.wd5.myworkdayjobs.com/en-US/Careers/job/US-Arizona-Phoenix/Engineer_1'
    info = {'jobPostingInfo': {'location': 'Phoenix, Arizona', 'country': {'descriptor': 'United States'}}}
    markup = '<html><body>Posting<script>fetch("/wday/cxs/acme/Careers/job/US-Arizona-Phoenix/Engineer_1");</script></body></html>'
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='chrome', headless=True)
        ctx = browser.new_context();ctx.new_page()
        def serve(route):
            if '/wday/cxs/' in route.request.url:
                route.fulfill(status=200, content_type='application/json', body=json.dumps(info))
            else:
                route.fulfill(status=200, content_type='text/html', body=markup)
        ctx.route('**/*', serve)
        @contextmanager
        def factory():
            yield ctx
        try:
            assert read_workday_detail(url, context_factory=factory) == info
        finally:
            browser.close()
