"""Metric-direction contradictions: detector scope, shared quality gate, repair path, incremental ingress, letter, protected originals."""
import re
from types import SimpleNamespace

import pytest

from jobagent.state import JobDescription
from jobagent.steps import multi_agent as M
from jobagent.tools import cover_letter as CL
from jobagent.tools import metric_direction as MD
from jobagent.tools import tailor as T

GOOD = "Built Python event handlers, reducing processing latency by 10%."
BAD = "Built scalable backend systems in Golang, increasing API response time by 28%."
JD = JobDescription(title="Software Engineer", company="Example", responsibilities=["Build backend services"],
                    technical_skills=["Python", "Golang"])


def template(original=None):
    bullets = [original] if original else []
    return SimpleNamespace(skills=[], tex="", roles=[SimpleNamespace(company="Example Lab", title="Engineer", dates="2024", bullets=bullets)])


def filled(*texts, original=None, estimated=True):
    bullets = [T.FillBullet(text=t, estimated=re.findall(r"\d+%", t) if estimated else [], duties=[0],
                            original=original if t == texts[0] else None) for t in texts]
    return T.Filled(summary="Software Engineer with Python experience.", roles=[T.FillRole(role=0, bullets=bullets)],
                    skills=[T.SkillLine(category="Languages", terms=["Python", "Golang"]), T.SkillLine(category="Databases", terms=["SQL"]),
                            T.SkillLine(category="Cloud", terms=["AWS"])], notes="n")


@pytest.mark.parametrize("text", [
    BAD, "API response time increased by 28% after the Golang rewrite.", "Raised p99 latency by 12 percent in Go services.",
    "Increased downtime by 3x with Kubernetes.", "Cut Kubernetes cluster uptime by 5%.", "Grew infrastructure cost by 40% on AWS.",
    "Lowered Python service availability by 80 ms.", "Boosted failure rate by 15% in Python.",
])
def test_clear_contradictions_are_found_in_either_word_order_and_unit(text):
    assert MD.contradictions(text)


@pytest.mark.parametrize("text", [
    "Built Golang services, cutting API response time by 28%.", "Scaled Go services without increasing latency by more than 2%.",
    "Mitigated the rise in latency by 10% with caching in Go.", "Cut yield loss by 10% in Python.",
    "Increased cost savings by 15% with Terraform.", "Increased CPU utilization by 20% and cache size by 2x in Go.",
    "Allocated 3x more memory to Python workers.", "Increased throughput by 30%, reducing latency by 12% in Go.",
    "Reduced latency by increasing throughput by 20% in Go.", "Increased cache hit rate by 10%, lowering response time by 30%.",
    "Response time dropped 28% after the Golang rewrite.", "Increased throughput by 20% and reducing error rate by 15% in Python.",
    "Reduced cost by 20%, accepting a 5% increase in response time.", "Injected latency into Python services to test retries, raising response time by 20%.",
    "Measured a Go regression, with response time increasing by 28%.",
])
def test_benefits_negations_ambiguous_metrics_tradeoffs_and_tests_are_allowed(text):
    assert MD.contradictions(text) == []


def test_one_clauses_increase_never_governs_another_clauses_decrease():
    assert MD.contradictions("Increased throughput by 20% and reducing latency by 10% in Go.") == []
    assert MD.contradictions("Increased throughput by 20%, but latency rose 5% in Python.")      # the second clause genuinely worsens latency


@pytest.mark.parametrize("estimated", [True, False])
@pytest.mark.parametrize("text", [BAD, BAD.rstrip("."), BAD.upper()])
def test_quality_rejects_the_contradiction_whatever_the_estimate_metadata_or_final_period(text, estimated):
    f = filled(text, GOOD, estimated=estimated)
    before = f.model_dump()
    assert any("metric-direction" in i for i in T.quality(template(), JD, f))
    assert f.model_dump() == before                      # never mutated


def test_quality_still_passes_the_corrected_bullet_and_is_not_company_or_api_specific():
    assert T.quality(template(), JD, filled(BAD.replace("increasing", "cutting"), GOOD)) == []
    other = "Built Go services at Zeta, increasing page load time by 12%."
    assert any("metric-direction" in i for i in T.quality(template(), JD, filled(other, GOOD)))


def test_a_verbatim_owner_original_is_never_judged_or_flipped():
    honest = "Built a Python prototype that increased response time by 8\\% under load."
    plain = T.to_plain(honest)
    f = filled(plain, GOOD, original=0)
    assert T.quality(template(honest), JD, f) == []
    assert f.roles[0].bullets[0].text == plain
    assert any("metric-direction" in i for i in T.quality(template(honest), JD, filled(plain + " ", BAD)))  # same words as NEW text are judged


def test_repair_prompt_carries_the_finding_and_the_direction_rule_and_apply_repair_is_a_plain_rewrite():
    f = filled(BAD, GOOD)
    issues = T.quality(template(), JD, f)
    prompt = "\n".join(str(m.content) for m in T.build_repair_prompt(template(), JD, f, issues))
    assert "metric-direction contradiction" in prompt and "beneficial way" in prompt and "never present a worse metric" in prompt
    assert "Permission to estimate never allows a metric to move the wrong way" in T.facts_block()
    fix = T.Repair(fixes=[T.BulletFix(role=0, bullet=1, text="Built Golang APIs, cutting response time by 28%.", estimated=["28%"])])
    assert T.apply_repair(f, fix) == 1 and T.quality(template(), JD, f) == []
    assert f.roles[0].bullets[0].estimated == ["28%"]    # an estimate stays listed for owner confirmation


def _fix(text):
    return SimpleNamespace(fixes=[SimpleNamespace(role=0, bullet=1, text=text, estimated=re.findall(r"\d+%", text), duties=[0])])


def test_incremental_repair_cannot_introduce_the_contradiction_but_may_fix_it():
    with pytest.raises(ValueError, match="quality defects"):
        M.validate_incremental_repair(filled(GOOD, "Built Go jobs, cutting build time by 20%."), JD, _fix(BAD), template=template())
    M.validate_incremental_repair(filled(BAD, GOOD), JD, _fix("Built Golang APIs, cutting response time by 28%."), template=template())


def test_final_acceptance_gate_blocks_the_contradiction_through_hard_issues_quality():
    assert any("metric-direction" in i for i in T.quality(template(), JD, filled(BAD, GOOD)))


RESUME = "Jane Doe. Built Golang APIs, cutting API latency by 20%. Python services improving throughput by 30%."


def letter(*s):
    return CL.Letter(salutation="Dear Team,", closing="Sincerely", paragraphs=[
        CL.Paragraph(text="I am excited to apply.", evidence=["x"]), CL.Paragraph(text=" ".join(s), evidence=["x"]),
        CL.Paragraph(text="Thank you for your time.", evidence=["x"])])


def test_a_letter_cannot_invert_or_worsen_a_resume_metric():
    assert not any("metric-direction" in i for i in CL.consistency(letter("I cut API latency by 20% in Golang."), RESUME, JD, ""))
    for s in ("I raised API latency by 20% in Golang.", "I reduced Python throughput by 30%."):
        assert any("metric-direction" in i for i in CL.consistency(letter(s), RESUME, JD, "")), s
    assert any("reverses" in i for i in CL.consistency(letter("My Golang work lowered uptime by 20%, and I raised latency by 20%."), RESUME, JD, ""))
