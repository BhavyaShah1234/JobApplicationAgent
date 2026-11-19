"""Source-bound role grades survive description-only extraction."""
import pytest
from jobagent.state import JobDescription
from jobagent.tools.scrape import _retain_heading_grade


@pytest.mark.parametrize('title,headings,expected',[
    ('Software Engineer',['Software Engineer 3'],'Software Engineer 3'),
    ('Software Engineer',['Software Engineer III'],'Software Engineer III'),
    ('Software Engineer 3',['Software Engineer 3'],'Software Engineer 3'),
    ('C++ Developer',['C++ Developer II'],'C++ Developer II'),
    ('Software Engineer',['Software Engineer 3','Software Engineer 4'],'Software Engineer'),
    ('Software Engineer',['Careers at Acme'],'Software Engineer'),
    ('Software Engineer',['Software Engineer 0'],'Software Engineer'),
])
def test_only_a_unique_explicit_grade_can_extend_the_extracted_base_title(title,headings,expected):
    jd=JobDescription(title=title,company='Acme')
    _retain_heading_grade(jd,headings)
    assert jd.title==expected
