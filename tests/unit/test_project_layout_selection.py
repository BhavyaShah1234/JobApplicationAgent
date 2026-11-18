"""A one-page fit must not stop on a project description the hard gate rejects."""
import json
import shutil

import pytest

from jobagent.tools import projects, tailor


def _state(tmp_path):
    entries = [projects.ProjectEntry(repo=f"owner/p{i}", title=f"Project {i}", stack=["Python"],
                                    paragraph=f"Full description {i}.", short=f"Short description {i}.")
               for i in range(4)]
    cards = {e.repo: {"url": f"https://example.com/{i}", "full_name": e.repo} for i, e in enumerate(entries)}
    (tmp_path / "projects.json").write_text(json.dumps({
        "ranked": [e.model_dump() for e in entries], "cards": cards, "years": {}, "macro": True,
        "kept": 4, "short": [],
    }))
    return r"\section{Projects}Old projects.\section{Education}Education."


def test_rejected_extra_short_project_yields_to_three_full_projects(tmp_path):
    base = _state(tmp_path)
    attempts, checked = [], []

    def compile_pdf(tex):
        attempts.append(tex)
        # Four full descriptions overflow; both remaining candidates are one page.
        return (2 if len(attempts) == 1 else 1), ""

    def accept():
        checked.append(len(attempts))
        return "Short description" not in attempts[-1]

    tex, pages, error, kept, short = projects.fit(base, tmp_path, compile_pdf, accept=accept)
    assert (pages, error, kept, short) == (1, "", 3, frozenset())
    assert checked == [2, 3]
    assert "Full description 2." in tex and "Project 3" not in tex


def test_acceptable_extra_short_project_is_retained(tmp_path):
    base = _state(tmp_path)
    attempts = []

    def compile_pdf(tex):
        attempts.append(tex)
        return (2 if len(attempts) == 1 else 1), ""

    _, pages, error, kept, short = projects.fit(base, tmp_path, compile_pdf, accept=lambda: True)
    assert (pages, error, kept, short) == (1, "", 4, frozenset({"owner/p3"}))


def test_compile_failure_is_returned_without_inspecting_a_stale_pdf(tmp_path):
    base = _state(tmp_path)

    def accept():
        pytest.fail("A failed compilation must not inspect an older PDF")

    result = projects.fit(base, tmp_path, lambda _: (0, "compile failed"), accept=accept)
    assert result[1:3] == (0, "compile failed")


def test_exhausted_candidates_still_leave_the_hard_gate_a_rejectable_draft(tmp_path):
    base = _state(tmp_path)
    attempts = []

    def compile_pdf(tex):
        attempts.append(tex)
        return 1, ""

    result = projects.fit(base, tmp_path, compile_pdf, accept=lambda: False)
    assert len(attempts) == len(projects.layouts(4))
    assert result[0] == attempts[-1]
    assert result[3:] == (2, frozenset({"owner/p0", "owner/p1"}))


def test_missing_pdf_is_not_an_acceptable_layout(tmp_path):
    assert not projects.project_layout_ok(tmp_path / "missing.pdf")


@pytest.mark.skipif(shutil.which("latexmk") is None, reason="needs LaTeX")
def test_acceptance_reads_the_rendered_description(tmp_path):
    doc = r"""\documentclass[11pt]{article}\usepackage[margin=0.6in]{geometry}
\pagestyle{empty}\begin{document}\noindent Jane Doe\\ jane@example.com
\section*{PROJECTS}\noindent Demo $|$ Python \hfill March 2026\\
DESCRIPTION
\section*{SKILLS}\noindent Languages: Python, Rust\end{document}"""
    pages, error = tailor.compile_pdf(doc.replace("DESCRIPTION", "A one-line project."), tmp_path)
    assert pages == 1 and not error
    assert not projects.project_layout_ok(tmp_path / "resume.pdf")
    paragraph = ("Engineers needed reproducible cloud deployments across development environments. "
                 "Built a Python validation service with configuration checks, integration tests, and structured logs. "
                 "The system made release failures easier to diagnose and helped the team reproduce issues consistently.")
    pages, error = tailor.compile_pdf(doc.replace("DESCRIPTION", paragraph), tmp_path)
    assert pages == 1 and not error
    assert projects.project_layout_ok(tmp_path / "resume.pdf")
