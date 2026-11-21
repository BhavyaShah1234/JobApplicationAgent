"""A project's paragraph may say what the project uses, tests or audits against; an ACHIEVED standard needs the project's own card to show it."""
import pytest

from jobagent.tools import projects as P

WCAG_CARD = {"full_name": "owner/platform", "tech_stack": ["Next.js", "Playwright + @axe-core/playwright (E2E + WCAG 2.1 AA audits)", "Vitest + Testing Library"],
             "results": [], "summary": "A multi-tenant web platform for an organisation."}


def state(text, *, short=None, paragraph=""):
    entry = {"repo": "owner/platform", "paragraph": paragraph or text}
    if short:
        entry["short"] = short
    return {"ranked": [entry], "kept": 1, "short": ["owner/platform"] if short else []}


def found(text, card=WCAG_CARD, **kw):
    return [f for f in P.content_findings(state(text, **kw), {"owner/platform": card}) if "achieved a standard" in f or "does not show" in f]


@pytest.mark.parametrize("text", [
    "Built with Next.js. The platform achieved high accessibility standards with Playwright and WCAG 2.1 AA audits.",
    "It meets industry standards and was audited with WCAG 2.1 AA tooling.",
    "The platform is WCAG 2.1 AA compliant, checked by Playwright audits.",
    "Delivered a WCAG 2.1 AA conformant interface using axe-core."])
def test_tooling_only_evidence_does_not_support_an_achieved_standard(text):
    assert found(text), text


@pytest.mark.parametrize("text", [
    "Tested accessibility with Playwright and axe-core WCAG 2.1 AA audits.",
    "Includes Playwright end-to-end tests with axe-core WCAG 2.1 AA audits.",
    "Audits pages against WCAG 2.1 AA in CI using Playwright.",
    "Reduced manual effort and accelerated deployment with generated infrastructure.",
    "Achieved sub-second report generation using Snowflake archetypes.",
    "Delivered a drag-and-drop report designer with a comprehensive test suite.",
    "Passed all unit tests in CI and supports local and cloud-based LLM providers."])
def test_capability_wording_and_ordinary_benefits_are_not_flagged(text):
    assert not found(text), text


def test_an_achieved_standard_is_allowed_when_the_card_itself_shows_the_outcome():
    card = {**WCAG_CARD, "summary": "CI runs axe-core and reports zero WCAG 2.1 AA violations on every page; the platform conforms to WCAG 2.1 AA."}
    assert not found("The platform achieved WCAG 2.1 AA conformance, verified by Playwright audits.", card)
    card2 = {**WCAG_CARD, "results": ["Passed WCAG 2.1 AA audit on all public pages"]}
    assert not found("Passed WCAG 2.1 AA audits on all public pages.", card2)


@pytest.mark.parametrize("claim,card_text,ok", [
    ("A HIPAA-compliant data pipeline for patient records.", "Logs access to PHI and encrypts data at rest.", False),
    ("A HIPAA-compliant data pipeline for patient records.", "The README states the service is HIPAA compliant.", True),
    ("Implemented audit logging aligned with HIPAA requirements.", "Logs access to PHI.", True),
    ("Achieved SOC 2 certification for the hosted service.", "Terraform modules with encryption and access reviews.", False),
    ("Implemented access controls mapped to SOC 2 criteria.", "Terraform modules with access reviews.", True),
    ("Achieved SOC 2 Type II certification for the hosted service.", "The company achieved SOC 2 Type II certification in 2025.", True),
    ("GDPR-compliant consent management.", "Consent and data-subject export endpoints.", False)])
def test_other_named_standards_follow_the_same_rule(claim, card_text, ok):
    card = {"full_name": "owner/platform", "summary": card_text, "tech_stack": [], "results": []}
    assert bool(found(claim, card)) is (not ok), claim


def test_a_standard_the_card_mentions_does_not_vouch_for_a_different_standard():
    card = {**WCAG_CARD, "summary": "The service is HIPAA compliant."}
    assert found("The platform achieved WCAG 2.1 AA conformance.", card)


def test_a_generic_standards_claim_needs_the_card_to_say_something_similar():
    assert found("The service meets industry standards for reliability.", {"full_name": "o/p", "summary": "A service.", "tech_stack": [], "results": []})
    assert not found("The service meets industry standards for reliability.", {"full_name": "o/p", "summary": "Meets industry standards for uptime.", "tech_stack": []})


