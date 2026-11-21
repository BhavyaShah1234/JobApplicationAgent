"""Experience quantities: counts, volumes and durations are figures like percentages; versions and standards are not; a result need not be a percentage."""
import pytest

from jobagent.tools import number_claims as NC
from jobagent.tools import tailor as T
from test_figure_provenance import check


@pytest.mark.parametrize("text,token", [
    ("Built 12 REST API endpoints for the platform.", "12endpoint"), ("Owned 3 services.", "3service"), ("Trained fifteen technicians.", "15technician"),
    ("Handled five million events a day.", "5000000event/day"), ("Ran 2,500 tests nightly.", "2500test"), ("Stored 1.5 TB of images.", "1.5tb"),
    ("Cut build time to 6 minutes.", "6minute"), ("Served 40k users.", "40kuser")])
def test_counted_quantities_are_figures(text, token):
    assert token in NC.metric_spans(text)


@pytest.mark.parametrize("text", ["Used Python 3.11 services.", "Met WCAG 2.1 AA and ISO 27001 requirements.", "One of the services failed.", "Targeted Java 17 services.",
                                  "Built it on React 18 components.", "Wrote 5+ years of code.", "Moved to version 2 services."])
def test_versions_standards_and_idioms_are_not_quantities(text):
    assert not NC.quantity_spans(text), text


@pytest.mark.parametrize("text,ok", [
    ("Shipped 12 REST endpoints across 4 services using FastAPI.", True), ("Reduced build time to 40 s with Bazel.", True),
    ("Stabilized the release process using GitHub Actions so deploys no longer needed manual checks.", True),
    ("Built dashboards in Python 3.11.", False), ("Met WCAG 2.1 requirements with React.", False), ("Cut latency 3.5% with Redis.", True),
    ("Worked on dashboards.", False)])
def test_a_result_is_a_quantity_a_change_or_an_outcome_not_a_version(text, ok):
    assert T.has_result(text) is ok, text


def test_a_count_is_bound_to_its_own_role_and_listed_for_confirmation():
    got = check("Shipped 12 REST endpoints across 4 services.", role=1)
    assert any("12" in t for t in got) and any("4 services" in t for t in got), got            # Beta's own bullets have neither figure


def test_a_count_in_a_jd_item_does_not_vouch_for_a_generated_count():
    got = check("Owned 5 services.", role=0)
    assert any("5 services" in t for t in got), got                                            # the JD says '5+ years', which is another figure


def test_a_listed_estimated_count_keeps_its_flag_and_is_not_listed_twice():
    assert check("Shipped 12 REST endpoints.", role=1, estimated=["12 REST endpoints"]) == ["12 REST endpoints"]
    assert check("Shipped 12 REST endpoints.", role=1, estimated=["12"]) == ["12"]             # a legacy bare tag still covers the one figure


def test_a_percentage_tag_cannot_vouch_for_a_count_with_the_same_digits():
    got = check("Owned 25 services.", role=1, estimated=["25%"])
    assert "25%" not in got and any("25 services" in t for t in got), got


def test_the_owners_own_count_in_this_role_is_not_an_estimate():
    got = check("Wrote SQL reports for 3 finance teams.", role=1)
    assert got and any("3" in t for t in got)                                                  # Beta's original bullet has no count: a new one is listed


def test_the_report_shows_the_result_mix_without_checking_it():
    def bullet(text):
        return T.FillBullet.model_construct(text=text, duties=[], original=None, estimated=[])
    f = T.Filled.model_construct(summary="s", skills=[], notes="", roles=[T.FillRole.model_construct(role=0, bullets=[
        bullet("Cut latency 30% using Redis."), bullet("Owned 12 REST endpoints using FastAPI."), bullet("Streamlined releases using GitHub Actions."),
        bullet("Cut cost 20% across 4 services using Terraform."), bullet("Ran builds 3x faster in 40 s using Bazel.")])])
    mix = T.metric_mix(f)
    assert mix == {"bullets": 5, "percent": 2, "quantity": 3, "other": 1, "qualitative": 1}, mix


def test_a_letter_spelling_a_source_count_differently_is_not_an_unknown_number():
    from jobagent.state import JobDescription
    from jobagent.tools import cover_letter as CL
    resume = "Engineer at Atlas Systems LLC. Built twelve REST endpoints with FastAPI and completed Docker builds in forty seconds."
    bank = {"employment:0": {"entity": "Atlas Systems LLC", "kind": "employment", "text": resume}}
    paras = [CL.Paragraph(text=t, evidence=[]) for t in ("I am interested.", "At Atlas Systems LLC, I built 12 REST endpoints with FastAPI and completed Docker builds in 40 seconds.", "Thank you.")]
    letter = CL.Letter(salutation="Dear Hiring Team,", closing="Sincerely", paragraphs=paras)
    assert CL.consistency(letter, resume, JobDescription(title="Engineer", company="Example"), "", source_bank=bank) == []
    bad = CL.Letter(salutation="Dear Hiring Team,", closing="Sincerely", paragraphs=paras[:1] + [CL.Paragraph(text="At Atlas Systems LLC, I built 15 REST endpoints with FastAPI.", evidence=[])] + paras[2:])
    assert CL.consistency(bad, resume, JobDescription(title="Engineer", company="Example"), "", source_bank=bank)


@pytest.mark.parametrize("text", ["Built dashboards in Python 3.11.", "Met WCAG 2.1 requirements with React.", "Targeted Java 17 services.", "Moved to version 2 services.",
                                  "Worked on dashboards."])
def test_the_spelled_count_fallback_keeps_versions_and_standards_negative(text):
    assert T.has_result(text) is False, text


def test_a_spelled_count_is_a_result_but_a_bare_counted_noun_is_not():
    assert T.has_result("Owned twelve REST endpoints using FastAPI.") and T.has_result("Served five million events per day using Kafka.")
    assert T.has_result("Wrote the endpoints using FastAPI.") is False


@pytest.mark.parametrize("compact,full", [("10min", "10 minutes"), ("2hrs", "2 hours"), ("45s", "45 seconds")])
def test_compact_time_units_are_the_same_figure_as_their_full_spelling(compact, full):
    assert NC.metric_tokens(f"Cut it by {compact}.") == NC.metric_tokens(f"Cut it by {full}.") and NC.metric_tokens(f"Cut it by {full}.")


@pytest.mark.parametrize("text", ["Popular in the 90s.", "Since the 1990s the team grew.", "Used Python 3.11s.", "The '80s era.", "Built v2.5s tooling."])
def test_decades_and_version_labels_are_not_compact_durations(text):
    assert not NC.quantity_spans(text), text


def test_a_compact_fractional_duration_is_a_result_and_counts_in_the_report_mix():
    assert T.has_result("Reduced page load time by 1.5s using Redis caching.") is True
    f = T.Filled.model_construct(summary="s", skills=[], notes="", roles=[T.FillRole.model_construct(role=0, bullets=[
        T.FillBullet.model_construct(text="Reduced page load time by 1.5s using Redis caching.", duties=[], original=None, estimated=[])])])
    assert T.metric_mix(f)["quantity"] == 1 and T.metric_mix(f)["qualitative"] == 0
