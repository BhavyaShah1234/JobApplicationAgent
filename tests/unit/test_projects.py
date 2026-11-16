import shutil

import pytest


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
