"""Figure provenance follow-ups: unit-specific tags, owner figures bound to the role they came from, word multipliers in letters, many tags."""
import pytest

from jobagent.state import JobDescription
from jobagent.tools import cover_letter as CL
from jobagent.tools import tailor as T

TEX2 = r"""\documentclass{article}
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
    \resumeSubheading
      {\underline{Analyst}}{May 2023 -- May 2024}
      {Beta}{Tempe, Arizona}
      \resumeItemListStart
        \resumeItem{Wrote SQL reports for finance.}
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
    base = {"title": "ML Engineer", "company": "X", "responsibilities": ["Build pipelines."], "technical_skills": ["Python 3.11"],
            "basic_qualifications": ["5+ years of experience"]}
    return JobDescription.model_validate(base | kw)


def check(text, role=0, estimated=()):
    f = T.Filled.model_construct(summary="s", roles=[T.FillRole.model_construct(role=role, bullets=[
        T.FillBullet.model_construct(text=text, duties=[], original=None, estimated=list(estimated))])], skills=[], notes="")
    T.fill_checks(T.parse(TEX2), jd(), f)
    return f.roles[0].bullets[0].estimated


def test_a_percent_tag_does_not_vouch_for_a_distinct_x_or_dollar_figure_with_the_same_digits():
    got = check("Cut cost 5% and lifted throughput 5x, saving $5k.", estimated=["5%"])
    assert "5%" in got and any("5x" in t.lower() for t in got) and any("$5k" in t.lower() for t in got), got


def test_a_legacy_bare_digit_tag_covers_a_single_figure_only():
    assert check("Reduced latency by 22%.", estimated=["22"]) == ["22"]                       # one figure with those digits: the tag covers it
    got = check("Cut cost 22% and lifted throughput 22x.", estimated=["22"])
    assert any("22%" in t for t in got) and any("22x" in t.lower() for t in got), got          # two distinct figures: say which


def test_an_owner_figure_from_another_role_does_not_make_a_generated_figure_the_owners_here():
    assert check("Cut latency by 30%.", role=0) == []                                           # the template's own bullet of this role says 30%
    got = check("Cut latency by 30%.", role=1)                                                  # Beta's original bullets have no 30%
    assert got and "30" in got[0], got


def test_many_new_figures_stay_listed_and_the_filled_structure_still_reloads():
    f = T.Filled(summary="s", roles=[T.FillRole(role=0, bullets=[
        T.FillBullet(text="Scaled 12 services to 2M requests, cutting latency 25% and cost 15%.", duties=[0], original=None),
        T.FillBullet(text="Wrote Python tools for the team.", duties=[0], original=None),
        T.FillBullet(text="Reviewed code for the platform group.", duties=[0], original=None)])],
        skills=[T.SkillLine(category=c, terms=["Python"]) for c in "ABC"], notes="")
    T.fill_checks(T.parse(TEX2), jd(), f)
    tags = f.roles[0].bullets[0].estimated
    assert len(tags) >= 4 and any("25%" in t for t in tags) and any("15%" in t for t in tags) and any("2M" in t.upper() for t in tags), tags
    again = T.Filled.model_validate_json(f.model_dump_json())                                    # used to fail: more than 3 tags
    assert again.roles[0].bullets[0].estimated == tags


RESUME_PLAIN = "Jane Doe. Built Azure pipelines, cutting failed runs 45%. Data engineer with 3+ years."
JD_ = JobDescription.model_validate({"title": "AI Data Engineer", "company": "Acme", "basic_qualifications": ["Up to 30% travel", "5+ years"]})


def letter(*sentences):
    return CL.Letter(salutation="Dear Team,", closing="Sincerely", paragraphs=[
        CL.Paragraph(text="I am excited to apply.", evidence=["x"]), CL.Paragraph(text=" ".join(sentences), evidence=["x"]),
        CL.Paragraph(text="Thank you for your time.", evidence=["x"])])


@pytest.mark.parametrize("sentence", ["I tripled the throughput of our pipelines.", "I cut our error rate tenfold.", "I halved model latency."])
def test_a_word_multiplier_the_resume_does_not_contain_is_reported_in_a_letter(sentence):
    assert any("figure" in i for i in CL.consistency(letter(sentence), RESUME_PLAIN, JD_, "")), sentence


def test_word_multipliers_and_qualitative_claims_the_resume_supports_are_allowed():
    resume = RESUME_PLAIN + " Tripled labelling throughput."
    assert not CL.consistency(letter("I tripled the throughput of our labelling pipeline."), resume, JD_, "")
    assert not CL.consistency(letter("I improved the performance of our pipelines."), RESUME_PLAIN, JD_, "")


def test_a_candidate_figure_must_come_from_the_resume_while_company_context_may_cite_the_posting_or_profile():
    bad = CL.consistency(letter("I cut inference latency by 30% in my last role."), RESUME_PLAIN, JD_, "")
    assert any("30%" in i for i in bad), bad                                                    # the posting's "30% travel" is not the candidate's result
    ok = CL.consistency(letter("Acme grew revenue 30% last year, and I admire that."), RESUME_PLAIN, JD_, "Acme grew revenue 30% last year.")
    assert not any("30%" in i for i in ok), ok
    mine = CL.consistency(letter("I cut failed runs by 45% on Azure pipelines."), RESUME_PLAIN, JD_, "")
    assert not any("45" in i for i in mine), mine


def test_a_sentence_about_the_company_may_cite_the_profile_even_with_a_first_person_word_in_it():
    ok = CL.consistency(letter("I admire that Acme grew revenue 30% last year."), RESUME_PLAIN, JD_, "Acme grew revenue 30% last year.")
    assert not any("30%" in i for i in ok), ok
    mine = CL.consistency(letter("My team tripled labelling throughput."), RESUME_PLAIN, JD_, "Acme grew revenue 30% last year.")
    assert any("figure" in i for i in mine), mine
