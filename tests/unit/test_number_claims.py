"""Metric figures in generated text: a figure is the owner's only when the owner's own text has that figure (not just the same digits)."""
import pytest

from jobagent.state import JobDescription
from jobagent.tools import cover_letter as CL
from jobagent.tools import tailor as T

TEX = r"""\documentclass{article}
\begin{document}
\section{Summary}
\small{Old summary.}
\section{Experience}
  \resumeSubHeadingListStart
    \resumeSubheading
      {\underline{Engineer}}{May 2025 -- May 2026}
      {Acme}{Phoenix, Arizona}
      \resumeItemListStart
        \resumeItem{Built a PyTorch pipeline cutting latency 30\%.}  % backfill
      \resumeItemListEnd
  \resumeSubHeadingListEnd
\section{Projects}
  \resumeSubHeadingListStart
    \resumeProjectHeading
      {\textbf{\href{https://github.com/me/Proj}{Proj, Hackathon Winner}} $|$ \emph{Python}}{2025}
  \resumeSubHeadingListEnd

\section{Education}
  MS
\section{Technical Skills}
 \begin{itemize}
    \small{\item{
     \textbf{Languages}{: Python 3.11, C++}
    }}
 \end{itemize}
\end{document}
"""


def jd(**kw):
    base = {"title": "ML Engineer", "company": "X", "responsibilities": ["Build pipelines."], "technical_skills": ["Python 3.11", "CUDA 12"],
            "basic_qualifications": ["5+ years of experience", "Up to 10% travel"]}
    return JobDescription.model_validate(base | kw)


def filled(*texts, estimated=()):
    bullets = [T.FillBullet.model_construct(text=t, duties=[], original=None, estimated=list(estimated)) for t in texts]
    return T.Filled.model_construct(summary="s", roles=[T.FillRole.model_construct(role=0, bullets=bullets)], skills=[], notes="")


def tagged(*texts, estimated=(), **kw):
    f = filled(*texts, estimated=estimated)
    T.fill_checks(T.parse(TEX), jd(**kw), f)
    return f.roles[0].bullets[0].estimated


@pytest.mark.parametrize("text,figure", [
    ("Reduced inference latency by 5%.", "5%"),                      # "5" is in the posting ("5+ years"): the digit is not the candidate's result
    ("Cut training cost by 10%.", "10%"),                            # "10" is in the posting ("10% travel")
    ("Improved throughput 12x on the ingestion path.", "12x"),
    ("Shrank p95 latency to 80 ms.", "80ms"),
    ("Saved $40k a month in compute.", "$40k"),
    ("Scaled the service to 2M daily requests.", "2m"),
    ("Cut build time tenfold.", "tenfold"),
    ("Tripled the labelling throughput.", "tripl"),
    ("Halved model error on the holdout set.", "halv"),
    ("Doubled retrieval recall.", "doubl")])
def test_a_generated_figure_is_flagged_unless_the_owner_wrote_that_figure(text, figure):
    got = tagged(text)
    assert got, f"{text!r} carries an unconfirmed figure but was not listed for the candidate to confirm"


def test_the_owners_own_figure_and_technology_versions_are_not_flagged():
    assert tagged("Built a PyTorch pipeline cutting latency 30%.") == []                  # the template says 30\%
    assert tagged("Built services on Python 3.11 with CUDA 12 kernels.") == []            # versions in the posting and template
    assert tagged("Reduced latency by 22%.", estimated=["22%"]) == ["22%"]                 # already listed: not listed twice
    assert tagged("Built Spark jobs for 3 teams.", estimated=["3"]) == ["3"]


def test_a_figure_is_listed_once_even_when_the_digit_and_the_unit_forms_both_apply():
    got = tagged("Reduced latency by 22%.")
    assert len(got) == 1 and "22" in got[0]


def test_metric_tokens_normalise_spelling():
    from jobagent.tools.number_claims import metric_tokens
    assert metric_tokens("cut cost 30 %, 30 percent and 30%") == {"30%"}
    assert metric_tokens("10x and 10×") == {"10x"}
    assert metric_tokens("Python 3.11, ISO 26262, release 2025, 5+ years, 4.0 GPA") == set()


RESUME_TEXT = r"""Jane Doe. Experience: Built Azure pipelines, cutting failed runs 45\%. Data engineer with 3+ years."""


def letter(text):
    return CL.Letter(salutation="Dear Team,", closing="Sincerely",
                     paragraphs=[CL.Paragraph(text=x, evidence=["x"]) for x in ("I am excited to apply.", text, "Thank you for your time.")])


def test_a_letter_figure_needs_that_figure_in_the_sources_not_just_its_digits():
    jd_ = JobDescription.model_validate({"title": "AI Data Engineer", "company": "Acme", "basic_qualifications": ["5+ years of experience"]})
    bad = CL.consistency(letter("I cut inference latency by 5% at my last role."), RESUME_TEXT, jd_, "")
    assert any("5%" in i for i in bad), bad
    ok = CL.consistency(letter("I cut failed runs by 45% on Azure pipelines."), RESUME_TEXT, jd_, "")
    assert not any("45" in i for i in ok), ok
    plain = CL.consistency(letter("With 5 years in data engineering I am ready."), RESUME_TEXT, jd_, "")
    assert not any("number" in i for i in plain), plain
