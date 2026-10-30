"""Step 10 pure logic: merging the sites' missing keywords, the advice pass, the upload file name."""

from jobagent.steps import ats
from jobagent.tools import ats_sites
from jobagent.tools import tailor as T


def _filled():
    return T.Filled(summary="Engineer with 3+ years building fraud detection systems in Python.",
                    roles=[T.FillRole(role=0, bullets=[T.FillBullet(text=t, duties=[0]) for t in (
                        "Built X in Python, cutting loss by 9%.", "Ran Y on Kafka, halving review time.")])],
                    skills=[T.SkillLine(category=c, terms=[t]) for c, t in (("Languages", "Python"), ("Data", "Kafka"),
                                                                             ("Cloud", "Azure"))], notes="")


def test_merged_missing_puts_curated_lists_first_and_dedupes():
    out = ats.merged_missing({"autoapplymax": ["rust", "Fraud Analysis"],
                              "atsfriendly": ["fraud analysis", "trust and safety"]})
    assert out == ["fraud analysis", "trust and safety", "rust"]


def test_eligibility_advice_is_filtered():
    assert ats.ELIGIBILITY.search("Consider emphasizing the 5+ years of software engineering experience")
    assert not ats.ELIGIBILITY.search("Highlight collaboration with finance, security, and product teams")


def test_advice_pass_rewrites_summary_only_when_sane():
    f = _filled()
    new = "Software engineer who partners with security and product teams on fraud detection in Python and Kafka."
    assert T.apply_keyword_pass(f, T.KeywordPass(summary=new)).summary == new
    assert T.apply_keyword_pass(f, T.KeywordPass(summary="")).summary == f.summary
    assert T.apply_keyword_pass(f, T.KeywordPass(summary="x" * 600)).summary == f.summary


def test_keyword_prompt_includes_advice_and_summary(monkeypatch):
    r = type("R", (), {"roles": [type("Role", (), {"title": "Engineer", "company": "Acme", "dates": "2024"})()]})()
    jd = type("JD", (), {})()
    monkeypatch.setattr(T, "jd_items", lambda jd: [])  # no posting context needed here
    msgs = T.build_keyword_prompt(r, jd, _filled(), ["fraud analysis"], ["Name the teams the work involved"])
    text = msgs[1].content
    assert "ADVICE:\n- Name the teams" in text and "SUMMARY:\nEngineer with 3+ years" in text


def test_upload_name_from_personal_info(tmp_path, monkeypatch):
    (tmp_path / "personal_info.yaml").write_text('name:\n  first: "Bhavya Minesh"\n  middle: ""\n  last: "Shah"\n')
    monkeypatch.setattr("jobagent.config.get_settings", lambda: type("S", (), {"data_dir": tmp_path})())
    assert ats_sites.upload_name() == "Bhavya_Minesh_Shah_Resume.pdf"


def test_skill_already_said_by_category_or_term_is_not_added():
    f = _filled()
    f.skills[1] = T.SkillLine(category="Fraud and Abuse", terms=["fraud detection and remediation"])
    kp = T.KeywordPass(skills=[T.SkillAdd(category="Fraud and Abuse",
                                          terms=["fraud remediation", "abuse detection"])])
    assert T.apply_keyword_pass(f, kp).skills[1].render()[-1] == "abuse detection"
    assert len(T.apply_keyword_pass(f, kp).skills[1].render()) == 2


def test_one_word_tool_is_never_covered():
    assert not T.covered_by("Azure", ["Azure DevOps CI/CD"])
    assert T.covered_by("backend systems", ["Backend and Data Systems"])
