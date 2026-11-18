"""Narrative metadata must not invalidate or alter an otherwise parseable field plan."""
import pytest
from pydantic import ValidationError,create_model
from jobagent.steps.apply import Answer


def test_long_explanations_preserve_full_evidence_and_the_application_value():
    schema=create_model('ApplicationFieldPlan',field_10=(Answer,...))
    value='A complete answer. '*100
    why='The owner supplied this exact answer. '*20
    flag='Review this qualification against its stated time window. '*10
    result=schema.model_validate({'field_10':{'id':10,'source':'applicant','value':value,'why':why,'flag':flag}})
    assert result.field_10.value==value
    assert result.field_10.why==why and result.field_10.flag==flag
    assert result.field_10.source=='applicant' and result.field_10.id==10


@pytest.mark.parametrize('patch',[{'why':None},{'flag':{}},{'source':'unverified_approved'},{'value':None}])
def test_tolerating_long_metadata_does_not_accept_invalid_types_or_provenance(patch):
    with pytest.raises(ValidationError):
        Answer.model_validate({'id':10,'source':'applicant','value':'Yes',**patch})
