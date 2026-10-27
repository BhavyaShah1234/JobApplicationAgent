"""Step 9 pure logic: template parsing, escaping, bullet checks, Projects rendering, repo-card change detection."""

import pytest

from jobagent.state import JobDescription
from jobagent.tools import github, projects, repo_cards
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
        \resumeItem{Presented results at a symposium.}
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
     \textbf{Languages}{: Python, C++}
    }}
 \end{itemize}
\end{document}
"""


def jd(**kw) -> JobDescription:
    base = {"title": "Data Engineer", "company": "X", "responsibilities": ["Build pipelines in Azure Data Factory."],
            "technical_skills": ["Azure Data Factory", "Python"]}
    return JobDescription.model_validate(base | kw)


def test_parse_roles_dates_and_backfill_markers():
    r = T.parse(TEX)
    (role,) = r.roles
    assert (role.title, role.company, role.dates) == ("Engineer", "Acme", "May 2025 – May 2026")
    assert role.pool == [True, False]
    assert T.eligible(role, 0, marked=True) and not T.eligible(role, 1, marked=True)
    assert r.skills == [("Languages", "Python, C++")]


def test_latex_escaping_and_unicode():
    assert T.to_latex("100% A&B_c #1") == r"100\% A\&B\_c \#1"
    assert T.to_latex("cut ~40% (45→20 min) и") == r"cut about 40\% (45 to 20 min) "
    assert T.to_plain(r"30\% \& more") == "30% & more"


def test_soft_duties():
    assert T.is_soft("Partner with architects and stakeholders to translate ambiguous requirements.")
    assert not T.is_soft("Ensure machine safety compliance. Partner with EHS on guarding.")


def _filled(*bullets: str) -> T.Filled:
    return T.Filled(summary="s", roles=[T.FillRole(role=0, bullets=[T.FillBullet(text=b, duties=[0]) for b in bullets])],
                    skills=[T.SkillLine(category=c, terms=["Python"]) for c in "ABC"], notes="")


def test_duplicates_across_bullets():
    f = _filled("Developed Python/PySpark and SQL/T-SQL transformations for robot data, cutting prep 20%.",
                "Developed Python/PySpark and SQL/T-SQL transformations for clinical imaging, cutting cost 10%.")
    assert T.duplicates(T.parse(TEX), f)


def test_quality_flags_academic_wording_and_missing_tech():
    f = _filled("Partnered with faculty to translate research goals into solutions.",
                "Built Azure Data Factory pipelines, cutting failed runs 30%.")
    issues = T.quality(T.parse(TEX), jd(), f)
    assert any("academic" in i and "faculty" in i for i in issues)
    assert any("names no technology" in i for i in issues)
    assert not any("bullet 2" in i for i in issues)


def test_one_bullet_without_result_is_allowed_but_not_two():
    one = _filled("Migrated storage to Azure Blob Storage with metadata in Azure SQL.",
                  "Built Azure Data Factory pipelines, cutting failed runs 30%.")
    two = _filled("Migrated storage to Azure Blob Storage with metadata in Azure SQL.",
                  "Wrote Python modules for Azure Data Factory.")
    assert not [i for i in T.quality(T.parse(TEX), jd(), one) if "no result" in i]
    assert len([i for i in T.quality(T.parse(TEX), jd(), two) if "no result" in i]) == 2


def test_skill_line_renders_tools_once():
    line = T.SkillLine(category="Vision", terms=["Machine Vision", "OpenCV", "PFMEA, SPC"],
                       tools_for=[T.ToolsFor(term="machine vision", tools=["OpenCV", "YOLO"])])
    assert line.render() == ["Machine Vision (OpenCV, YOLO)", "PFMEA", "SPC"]


def test_projects_known_entries_and_paragraph_render():
    assert projects.known_entries(TEX) == {"me/proj": ("Proj, Hackathon Winner", "2025")}
    entry = projects.ProjectEntry(repo="me/Proj", title="Proj", stack=["Python", "FastAPI"],
                                  paragraph="Solved X with Y; built Z & more.")
    block = projects.render([entry], {"me/proj": {"url": "https://github.com/me/Proj", "full_name": "me/Proj"}},
                            {"me/Proj": "2025"})
    assert r"\item[]\small{Solved X with Y; built Z \& more." in block and r"\resumeItem{" not in block
    out = projects.apply(TEX, block)
    assert out.count(r"\section{Projects}") == 1 and r"\section{Education}" in out


@pytest.fixture
def cards_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(repo_cards, "card_dir", lambda: tmp_path)
    return tmp_path


def test_repo_card_change_detection(cards_dir, monkeypatch):
    repo = github.Repo("me/Proj", "https://github.com/me/Proj", pushed_at="2026-10-01T00:00:00Z")
    assert repo_cards.needs_analysis(repo, None)  # new repo
    card = {"full_name": "me/Proj", "pushed_at": repo.pushed_at, "head_sha": "a" * 40, "readme": ""}
    monkeypatch.setattr(repo_cards, "head_sha", lambda _: pytest.fail("no request when pushed_at is unchanged"))
    assert not repo_cards.needs_analysis(repo, card)
    moved = github.Repo("me/Proj", repo.url, pushed_at="2026-10-05T00:00:00Z")
    monkeypatch.setattr(repo_cards, "head_sha", lambda _: "a" * 40)  # push to another branch
    assert not repo_cards.needs_analysis(moved, card) and card["pushed_at"] == moved.pushed_at
    newer = github.Repo("me/Proj", repo.url, pushed_at="2026-10-09T00:00:00Z")
    monkeypatch.setattr(repo_cards, "head_sha", lambda _: "b" * 40)  # new commit on the default branch
    assert repo_cards.needs_analysis(newer, card)


def test_punctuation_dashes_removed_but_ranges_kept():
    assert T.no_dashes("Built X — a bot—that works") == "Built X, a bot, that works"
    assert T.no_dashes("2025–2026 and pages 3-4") == "2025–2026 and pages 3-4"
    assert T.no_dashes("Fabric – Azure") == "Fabric, Azure"
    assert "—" not in T.to_latex("COLLIDE — AI-Powered Site Selection")


def test_quality_flags_dash_in_bullets():
    f = _filled("Built Azure Data Factory pipelines — cutting failed runs 30%.",
                "Wrote Python modules for Azure SQL, cutting prep 20%.")
    assert any("dash" in i for i in T.quality(T.parse(TEX), jd(), f))


@pytest.mark.parametrize("before, after", [
    ("lifting throughput 35%.", "lifting throughput by 35%."),
    ("cutting failed refreshes 45%", "cutting failed refreshes by 45%"),
    ("cutting review turnaround about 30%", "cutting review turnaround by about 30%"),
    ("cutting failed runs by 30%", "cutting failed runs by 30%"),
    ("reduced time from 45 to 20%", "reduced time from 45 to 20%"),
    ("lifted accuracy to 95%", "lifted accuracy to 95%"),
    ("fine-tuned YOLOv8 (96.4% mAP@0.5)", "fine-tuned YOLOv8 (96.4% mAP@0.5)"),
])
def test_percentage_changes_take_by(before, after):
    assert T.by_percent(before) == after


def test_capitalized_tool_names_count_as_technology():
    f = _filled("Engineered a config-driven pipeline where Pydantic specs compile into Jinja2 reports, cutting build "
                "time by 60%.", "Built Azure Data Factory pipelines, cutting failed runs by 30%.")
    assert not [i for i in T.quality(T.parse(TEX), jd(), f) if "names no technology" in i]


def test_jd_items_include_technical_qualifications_but_not_degrees_or_soft_duties():
    j = jd(responsibilities=["Build pipelines in Azure Data Factory.", "Partner with stakeholders on requirements."],
           basic_qualifications=["Bachelor's degree in Computer Science.", "3+ years of experience.",
                                 "Strong SQL and Python for data transformation.", "Excellent attitude."],
           preferred_qualifications=["Experience with Databricks."])
    items = T.jd_items(j)
    assert [(k, n) for k, _, n in items] == [("duty", "REQUIRED"), ("duty", "soft"), ("basic", "REQUIRED"),
                                             ("preferred", "PREFERRED")]
    assert items[2][1].startswith("Strong SQL")


def test_only_new_bullets_cover_jd_items():
    j = jd(responsibilities=["Build pipelines in Azure Data Factory."])
    original = T.Filled(summary="s", roles=[T.FillRole(role=0, bullets=[
        T.FillBullet(text="Built a PyTorch pipeline cutting latency by 30%.", duties=[0], original=0),
        T.FillBullet(text="Wrote Python tools, cutting prep by 10%.", duties=[0], original=0)])],
        skills=[T.SkillLine(category=c, terms=["Python"]) for c in "ABC"], notes="")
    assert T.fill_checks(T.parse(TEX), j, original)[0] == [0]
    original.roles[0].bullets[1].original = None
    assert T.fill_checks(T.parse(TEX), j, original)[0] == []


def test_skill_term_equal_to_category_is_not_repeated():
    line = T.SkillLine(category="AI Tools", terms=["AI tools", "Ollama"],
                       tools_for=[T.ToolsFor(term="AI tools", tools=["GitHub Copilot", "Claude Code"])])
    assert line.render() == ["GitHub Copilot", "Claude Code", "Ollama"]


def test_project_paragraph_uses_template_macro_when_defined():
    entry = projects.ProjectEntry(repo="me/Proj", title="Proj", stack=["Python"], paragraph="Built X.")
    cards = {"me/proj": {"url": "https://github.com/me/Proj", "full_name": "me/Proj"}}
    assert r"\resumeProjectText{Built X.}" in projects.render([entry], cards, {"me/Proj": "2025"}, macro=True)
