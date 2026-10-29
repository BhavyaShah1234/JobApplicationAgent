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
                                  paragraph="Solved X with Y; built Z & more.", short="Built Z.")
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
    entry = projects.ProjectEntry(repo="me/Proj", title="Proj", stack=["Python"], paragraph="Built X.", short="X.")
    cards = {"me/proj": {"url": "https://github.com/me/Proj", "full_name": "me/Proj"}}
    assert r"\resumeProjectText{Built X.}" in projects.render([entry], cards, {"me/Proj": "2025"}, macro=True)


TWO_ROLES = TEX.replace(r"""  \resumeSubHeadingListEnd

\section{Projects}""", r"""    \resumeSubheading
      {\underline{Developer}}{May 2026 -- August 2026}
      {Beta Corp}{Tempe, Arizona}
      \resumeItemListStart
        \resumeItem{Migrated a Windows VM monolith to Azure microservices on Kubernetes.}  % backfill
        \resumeItem{Deployed a forecasting service through Azure DevOps CI/CD pipelines.}  % backfill
      \resumeItemListEnd
  \resumeSubHeadingListEnd

\section{Projects}""")


def test_bullet_from_another_employer_is_flagged():
    r = T.parse(TWO_ROLES)
    assert T.moved_from(r, 0, "Migrated a Windows VM monolith to Azure microservices on Kubernetes, cutting cost 10%.") == 1
    assert T.moved_from(r, 1, "Migrated a Windows VM monolith to Azure microservices on Kubernetes.") is None
    assert T.moved_from(r, 0, "Built Azure Data Factory pipelines, cutting failed runs by 30%.") is None


def test_repair_rewrites_only_the_named_bullets():
    f = _filled("Old bullet one about Python.", "Old bullet two about SQL.")
    f.roles[0].bullets[0].original = 1
    fixes = T.Repair(fixes=[T.BulletFix(role=0, bullet=1, text="Built Python ETL, cutting runtime by 20%.",
                                        estimated=["20%"]), T.BulletFix(role=5, bullet=1, text="ignored")])
    assert T.apply_repair(f, fixes) == 1
    b0, b1 = f.roles[0].bullets
    assert (b0.text, b0.original, b0.estimated) == ("Built Python ETL, cutting runtime by 20%.", None, ["20%"])
    assert b1.text == "Old bullet two about SQL."


def test_project_dates_are_month_and_year():
    assert projects.month_year("2026-03-14T10:00:00Z") == "March 2026"
    assert projects.month_year("") == ""


def test_today_line_is_in_phoenix_time(monkeypatch):
    from jobagent import clock
    line = clock.today_line()
    assert "America/Phoenix" in line and str(clock.today().year) in line
    assert clock.with_today("PROMPT").startswith("PROMPT\n\nToday is ")


def test_short_paragraph_used_only_for_named_projects():
    entry = projects.ProjectEntry(repo="me/Proj", title="Proj", stack=["Python"], paragraph="Long version.",
                                  short="Short version.")
    cards = {"me/proj": {"url": "https://github.com/me/Proj", "full_name": "me/Proj"}}
    assert "Long version." in projects.render([entry], cards, {}, macro=True)
    assert "Short version." in projects.render([entry], cards, {}, macro=True, short=frozenset({"me/Proj"}))


def test_project_from_card_fallback_is_short_and_dash_free():
    card = {"full_name": "me/Proj", "title": "Proj — a long subtitle", "tech_stack": ["Python", "FastAPI"],
            "summary": "First sentence about the problem. Second sentence about the system. " * 6}
    e = projects.from_card(card)
    assert e.title == "Proj" and len(e.paragraph) <= 320 and len(e.short) <= 170 and e.short.endswith(".")


def test_tools_written_inside_a_term_are_parsed():
    line = T.SkillLine(category="AI Tools", terms=["AI Tools (GitHub Copilot, Claude Code, Cursor)"],
                       tools_for=[T.ToolsFor(term="AI Tools", tools=["GitHub Copilot", "Claude Code", "Cursor"])])
    assert line.render() == ["GitHub Copilot", "Claude Code", "Cursor"]
    line = T.SkillLine(category="Data", terms=["SQL, Spark (PySpark, Databricks)"])
    assert line.render() == ["SQL", "Spark (PySpark, Databricks)"]


def test_keyword_pass_adds_skills_and_rewrites_named_bullets():
    f = _filled("Built Python ETL, cutting runtime by 20%.", "Wrote SQL reports, cutting prep by 10%.")
    kp = T.KeywordPass(skills=[T.SkillAdd(category="A", terms=["Kafka", "Python"]),
                              T.SkillAdd(category="Streaming", terms=["Flink"])],
                       bullets=[T.BulletFix(role=0, bullet=2, text="Wrote SQL and Spark reports, cutting prep by 10%.")])
    g = T.apply_keyword_pass(f, kp)
    assert g.skills[0].render() == ["Python", "Kafka"] and g.skills[-1].category == "Streaming"
    assert g.roles[0].bullets[1].text.startswith("Wrote SQL and Spark") and f.roles[0].bullets[1].text.startswith("Wrote SQL reports")


def test_generic_words_never_become_skills():
    f = _filled("Built Python ETL, cutting runtime by 20%.", "Wrote SQL reports, cutting prep by 10%.")
    kp = T.KeywordPass(skills=[T.SkillAdd(category="A", terms=["AWS", "build", "tools", "information", "Rust"]),
                              T.SkillAdd(category="Junk", terms=["value", "problems"])])
    g = T.apply_keyword_pass(f, kp)
    assert g.skills[0].render() == ["Python", "AWS", "Rust"] and all(s.category != "Junk" for s in g.skills)
    assert T.is_generic("Information") and not T.is_generic("CI/CD") and not T.is_generic("data pipelines on AWS")


def test_project_refit_prefers_short_paragraphs_before_dropping(tmp_path):
    import json
    ranked = [projects.ProjectEntry(repo=f"me/P{i}", title=f"P{i}", stack=["Python"], paragraph=f"Long {i}.",
                                    short=f"Short {i}.") for i in range(3)]
    cards = {f"me/p{i}": {"url": f"https://github.com/me/P{i}", "full_name": f"me/P{i}"} for i in range(3)}
    (tmp_path / "projects.json").write_text(json.dumps({"ranked": [e.model_dump() for e in ranked], "kept": 3,
                                                        "short": [], "years": {}, "macro": True, "cards": cards}))
    # fits only with 3 projects if at least one is short
    fake = lambda tex: (1 if tex.count("Short") >= 1 or tex.count("resumeProjectHeading") <= 2 else 2, "")
    tex, pages, err, kept, short = projects.fit(TEX, tmp_path, fake, start=(3, 0))
    assert (pages, kept, short) == (1, 3, frozenset({"me/P2"})) and "Short 2." in tex and "Long 0." in tex
