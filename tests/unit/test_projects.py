import shutil

import pytest


def test_visible_project_fragments_block_but_hidden_and_unused_variants_do_not(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    from jobagent.steps import multi_agent
    from jobagent.tools import projects

    state = {"kept": 2, "short": ["owner/second"], "ranked": [
        {"repo": "owner/first", "paragraph": "Built cloud automation. It ensures consistent, validated AWS", "short": "Unused fragment"},
        {"repo": "owner/second", "paragraph": "Unused full fragment", "short": "Built the system. Reduced deployment time."},
        {"repo": "owner/hidden", "paragraph": "Hidden fragment"},
    ]}
    assert len(projects.content_findings(state)) == 1
    assert "project 1" in projects.content_findings(state)[0]
    # Exercise the real document hard gate as well as the standalone finding.
    (tmp_path / "projects.json").write_text(json.dumps(state))
    monkeypatch.setattr(multi_agent.layout, "check", lambda _: SimpleNamespace(issues=[], pages=1, free_pt=0))
    findings = multi_agent.hard_issues({"job_dir": tmp_path}, SimpleNamespace(skills=[]))
    assert any("project 1 description ends" in f for f in findings)
    state["ranked"][0]["paragraph"] = 'Built cloud automation. It validates infrastructure. Reduced deployment time.'
    assert projects.content_findings(state) == []
    state["ranked"][1]["short"] = "Reduced site selection"
    assert "project 2" in projects.content_findings(state)[0]


def test_project_headings_say_python_without_a_version():
    from jobagent.tools.projects import heading_stack
    assert heading_stack(["Python 3.12", "FastAPI", "LangGraph"]) == ["Python", "FastAPI", "LangGraph"]
    assert heading_stack(["Python 3.14", "Python", "PyTorch"]) == ["Python", "PyTorch"]
    assert heading_stack(["python3.11", "Python 3.x", "Pydantic v2"]) == ["Python", "Pydantic v2"]


@pytest.mark.skipif(shutil.which("latexmk") is None, reason="needs LaTeX")
def test_long_project_heading_wraps_without_clipping_title_stack_or_date(tmp_path):
    import subprocess

    from jobagent.tools import layout, projects, tailor

    entry = projects.ProjectEntry(
        repo="owner/research", title="LLM/mLLM Training & Optimization Research",
        stack=["Python", "PyTorch", "HuggingFace Transformers", "scikit-learn", "DeepSpeed"],
        paragraph="Researchers needed a common framework. We built a modular training system. It improved evaluation.",
        short="A modular training system.")
    block = projects.render([entry], {entry.repo: {"url": "https://example.com/research", "full_name": entry.repo}},
                            {entry.repo: "August 2026"}, macro=True)
    tex = r"""\documentclass[11pt]{article}
\usepackage[margin=0.6in]{geometry}\usepackage{enumitem}\usepackage{hyperref}
\pagestyle{empty}
\newcommand{\resumeSubHeadingListStart}{\begin{itemize}[leftmargin=0in,label={}]}
\newcommand{\resumeSubHeadingListEnd}{\end{itemize}}
\newcommand{\resumeProjectHeading}[2]{\item\begin{tabular*}{0.97\textwidth}[t]{l@{\extracolsep{\fill}}r}\small#1 & #2\\\end{tabular*}}
\newcommand{\resumeProjectText}[1]{\par #1}
\begin{document}\noindent Jane Doe\\ jane@example.com\section*{PROJECTS}
""" + block + r"\end{document}"
    pages, error = tailor.compile_pdf(tex, tmp_path)
    assert pages == 1 and not error
    pdf = tmp_path / "resume.pdf"
    assert not any(i.startswith("text overflows") for i in layout.check(pdf).issues)
    text = subprocess.check_output(["pdftotext", str(pdf), "-"], text=True)
    assert "August 2026" in text and "DeepSpeed" in text and "Optimization Research" in text
