"""A compliance-mapping capability is not an achieved generic compliance guarantee."""
import pytest
from jobagent.tools import projects as P

MAPPING_CARD = {'summary': 'The pipeline refines requirements, plans architecture with compliance mapping for SOC 2/HIPAA/ISO 27001, generates Terraform using a TFLint/Checkov validation loop, and deploys through CloudFormation.', 'results': []}

@pytest.mark.parametrize('claim', [
    'The system uses a multi-agent pipeline to refine requirements, plan architecture, generate Terraform code, and deploy resources, ensuring compliance and validation at each stage.',
    'The system guarantees compliance.',
    'The system ensures conformance.',
])
def test_actual_pipeline_and_equivalent_generic_guarantees_need_an_own_outcome(claim):
    assert P.standard_outcome_claims(claim,MAPPING_CARD)

@pytest.mark.parametrize('source', [
    'The system does not ensure compliance.',
    'The team is working toward compliance and plans to ensure compliance.',
    'The system meets accessibility standards.',
])
def test_negated_planned_or_other_outcome_evidence_cannot_vouch_for_compliance(source):
    assert P.standard_outcome_claims('The system ensures compliance.',{'summary':source})

@pytest.mark.parametrize('claim', [
    'The system does not guarantee compliance.',
    'The team is working toward compliance and plans to ensure compliance.',
    'The system provides compliance mapping for SOC 2 and HIPAA.',
    'The platform ensures scalability and ease of use.',
    'Playwright ensures robust quality assurance, aligning with the job focus on automation testing.',
])
def test_capability_plans_denials_and_ordinary_qualitative_benefits_remain_allowed(claim):
    assert not P.standard_outcome_claims(claim,MAPPING_CARD)


def test_an_own_affirmative_achieved_outcome_supports_the_same_generic_compliance_claim():
    assert not P.standard_outcome_claims('The system ensures compliance.',{'results':['The system achieved full compliance after an independent audit.']})


def test_existing_unnamed_standard_outcomes_still_work_and_denials_do_not_vouch():
    claim='The system meets industry standards.'
    assert not P.standard_outcome_claims(claim,{'results':['The system meets industry standards.']})
    assert P.standard_outcome_claims(claim,{'results':['The system does not meet industry standards.']})
