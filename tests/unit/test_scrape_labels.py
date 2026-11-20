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


def test_required_experience_subheadings_keep_technology_bullets_and_years():
    degree = 'A relevant bachelor degree and five years of experience.'
    first = 'Experience must include three (3) years involving each of the following'
    second = 'Experience must involve two (2) years in the following'
    technologies = ['REST APIs;', 'Git or GitHub;', 'Database technologies;', 'HTML, CSS, or Typescript;', 'Micro-service architecture.',
                    'Multi-threaded application code;', 'Production-grade software;', 'JSON and XML;', 'Spring Boot and Java;', 'Maven.']
    sections = [jdtext.Section('Qualifications', paragraphs=[degree]),
                jdtext.Section('Qualifications/Skills and Competencies Experience'),
                jdtext.Section(first, technologies[:5]), jdtext.Section(second, technologies[5:])]
    jd = JobDescription(title='Developer', company='Fixture', locations=['City, State'],
                        responsibilities=['Build backend services.'], technical_skills=['Java'])
    apply_labeled(jd, sections, {0:'basic_qualifications', 1:'technical_skills', 2:'technical_skills', 3:'technical_skills'})
    assert all(item in jd.basic_qualifications for item in [degree, first, second, *technologies])
    assert jd.missing_core() == []
