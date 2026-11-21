"""Post-implementation review boundaries: retain noun and time basis, not just digits."""
from types import SimpleNamespace as NS
import pytest
from jobagent.tools import tailor as T,letter_attribution as L,cover_letter as C
from jobagent.state import JobDescription

def tags(source,claim):
    r=NS(tex=source,roles=[NS(bullets=[source])])
    b=T.FillBullet.model_construct(text=claim,duties=[],original=None,estimated=[])
    f=T.Filled.model_construct(summary='',roles=[T.FillRole.model_construct(role=0,bullets=[b])],skills=[],notes='')
    T.fill_checks(r,JobDescription(title='Engineer',company='Example'),f)
    return b.estimated

@pytest.mark.parametrize('source,claim',[
 ('Processed 12 events/day with Python.','Processed 12 events/hour with Python.'),
 ('Processed 12 events with Python.','Processed 12 events per day with Python.'),
 ('Completed Docker builds in 40 ms.','Completed Docker builds in 40 s.'),
 ('Processed 12 requests/day with Python.','Supported 12 users/day with Python.'),
])
def test_quantity_dimension_changes_need_owner_confirmation(source,claim):
    assert tags(source,claim), 'Different noun or time basis cannot be documented by equal digits'

@pytest.mark.parametrize('source,claim',[
 ('Processed twelve events per day with Python.','Processed 12 events/day with Python.'),
 ('Completed Docker builds in 40 seconds.','Completed Docker builds in 40 s.'),
])
def test_equal_quantity_spelling_does_not_create_a_new_estimate(source,claim):
    assert not tags(source,claim)

def test_count_letter_cannot_turn_a_daily_rate_into_hourly():
    bank={'employment:0':{'entity':'Atlas Systems LLC','kind':'employment','text':'Processed 12 events per day with Python.'}}
    p=C.Paragraph(text='At Atlas Systems LLC, I processed 12 events per hour with Python.',evidence=[])
    assert L.paragraph_metric_issues(NS(paragraphs=[p]),bank)
