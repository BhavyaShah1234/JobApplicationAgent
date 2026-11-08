def test_project_headings_say_python_without_a_version():
    from jobagent.tools.projects import heading_stack
    assert heading_stack(["Python 3.12", "FastAPI", "LangGraph"]) == ["Python", "FastAPI", "LangGraph"]
    assert heading_stack(["Python 3.14", "Python", "PyTorch"]) == ["Python", "PyTorch"]
    assert heading_stack(["python3.11", "Python 3.x", "Pydantic v2"]) == ["Python", "Pydantic v2"]
