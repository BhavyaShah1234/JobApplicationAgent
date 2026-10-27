"""Cover letter: resume text extraction, consistency with the resume, template slots, header match."""

from datetime import date
from pathlib import Path

from jobagent.state import JobDescription
from jobagent.tools import cover_letter as CL

RESUME = r"""\begin{document}
\begin{center}
    \textbf{\Huge \scshape Jane Doe} \\ \vspace{1pt}
    \faEnvelope$/$\href{mailto:j@x.com}{\underline{j@x.com}}
\end{center}
\section{Summary}
\small{Data engineer with 3+ years.}
\section{Experience}
      \resumeItemListStart
        \resumeItem{Built Azure Data Factory pipelines, cutting failed runs 45\%.}  % backfill
      \resumeItemListEnd
\section{Technical Skills}
 \begin{itemize}[leftmargin=0.15in, label={}]
    \small{\item{
     \textbf{Data}{: Python, PySpark} \\
     \textbf{Cloud}{: Azure SQL}
    }}
 \end{itemize}
\end{document}
"""
JD = JobDescription.model_validate({"title": "AI Data Engineer", "company": "Acme", "locations": ["Chicago, IL"],
                                    "req_id": "JR-1", "technical_skills": ["Microsoft Fabric"]})


def letter(*texts: str) -> CL.Letter:
    return CL.Letter(salutation="Dear Acme Hiring Team,", closing="Sincerely",
                     paragraphs=[CL.Paragraph(text=t, evidence=[]) for t in texts])


def test_resume_text_keeps_escaped_percent_and_drops_markup():
    text = CL.resume_text(RESUME)
    assert "cutting failed runs 45%." in text and "backfill" not in text
    assert "Python, PySpark" in text and "itemize" not in text and "\\" not in text


def test_consistency_flags_new_numbers_and_tools():
    resume = CL.resume_text(RESUME)
    ok = letter("I built Azure Data Factory pipelines that cut failed runs 45%.", "PySpark and Python daily.",
                "I'd like to bring Microsoft Fabric skills to Acme.")
    assert CL.consistency(ok, resume, JD, "Acme has 36 offices.") == []
    bad = letter("I cut costs 70% with Snowflake.", "x", "y")
    issues = CL.consistency(bad, resume, JD, "")
    assert any("'70'" in i for i in issues) and any("Snowflake" in i for i in issues)


def test_render_fills_every_slot():
    template = "<<DATE>>|<<RECIPIENT>>|Re: <<SUBJECT>>|<<SALUTATION>>|<<BODY>>|<<CLOSING>>,"
    out = CL.render(template, letter("A & B 10%.", "Two.", "Three."), JD, today=date(2026, 10, 27))
    assert "<<" not in out
    assert out.startswith("October 27, 2026|Hiring Team\\\\\nAcme\\\\\nChicago, IL|Re: AI Data Engineer (Req. JR-1)")
    assert "A \\& B 10\\%." in out and out.endswith("Sincerely,")


def test_header_match_and_committed_template_has_all_slots():
    assert CL.header_matches(RESUME, RESUME)
    assert not CL.header_matches(RESUME.replace("Jane Doe", "John Doe"), RESUME)
    example = (Path(__file__).parents[2] / "data" / "templates" / "cover_letter.example.tex").read_text()
    assert all(slot in example for slot in CL.SLOTS)


def test_consistency_flags_em_dashes():
    resume = CL.resume_text(RESUME)
    issues = CL.consistency(letter("I built pipelines — in Python.", "x", "y"), resume, JD, "")
    assert any("dash" in i for i in issues)
