"""Additional public boards: locations/identity, search pagination and conservative detail recovery."""
from urllib.parse import parse_qs,urlparse

import pytest

from jobagent.tools import careers,listings
from jobagent.tools import listings_extra as E
from jobagent.tools.jobqueue import Listing

UKG='https://recruiting2.ultipro.com/ACME1000/JobBoard/12345678-1234-1234-1234-123456789abc'


def test_ukg_board_resolves_from_an_official_careers_link():
    found=careers.scan_for_board('<a href="'+UKG+'/?q=x">Join our team</a>','https://acme.test/careers')
    assert found['family']=='ultipro' and found['url']==UKG
    assert found['token']=='ACME1000/JobBoard/12345678-1234-1234-1234-123456789abc'


def ukg_row(i,country='United States',city='Reno',state='NV'):
    return {'Id':str(i),'Title':'Software Engineer','RequisitionNumber':'REQ'+str(i),
            'Locations':[{'Address':{'City':city,'State':{'Code':state},'Country':{'Name':country}}}],
            'PostedDate':'2026-11-16T12:00:00Z','BriefDescription':'Develop public software.'}


def test_ukg_preserves_foreign_locations_and_uses_each_opportunity_identity():
    rows=E.parse_ukg({'opportunities':[ukg_row(1),ukg_row(2,'Canada','Vancouver','BC')]},'Acme',UKG)
    assert rows[0].locations==['Reno, NV, United States']
    assert rows[1].locations==['Vancouver, BC, Canada']
    assert rows[0].req_id=='REQ1' and rows[1].url.endswith('OpportunityDetail?opportunityId=2')


def test_ukg_reads_beyond_featured_jobs_and_stops_when_the_server_repeats_a_page():
    calls=[]
    def get(url,method,body):
        assert url==UKG+'/JobBoardView/LoadSearchResults' and method=='POST'
        calls.append(body['opportunitySearch']['Skip'])
        return {'opportunities':[ukg_row(i) for i in range(50)],'totalCount':137}
    rows=E.collect_ukg({'url':UKG},'Acme',get,lambda:None,lambda _:None)
    assert calls==[0,50] and len(rows)==50


def kpmg_card(i,locations='Phoenix, AZ; New York, NY'):
    return ('<a href="/jobdetail/?jobId='+str(i)+'"><div class="grid-view"><div class="h4">Software Engineer</div>'
            '<div class="text-xs">2 Locations</div></div><div class="list-view"><div class="h5">Software Engineer</div>'
            '<div class="text-xs">Advisory | '+locations+'</div></div></a>')


def test_kpmg_reads_single_title_with_all_offices_from_duplicate_grid_list_markup():
    rows=E.parse_kpmg(kpmg_card(12),'https://www.kpmguscareers.com/job-search/','KPMG')
    assert len(rows)==1 and rows[0].title=='Software Engineer'
    assert rows[0].locations==['Phoenix, AZ','New York, NY'] and rows[0].req_id=='12'
    assert rows[0].url=='https://www.kpmguscareers.com/jobdetail/?jobId=12'


def test_kpmg_queries_and_pages_the_published_search_endpoint():
    calls=[]
    def get(url):
        query=parse_qs(urlparse(url).query);calls.append(query)
        n=int(query['spage'][0]);more='<ul><li class="pagination-next"><a data-href="2">Next</a></li></ul>' if n==1 else ''
        return {'postings':{'jobs':kpmg_card(n)},'pagination':more}
    board=careers.board_of('https://www.kpmguscareers.com/job-search/')
    rows=E.collect_kpmg(board,'KPMG',['Software Engineer'],lambda _: '<input id="template-url" value="/theme">',get,lambda:None,lambda _:None)
    assert len(rows)==2 and [x['spage'] for x in calls]==[['1'],['2']]
    assert all(x['keyword']==['Software Engineer'] for x in calls)


@pytest.mark.parametrize('additional,expected', [(['Austin, TX'],['Reno, NV, United States','Austin, TX']),
                                              (['Vancouver, BC, Canada'],['Reno, NV, United States','Vancouver, BC, Canada']),
                                              (['Unresolved office'],['2 Locations'])])
def test_workday_location_expansion_never_lets_one_known_office_hide_an_unknown(additional,expected):
    row=Listing('Acme','Software Engineer','https://acme.wd5.myworkdayjobs.com/en-US/Careers/job/Reno/Engineer_R1',
                ['2 Locations'],portal='workday')
    def get(url):
        assert url=='https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/Careers/job/Reno/Engineer_R1'
        return {'jobPostingInfo':{'location':'Reno, NV','country':{'descriptor':'United States'},'additionalLocations':additional}}
    assert listings.workday_locations(row,get_json=get)==expected
