"""F18: one set of length budgets for the schemas, prompts, repair requests and validators."""
import pytest
from pydantic import ValidationError

from jobagent.state import JobDescription
from jobagent.steps import multi_agent as M
from jobagent.tools import tailor as T


def bullet(text, original=None, duties=(0,)):
    return T.FillBullet(text=text, duties=list(duties), original=original)


# --- the contract is the same everywhere ---------------------------------------------------------------------------------

def test_a_new_bullet_over_the_budget_is_refused_with_the_measured_length_and_the_limit():
    ok = "Built a retrieval pipeline cutting analyst search time by an estimated 40 percent across three teams"
    assert len(ok) <= T.BULLET_MAX and bullet(ok)
    with pytest.raises(ValidationError) as e:
        bullet("x" * (T.BULLET_MAX + 1) + " tail")
    msg = str(e.value)
    assert f"{T.BULLET_MAX + 6} characters" in msg and f"at most {T.BULLET_MAX}" in msg and "shorten it" in msg


def test_a_template_bullet_copied_verbatim_keeps_its_length():
    long_original = "y" * 180
    assert bullet(long_original, original=0).text == long_original
    with pytest.raises(ValidationError):
        bullet("y" * (T.BULLET_VERBATIM_MAX + 1), original=0)          # the schema ceiling still holds


def test_the_summary_schema_and_the_validator_use_the_same_ceiling():
    assert M.SUMMARY_MAX == T.SUMMARY_MAX == 450
    over = "A" * 20 + ". " + "B" * 440
    assert len(over) > T.SUMMARY_MAX
    with pytest.raises(ValidationError):
        M.SummaryOut(summary=over)
    assert M.summary_issues(over, False)
    fine = "Engineer who builds data platforms. Delivered pipelines at scale. Skilled in Python and SQL."
    assert M.SummaryOut(summary=fine) and M.summary_issues(fine, False) == []
    # anything the schema accepts is not then refused by the length rule
    edge = ("S" * 100 + ". ") * 2 + "S" * 120
    assert len(edge) <= T.SUMMARY_MAX and not any("characters" in x for x in M.summary_issues(edge, False))


def test_the_prompts_and_field_descriptions_quote_the_budgets_they_enforce():
    jd = JobDescription(company="Acme", title="Engineer")
    fill = T.FILL_PROMPT.format(max_bullets=12, bullet_target=T.BULLET_TARGET, bullet_max=T.BULLET_MAX)
    assert f"~{T.BULLET_TARGET} characters, never more than {T.BULLET_MAX}" in fill
    assert str(T.BULLET_MAX) in T.REPAIR_PROMPT and str(T.SUMMARY_TARGET) in T.KEYWORD_PROMPT and "{" not in T.REPAIR_PROMPT
    assert f"{T.PARAGRAPH_MIN}-{T.PARAGRAPH_MAX}" in M.PROJECTS_EDIT_PROMPT and "{PMIN}" not in M.PROJECTS_EDIT_PROMPT
    assert T.BulletFix.model_fields["text"].metadata[0].max_length == T.BULLET_MAX
    assert T.ProjectFix.model_fields["paragraph"].metadata[0].max_length == T.PARAGRAPH_MAX
    assert T.KeywordPass.model_fields["summary"].metadata[0].max_length == T.SUMMARY_MAX
    assert str(T.BULLET_MAX) in T.budget_text() and str(T.PARAGRAPH_MAX) in T.budget_text()
    del jd


def test_the_summary_overflow_rule_uses_the_shared_tight_budget():
    mid = "S" * (T.SUMMARY_TIGHT + 60)
    issues = M.summary_issues(mid + ".", True)
    assert any(f"at most {T.SUMMARY_TIGHT} characters" in i for i in issues)


# --- infeasible combinations are found before the models are called ------------------------------------------------------

class FakeResume:
    def __init__(self, roles):
        self.roles = [object()] * roles


def jd_with(n_required):
    return JobDescription(company="Acme", title="Engineer", responsibilities=[f"Own system number {i} end to end" for i in range(n_required)])


def test_required_items_beyond_what_the_bullets_can_hold_are_reported_and_not_demanded():
    jd = jd_with(30)
    needed, dropped = T.coverage_target(jd, 8)                    # 8 bullets x 3 items = 24
    assert len(needed) == 24 and dropped == list(range(24, 30))
    problems = T.feasibility(FakeResume(3), jd, 8)
    assert any("cannot be covered" in p and "24" in p for p in problems)
    assert T.feasibility(FakeResume(3), jd_with(6), 12) == []


def test_too_few_bullets_for_the_roles_is_infeasible():
    assert any("at least" in p for p in T.feasibility(FakeResume(5), jd_with(3), 9))


def test_fill_checks_only_expects_what_the_page_can_hold():
    jd = jd_with(30)

    class R:
        tex = ""
    # a resume that covers items 0..23 (all 8 slots x 3) meets the target; without the limit it would look 6 items short
    f = type("F", (), {})()
    f.summary = ""
    f.skills = []
    f.roles = [type("Role", (), {"bullets": [bullet("Built system " + str(i) + " end to end", duties=(3 * i, 3 * i + 1, 3 * i + 2))
                                              for i in range(8)]})()]
    assert T.fill_checks(R(), jd, f)[0] == list(range(24, 30))
    assert T.fill_checks(R(), jd, f, 8)[0] == []


# --- F18 follow-up: an `original` index does not exempt new text ---------------------------------------------------------

class TemplateResume:
    def __init__(self):
        self.roles = [type("Role", (), {"bullets": ["Built the original data pipeline for the team."]})()]
        self.skills = []


def test_new_text_that_claims_an_original_index_is_still_held_to_the_budget(monkeypatch):
    r = TemplateResume()
    long_new = "New and changed text " + "x" * 160
    b = bullet(long_new, original=0)
    assert len(b.text) == 181 and b.original == 0                               # the schema alone accepts it (ceiling 300)
    assert not T.is_verbatim(r, 0, b)
    f = type("F", (), {})()
    f.summary, f.skills, f.roles = "", [], [T.FillRole(role=0, bullets=[b, T.FillBullet(text="Built the original data pipeline for the team.", duties=[0], original=0)])]
    T.normalize_originals(r, f)
    assert b.original is None and f.roles[0].bullets[1].original == 0           # the altered one lost the claim; the true copy keeps it
    jd = JobDescription(company="Acme", title="Engineer")
    issues = T.quality(r, jd, f)
    assert any("181 characters" in i and f"at most {T.BULLET_MAX}" in i for i in issues)
    assert not any("original data pipeline" in i and "characters;" in i for i in issues)


def test_a_verbatim_template_bullet_longer_than_the_budget_is_still_exempt():
    r = TemplateResume()
    r.roles[0].bullets = ["z " * 90 + "end"]
    b = T.FillBullet(text=T.to_plain(r.roles[0].bullets[0]), duties=[0], original=0)
    assert len(b.text) > T.BULLET_MAX and T.is_verbatim(r, 0, b)
