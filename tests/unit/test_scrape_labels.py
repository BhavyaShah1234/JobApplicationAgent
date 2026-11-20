"""Required technology bullets retain their qualification context."""
import pytest
from jobagent.state import JobDescription
from jobagent.tools import jdtext
from jobagent.tools.scrape import apply_labeled


@pytest.mark.parametrize('heading,category',[
    ('Qualifications/Skills and Competencies Experience','basic_qualifications'),
    ('Preferred technical qualifications','preferred_qualifications'),
])
def test_technology_section_with_explicit_qualification_heading_remains_qualifications(heading,category):
    degree='A relevant bachelor degree.'
    bullets=['REST APIs;', 'Docker;']
    jd=JobDescription(title='Developer',company='Fixture',locations=['City, State'],
                      responsibilities=['Build backend services.'],technical_skills=['REST APIs','Docker'])
    sections=[jdtext.Section('Minimum qualifications',[],[degree]),jdtext.Section(heading,bullets)]
    apply_labeled(jd,sections,{0:'basic_qualifications',1:'technical_skills'})
    assert all(text in getattr(jd,category) for text in bullets)
    assert degree in jd.basic_qualifications
    assert jd.technical_skills==['REST APIs','Docker']
    assert jd.missing_core()==[]