def test_the_displayed_short_variant_is_the_one_checked():
    claim = "The platform achieved high accessibility standards with WCAG 2.1 AA audits."
    ok = "Tested accessibility with Playwright and WCAG 2.1 AA audits."
    assert found(ok, short=claim, paragraph=ok)                                              # the short form is rendered and carries the claim
    assert not found(claim, short=ok, paragraph=claim)                                      # the long form is not rendered, so its claim is not displayed


def test_without_cards_or_without_the_projects_card_nothing_new_is_reported_and_completeness_is_still_checked():
    claim = "The platform achieved high accessibility standards with WCAG 2.1 AA audits."
    assert not P.content_findings(state(claim))
    assert not P.content_findings(state(claim), {})
    assert any("complete sentence" in f for f in P.content_findings(state("It does things and"), None))


def test_the_instruction_tells_the_writer_what_to_say_instead():
    msg = P.content_findings(state("The platform achieved high accessibility standards with WCAG 2.1 AA audits."), {"owner/platform": WCAG_CARD})[0]
    assert "uses, tests or audits against" in msg and "project 1" in msg


@pytest.mark.parametrize("claim", ["Not yet HIPAA compliant; logs access to PHI.", "The platform is not WCAG 2.1 AA compliant and was audited with axe-core.",
                                    "Working toward SOC 2 certification with documented controls.", "Designed to meet GDPR requirements for consent.",
                                    "Lacks HIPAA certification but encrypts data at rest."])
def test_a_denied_or_planned_standard_is_not_an_achievement_claim(claim):
    assert not found(claim, {"full_name": "owner/platform", "summary": "A service.", "tech_stack": []}), claim


def test_a_card_line_that_denies_the_standard_does_not_support_the_claim():
    card = {"full_name": "owner/platform", "summary": "The service is not HIPAA compliant yet.", "tech_stack": [], "results": []}
    assert found("A HIPAA-compliant data pipeline.", card)
    card2 = {**card, "summary": "The service is HIPAA compliant."}
    assert not found("A HIPAA-compliant data pipeline.", card2)


@pytest.mark.parametrize("claim,card_text,ok", [
    ("The platform achieved WCAG 2.2 AA conformance.", "CI reports zero WCAG 2.1 AA violations; conforms to WCAG 2.1 AA.", False),
    ("The platform achieved WCAG 2.1 AA conformance.", "CI reports zero WCAG 2.1 AA violations; conforms to WCAG 2.1 AA.", True),
    ("The platform achieved WCAG 2.1 AAA conformance.", "Conforms to WCAG 2.1 AA.", False),
    ("The platform achieved WCAG conformance.", "Conforms to WCAG 2.1 AA.", True),
    ("Achieved ISO 27001 certification.", "The company is ISO/IEC 27001 certified.", True),
    ("Achieved ISO 9001 certification.", "The company is ISO/IEC 27001 certified.", False),
    ("SOC2 compliant hosting.", "Hosting is SOC 2 compliant.", True)])
def test_version_level_and_name_variants_are_compared(claim, card_text, ok):
    card = {"full_name": "owner/platform", "summary": card_text, "tech_stack": [], "results": []}
    assert bool(found(claim, card)) is (not ok), (claim, card_text)


def test_a_capability_phrase_is_not_an_achievement_in_a_card_or_in_a_claim():
    card = {"full_name": "owner/platform", "summary": "Compliance mapping for SOC 2/HIPAA/ISO 27001.", "results": []}
    assert found("The project achieved HIPAA compliance.", card)                              # mapping controls to HIPAA is not being HIPAA compliant
    card2 = {"full_name": "owner/platform", "summary": "Maps controls to HIPAA.", "results": []}
    assert not found("The system provides HIPAA compliance mapping.", card2)                  # the claim itself is a capability
    assert not found("Includes a HIPAA compliance checker and SOC 2 certification tracking.", card2)


def test_generic_or_differently_leveled_evidence_cannot_establish_a_specific_version_or_level():
    assert found("The project conforms to WCAG 2.2 AAA.", {"full_name": "owner/platform", "results": ["The project conforms to WCAG."]})
    assert found("The project conforms to WCAG 2.1 AAA.", {"full_name": "owner/platform", "results": ["The project conforms to wcag 2.1 aa."]})
    assert not found("The project conforms to WCAG 2.1 AA.", {"full_name": "owner/platform", "results": ["The project conforms to wcag 2.1 aa."]})
