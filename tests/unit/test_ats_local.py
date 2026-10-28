"""Step 10 local scorer: keyword extraction and normalisation (no PDF, no network)."""

import pytest

from jobagent.state import JobDescription
from jobagent.tools import ats_local as A


@pytest.mark.parametrize("term, names", [
    ("Different backend languages, including Java, Rust, Python and Go", ["Java", "Rust", "Python", "Go"]),
    ("Docker and Kubernetes for containerisation and orchestration", ["Docker", "Kubernetes"]),
    ("Our infrastructure is built on Terraform, Kubernetes, Azure, Python, Postgres, and Kafka. While we value it",
     ["Terraform", "Kubernetes", "Azure", "Python", "Postgres", "Kafka"]),
    ("PySpark", ["PySpark"]),
])
def test_atomize_sentences_into_names(term, names):
    assert A.atomize(term) == names


def test_normalisation_matches_common_spellings():
    assert A.norm("Node.js") in A.norm("Built services in nodejs and React")
    assert A.norm("C++") in A.norm("Strong C++ and C# skills")
    assert A.norm("CI/CD") in A.norm("shipped through CI CD pipelines")


def test_required_keywords_weigh_more():
    jd = JobDescription.model_validate({
        "title": "Engineer", "company": "X", "technical_skills": ["Python", "Kafka", "Figma"],
        "basic_qualifications": ["Strong Python and Kafka experience."],
        "preferred_qualifications": ["Familiarity with Figma."]})
    kw = A.keywords(jd)
    assert kw["Python"] == kw["Kafka"] == 2.0 and kw["Figma"] == 1.5


def test_header_junk_detected():
    assert A.header_junk("Jane Doe\n\x83/555-123-4567 | ï/jane") == ["\x83", "ï"]
    assert A.header_junk("Jane Doe\n/555-123-4567 | /jane@x.com") == []
