"""Multi-agent validator: the Summary's own checks."""

from jobagent.steps import multi_agent as M


def test_long_copied_summary_is_flagged():
    s = ("Software Engineer I, Entry-Level, as a recent graduate with a degree in Computer Science or related "
         "discipline, with 1 year of experience, having completed two internships, possessing skills in languages "
         "such as Python, Java, or Kotlin. " * 2)
    issues = M.summary_issues(s, overflow=False)
    assert any("characters" in i for i in issues) and any("copies" in i for i in issues)


def test_good_summary_passes():
    s = ("Software engineer with 5+ years building Python and Kafka backends for real-time detection. Shipped "
         "LLM tooling with LangGraph and RAG that cut review time by 35%.")
    assert M.summary_issues(s, overflow=False) == []
