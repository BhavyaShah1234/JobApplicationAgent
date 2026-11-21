"""Estimate tags name figures that are in their bullet: stale tags from an earlier wording or a neighbouring bullet are dropped, missing ones are added."""
import pytest

from jobagent.state import JobDescription
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
JD = JobDescription.model_validate({"title": "QA Engineer", "company": "X", "responsibilities": ["Test."], "technical_skills": ["Python"]})


def tags(text, estimated):
    f = T.Filled.model_construct(summary="s", roles=[T.FillRole.model_construct(role=0, bullets=[
        T.FillBullet.model_construct(text=text, duties=[], original=None, estimated=list(estimated))])], skills=[], notes="")
    T.fill_checks(T.parse(TEX), JD, f)
    return f.roles[0].bullets[0].estimated


@pytest.mark.parametrize("text,given,expected", [
    # the saved incremental Veeva draft: tags left over from other wordings
    ("Automated functional tests with Cucumber, cutting manual effort by 25% and verifying SaaS release timelines.", ["20%", "15%", "25%"], ["25%"]),
    ("Developed test plans in TestRail, reducing bug report time by 30% and becoming a product expert.", ["25%", "30%"], ["30%"]),
    ("Conducted POCs for new testing tools, enhancing team productivity by 20%.", ["30%", "20%"], ["20%"]),
    ("Developed Python scripts for boundary tests, reducing test cycle time by 35% in short release cycles.", ["35%", "25%"], ["35%"])])
def test_stale_tags_are_dropped_and_the_figure_of_the_bullet_stays(text, given, expected):
    assert tags(text, given) == expected


def test_tags_that_belong_to_the_bullet_are_kept_in_any_spelling():
    assert tags("Cut build time 40 s to 8 s.", ["40 s"]) == ["40 s", "8"]                                   # 40 s covers 40; 8 is a new plain count
    assert tags("Tripled labelling throughput.", ["Tripled"]) == ["Tripled"]
    assert tags("Reduced latency by 22%.", ["22"]) == ["22"]                                                 # legacy bare digits for a present figure
    assert tags("Processed 1,000 events per second.", ["1,000"]) == ["1,000"]


def test_a_tag_for_a_figure_that_is_not_in_the_bullet_is_dropped_even_when_the_bullet_has_none():
    assert tags("Wrote Python tools for the team.", ["30%", "tripled"]) == []


def test_missing_figures_are_still_added_after_stale_ones_are_removed():
    got = tags("Cut cost 15% and sped up builds 3x.", ["99%"])
    assert got and all(x in ("15%", "3x") or x.lower() == "3x" for x in got) and len(got) == 2, got


def test_a_word_figure_tag_is_not_listed_twice():
    assert tags("Tripled labelling throughput.", ["Tripled"]) == ["Tripled"]
    assert tags("Tripled labelling throughput and cut cost 20%.", ["tripled"]) == ["tripled", "20%"]
    assert tags("Tripled labelling throughput.", []) == ["Tripled"]
