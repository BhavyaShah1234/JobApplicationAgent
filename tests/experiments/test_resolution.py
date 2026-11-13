from jobagent.experiments.resolution import Fact,resolve,compile_facts
from jobagent.tools.forms import Field


def test_school_cannot_fuzzy_match_a_different_school():
    f=Field(0,'select','School',options=['Adams State University','Other'])
    assert resolve(f,[Fact('school',['School'],'Arizona State University','applicant')]).value is None
    f.kind='combobox'
    assert resolve(f,[Fact('school',['School'],'Arizona State University','applicant')]).value=='Arizona State University'


def test_travel_uses_original_jd_even_after_prefix_budget():
    data={'preferences':[{'question':'Willing to travel?','answer':'','instructions':'Use JD travel percentage'}]}
    facts=compile_facts(data,'x'*6000+'\nTravel - 30% or less')
    d=resolve(Field(0,'select','Willingness to travel',options=['Travel - 50% or more','Travel - 30% or less']),facts)
    assert d.value=='Travel - 30% or less' and d.source=='jd.txt:2'


def test_ambiguity_is_not_a_known_answer():
    facts=[Fact('a',['From Date'],'01/01/2026','applicant'),Fact('b',['From Date'],'08/01/2024','applicant')]
    assert resolve(Field(0,'text','From Date'),facts).value is None


def test_section_scope_prevents_cross_degree_answers():
    facts=[Fact('a',['School'],'ASU','applicant','Education 1'),Fact('b',['School'],'VIT','applicant','Education 2')]
    assert resolve(Field(0,'text','School',section='Education 2'),facts).value=='VIT'


def test_exact_day_is_not_invented_from_month_only_source():
    facts=[Fact('date',['Start Date'],'August 2024','applicant')]
    d=resolve(Field(0,'text','Start Date',format='MM/DD/YYYY'),facts)
    assert d.value is None and d.reason=='exact day missing from source'


def test_numeric_range_equivalence_and_explicit_absence_fallback():
    f=Field(0,'select','Willingness to travel',options=['Travel - 30% or less','Travel - 50% or more'])
    data={'preferences':[{'question':'Willing to travel?','answer':'','instructions':'Use JD percentage; otherwise choose 100%.'}]}
    assert resolve(f,compile_facts(data,'Some travel, up to 30%, will be required.')).value=='Travel - 30% or less'
    assert resolve(f,compile_facts(data,'A posting with no such requirement.')).value=='Travel - 50% or more'
