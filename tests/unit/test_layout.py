"""Layout validator on a compiled page: blank bottom and a one-line project description are reported."""

import shutil

import pytest

from jobagent.tools import layout
from jobagent.tools import tailor as T

pytestmark = pytest.mark.skipif(shutil.which("latexmk") is None, reason="needs LaTeX")

DOC = r"""\documentclass[11pt]{article}\usepackage[margin=0.6in]{geometry}\pagestyle{empty}\begin{document}
\noindent Jane Doe\\ jane@example.com
\section*{PROJECTS}
\noindent Demo $|$ Python, Kafka \hfill March 2026\\
A one-line description of a project.
\section*{TECHNICAL SKILLS}
\noindent Languages: Python, Rust\\
\end{document}"""


def test_blank_page_and_one_line_project_are_reported(tmp_path):
    pages, err = T.compile_pdf(DOC, tmp_path)
    assert pages == 1 and not err
    lay = layout.check(tmp_path / "resume.pdf")
    assert lay.free_pt and lay.free_pt > layout.BLANK_PT
    assert any("blank at the bottom" in i for i in lay.issues)
    assert lay.one_line_projects == ["Demo"]
    assert lay.short_skills == ["Languages"]


def test_off_page_text_is_a_hard_failure_even_on_one_page(tmp_path):
    from types import SimpleNamespace

    from jobagent.steps import multi_agent

    tex = DOC.replace("A one-line description of a project.", "VeryLongUnbreakableHeading" * 12)
    T.compile_pdf(tex, tmp_path)
    lay = layout.check(tmp_path / "resume.pdf")
    assert lay.pages == 1
    assert any(i.startswith("text overflows the page") for i in lay.issues)
    hard = multi_agent.hard_issues({"job_dir": tmp_path}, SimpleNamespace(skills=[]))
    assert any(i.startswith("text overflows the page") for i in hard)
