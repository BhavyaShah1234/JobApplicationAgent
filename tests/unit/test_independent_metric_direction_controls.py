"""Independent existing-quality-API controls, authored before the metric-direction fix."""
import re
from types import SimpleNamespace
import pytest
from jobagent.state import JobDescription
from jobagent.tools import tailor as T

CONTROL='Built Python event handlers, reducing processing latency by 10%.'

def issues(text):
    r=SimpleNamespace(skills=[],roles=[SimpleNamespace(company='Example Lab',bullets=[])])
    jd=JobDescription(title='Software Engineer',company='Example',responsibilities=['Build backend services'],technical_skills=['Python','Golang'])
    bullets=[T.FillBullet(text=s,estimated=re.findall(r'\d+%',s),duties=[0]) for s in (text,CONTROL)]
    f=T.Filled(summary='Software Engineer with Python experience.',roles=[T.FillRole(role=0,bullets=bullets)],
               skills=[T.SkillLine(category='Languages',terms=['Python','Golang']),T.SkillLine(category='Databases',terms=['SQL']),T.SkillLine(category='Cloud',terms=['AWS'])],notes='Independent estimate-direction fixture.')
    return T.quality(r,jd,f)

@pytest.mark.parametrize('text',[
    'Built scalable backend systems in Golang, increasing API response time by 28%.',
    'Optimized Python services, boosting error rate by 15%.',
    'Improved Python pipelines, reducing processing throughput by 20%.',
])
def test_a_generated_adverse_metric_must_not_pass_as_a_benefit(text):
    assert issues(text)

@pytest.mark.parametrize('text',[
    'Built scalable backend systems in Golang, cutting API response time by 28%.',
    'Optimized Python services, reducing error rates by 15%.',
    'Optimized Python pipelines, improving processing throughput by 20%.',
    'Reduced Python service cost by 20%, accepting a 5% increase in response time.',
    'Injected Python service delays to test retries, increasing response time by 20%.',
    'Observed a Python latency regression after instrumentation, increasing response time by 28%.',
])
def test_correct_benefits_explicit_tradeoffs_and_deliberate_negative_observations_remain_allowed(text):
    assert issues(text)==[]
