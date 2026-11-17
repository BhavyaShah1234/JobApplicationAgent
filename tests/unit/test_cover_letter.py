"""Cover letter: resume text extraction, consistency with the resume, template slots, header match."""

from pathlib import Path

import pytest

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


@pytest.mark.parametrize('claim', [
    'I look forward to discussing how my background, skills, and certifications align with this role.',
    'I hold a professional certification.',
    'I am professionally certified.',
])
def test_job_or_company_certifications_do_not_authorize_candidate_credentials(claim):
    issues = CL.consistency(letter('Introduction.', 'Experience.', claim), CL.resume_text(RESUME),
                            JD.model_copy(update={'preferred_qualifications': ['Professional certification preferred']}),
                            'The company values certifications.')
    assert any('candidate certification' in issue for issue in issues)


def test_credential_guard_preserves_supported_credentials_and_company_facts():
    assert not CL.consistency(letter('Introduction.', 'Experience.', 'My certification is relevant.'),
                              'Professional certification.', JD, '')
    assert not CL.consistency(letter('Introduction.', 'Experience.', 'The company values certifications.'),
                              CL.resume_text(RESUME), JD, 'The company values certifications.')


def test_render_fills_every_slot_and_skips_url_ids():
    template = "<<RECIPIENT>>|Re: <<SUBJECT>>|<<SALUTATION>>|<<BODY>>|<<CLOSING>>,"
    out = CL.render(template, letter("A & B 10%.", "Two.", "Three."), JD)
    assert "<<" not in out
    assert out.startswith("Hiring Team\\\\\nAcme\\\\\nChicago, IL|Re: AI Data Engineer (Req. JR-1)")
    assert "A \\& B 10\\%." in out and out.endswith("Sincerely,")
    uuid = JD.model_copy(update={"req_id": "10dfc8bc-99ad-4ca2-ab76-853cb90a92c2"})
    assert "Req." not in CL.render(template, letter("a", "b", "c"), uuid)
    assert CL.real_req_id("12795") and CL.real_req_id("JR2026744") and not CL.real_req_id("8E736DCABBE5EE1B1B7B")
    assert not CL.real_req_id("8130881", "https://job-boards.greenhouse.io/stripe/jobs/8130881")
    assert CL.real_req_id("JR2026744", "https://nvidia.wd5.myworkdayjobs.com/job/SoC-Engineer_JR2026744")


def test_header_match_and_committed_template_has_all_slots():
    assert CL.header_matches(RESUME, RESUME)
    assert not CL.header_matches(RESUME.replace("Jane Doe", "John Doe"), RESUME)
    example = (Path(__file__).parents[2] / "data" / "templates" / "cover_letter.example.tex").read_text()
    assert all(slot in example for slot in CL.SLOTS)


def test_consistency_flags_em_dashes():
    resume = CL.resume_text(RESUME)
    issues = CL.consistency(letter("I built pipelines — in Python.", "x", "y"), resume, JD, "")
    assert any("dash" in i for i in issues)


def test_template_is_justified_with_name_right_under_the_closing():
    example = (Path(__file__).parents[2] / "data" / "templates" / "cover_letter.example.tex").read_text()
    assert "\\raggedright" not in example
    assert "<<CLOSING>>,\\\\\nFirst Last" in example and "<<DATE>>" not in example


@pytest.mark.parametrize("unsupported_claim", [False, True])
def test_write_checks_normalized_punctuation_but_retains_claim_checks(monkeypatch, tmp_path, unsupported_claim):
    (tmp_path / "resume.tex").write_text(RESUME)
    template = tmp_path / "letter-template.tex"
    template.write_text(RESUME[:RESUME.index(r"\section{Summary}")] + "\n" + "\n".join(CL.SLOTS) + r"\end{document}")
    monkeypatch.setattr(CL, "template_path", lambda: template)
    raw = letter("I am applying for the Software Engineer – Datacenter position.",
                 "I built Azure Data Factory pipelines in Python.",
                 "I cut costs 70% with Snowflake." if unsupported_claim else "I would bring Microsoft Fabric skills.")
    calls = []

    def invoke(*args, **kwargs):
        calls.append(1)
        return raw

    def compile_pdf(tex, job_dir, name="resume"):
        (job_dir / f"{name}.tex").write_text(tex)
        return 1, ""

    monkeypatch.setattr(CL.llm, "structured_invoke", invoke)
    monkeypatch.setattr(CL, "compile_pdf", compile_pdf)
    result = CL.write(tmp_path, JD.model_copy(update={"title": "Software Engineer – Datacenter"}), "", object())
    assert not any("dash" in issue for issue in result["issues"])
    assert "Software Engineer, Datacenter" in result["letter"].paragraphs[0].text
    assert "–" in raw.paragraphs[0].text  # normalize a copy, not the caller's evidence
    if unsupported_claim:
        assert not result["valid"]
        assert any("Snowflake" in issue for issue in result["issues"])
        assert any("'70'" in issue for issue in result["issues"])
    else:
        assert result["valid"] and len(calls) == 1
