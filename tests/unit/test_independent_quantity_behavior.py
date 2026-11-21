"""Independent existing-entrypoint counterexamples for quantity provenance and result recognition."""
from types import SimpleNamespace as NS
import pytest
from jobagent.tools import tailor as T, cover_letter as C, letter_attribution as L
from jobagent.state import JobDescription

def tagged(candidate, own='Built a FastAPI service for internal reporting.', other='', technical=(), estimated=()):
    r=NS(tex='Engineer with 5 years of experience. '+own+' '+other,roles=[NS(bullets=[own]),NS(bullets=[other])])
    b=T.FillBullet.model_construct(text=candidate,duties=[],original=None,estimated=list(estimated))
    f=T.Filled.model_construct(summary='',roles=[T.FillRole.model_construct(role=0,bullets=[b])],skills=[],notes='')
    T.fill_checks(r,JobDescription(title='Engineer',company='Example',technical_skills=list(technical)),f)
    return b.estimated

@pytest.mark.parametrize('candidate,other',[
 ('Built 12 REST endpoints using FastAPI.','Built 12 REST endpoints using FastAPI.'),
 ('Deployed 5 services using Docker.',''),
 ('Processed 12 datasets using Python.','Maintained 12 datasets using Python.'),
])
def test_global_or_other_role_numbers_do_not_establish_my_count(candidate,other):
    assert tagged(candidate,other=other), 'Unconfirmed role-specific quantity was silently treated as documented'

def test_a_technology_version_in_the_jd_does_not_establish_a_count():
    assert tagged('Deployed 12 services using Docker.',technical=['CUDA 12'])

def test_equal_digits_with_a_different_counted_noun_are_not_same_evidence():
    assert tagged('Supported 12 users using FastAPI.',own='Deployed 12 services using Docker.')

@pytest.mark.parametrize('source,claim',[
 ('Built 12 REST endpoints using FastAPI.','Built 12 REST endpoints using FastAPI.'),
 ('Deployed 7 services using Docker.','Deployed 7 services using Docker.'),
 ('Completed the Docker rollout in 40 minutes.','Completed the Docker rollout in 40 minutes.'),
])
def test_the_same_role_actual_quantity_does_not_become_an_estimate(source,claim):
    assert tagged(claim,own=source)==[]

def test_explicit_count_estimate_tag_is_preserved_once():
    tags=tagged('Built 12 API endpoints using FastAPI.',estimated=['12 endpoints'])
    assert len(tags)==1 and '12' in tags[0]

@pytest.mark.parametrize('source',[
 'Built services on Python 3.12 using CUDA 12.',
 'Validated ISO 27001 controls with Python 3.12.',
])
def test_known_versions_and_standard_ids_are_not_estimated_business_quantities(source):
    assert tagged(source,technical=['Python 3.12','CUDA 12','ISO 27001'])==[]

def letter(text):
    return C.Letter(salutation='Dear Hiring Team,',closing='Sincerely',paragraphs=[C.Paragraph(text=t,evidence=[]) for t in ('I am interested.',text,'Thank you.')])

BANK={
 'employment:0':{'entity':'Atlas Systems LLC','kind':'employment','text':'Built 12 services in Python. Cut latency by 20%.'},
 'employment:1':{'entity':'Beacon Inc','kind':'employment','text':'Supported 12 users with FastAPI. Built 9 API endpoints.'},
}

@pytest.mark.parametrize('claim',[
 'At Atlas Systems LLC, I built 9 API endpoints.',
 'At Atlas Systems LLC, I supported 12 users with FastAPI.',
])
def test_count_letter_cannot_borrow_another_roles_quantity(claim):
    assert L.paragraph_metric_issues(letter(claim),BANK)

def test_same_source_count_letter_passes():
    assert not L.paragraph_metric_issues(letter('At Atlas Systems LLC, I built 12 services in Python.'),BANK)

def test_general_interest_and_versions_are_not_count_achievements():
    assert not L.paragraph_metric_issues(letter('At Atlas Systems LLC, I worked with Python 3.12.'),BANK)

@pytest.mark.parametrize('claim',[
 'Implemented Python 3.12.',
 'Configured ISO 27001.',
])
def test_a_version_or_standard_number_alone_does_not_supply_a_result(claim):
    r=NS(tex='',skills=[('Tools','Python, Docker')],roles=[NS(company='Atlas Systems LLC',bullets=[])])
    b=T.FillBullet.model_construct(text=claim,duties=[],original=None,estimated=[])
    f=T.Filled.model_construct(summary='',roles=[T.FillRole.model_construct(role=0,bullets=[b])],skills=[],notes='')
    findings=T.quality(r,JobDescription(title='Engineer',company='Example',technical_skills=['Python']),f)
    assert any('result' in x.lower() for x in findings)

@pytest.mark.parametrize('claim',[
 'Built 12 REST endpoints using FastAPI.',
 'Processed 10,000 records per day with Python.',
 'Added OpenTelemetry tracing to FastAPI so that engineers could diagnose failed requests.',
])
def test_scope_quantity_or_precise_qualitative_result_does_not_require_percent(claim):
    r=NS(tex='',skills=[('Tools','FastAPI, Python, OpenTelemetry')],roles=[NS(company='Atlas Systems LLC',bullets=[])])
    b=T.FillBullet.model_construct(text=claim,duties=[],original=None,estimated=[])
    f=T.Filled.model_construct(summary='',roles=[T.FillRole.model_construct(role=0,bullets=[b])],skills=[],notes='')
    assert not [x for x in T.quality(r,JobDescription(title='Engineer',company='Example',technical_skills=['Python']),f) if 'states no result' in x]
