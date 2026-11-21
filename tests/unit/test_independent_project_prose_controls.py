"""Independent controls authored while Claude implements the source-prose fix; existing APIs only."""
from jobagent.tools import projects as P


def findings(text, own_stack=('Python', 'FastAPI'), *, short=None, use_short=False, own_summary=None):
    entry={'repo':'owner/selected','title':'Selected','stack':['Python'],
           'paragraph':text,'short':short or text}
    state={'ranked':[entry],'kept':1,'short':['owner/selected'] if use_short else []}
    cards={'owner/selected':{'full_name':'owner/selected','summary':own_summary or 'Built a Python FastAPI scoring service.',
                             'tech_stack':list(own_stack)},
           'owner/other':{'full_name':'owner/other','summary':'A separate React Native frontend using TypeScript and .NET.',
                          'tech_stack':['React','React Native','TypeScript','.NET']}}
    return P.content_findings(state,cards)


def test_prose_cannot_borrow_react_from_another_project_after_the_stack_is_grounded():
    assert findings('Built a Python scoring service with a React frontend.')


def test_a_supported_project_technology_in_prose_is_kept():
    assert not findings('Built a React frontend and Python API.',own_stack=('Python','React'))


def test_react_does_not_establish_react_native():
    assert findings('Built a React Native frontend and Python API.',own_stack=('Python','React'))


def test_an_ordinary_lowercase_verb_is_not_a_framework_claim():
    assert not findings('Built a Python service that can react to changing inputs.')


def test_a_denied_technology_is_not_a_claim_to_have_used_it():
    assert not findings('Built a Python service without React or TypeScript.')


def test_only_the_selected_short_variant_is_judged():
    assert not findings('Built a React frontend and Python API.',short='Built a Python API.',use_short=True)


def test_a_bad_selected_short_variant_cannot_hide_behind_a_supported_full_one():
    assert findings('Built a Python scoring API.',short='Built a React frontend.',use_short=True)


def test_readme_evidence_for_the_same_project_can_support_its_prose():
    assert not findings('Built a React frontend and Python API.',own_summary='Built a Python API and React frontend.')


def test_dotnet_punctuation_does_not_borrow_unrelated_net_word():
    assert findings('Built a .NET service with Python APIs.',own_summary='Built a Python service. Net latency is reported.')


def test_an_adjectival_compliance_guarantee_still_needs_its_own_achievement():
    assert P.standard_outcome_claims('The system ensures deterministic and compliant cloud setups.',
                                    {'summary':'It maps compliance controls and validates Terraform configuration.'})


def test_an_actual_same_source_compliant_outcome_is_not_blanket_banned():
    text='The system ensures compliant cloud setups.'
    assert not P.standard_outcome_claims(text,{'results':[text]})


def test_planned_adjectival_compliance_is_not_already_achieved():
    assert not P.standard_outcome_claims('The system is designed to ensure compliant cloud setups.',
                                        {'summary':'It maps compliance controls and validates configuration.'})


def test_ordinary_qualitative_benefits_are_still_permitted():
    assert not P.standard_outcome_claims('The system ensures reliable scoring and improves deployment reliability.',
                                        {'summary':'Built a scoring service and deployment pipeline.'})


def test_work_drafting_permission_cannot_dismiss_a_project_implementation_error(tmp_path,monkeypatch):
    import pytest
    from test_generation_reliability import _project_world,_verdict
    from jobagent.steps import multi_agent as M
    R,ctx,value=_project_world(tmp_path,monkeypatch,'Built a React frontend.')
    finding=M.Finding(agent='projects',severity='error',issue='React is not shown by this project card.')
    monkeypatch.setattr(R.llm,'structured_invoke',lambda model,schema,messages,**kw:
        schema.model_validate(_verdict(0,'rejected','The owner permits technical duties and impact estimates.',source_id='owner_policy')))
    with pytest.raises(ValueError,match='(?i)repositor|own.*source'):
        R.adjudicate(ctx,value,[finding])


def test_a_project_rejection_still_can_cite_its_own_supporting_repository(tmp_path,monkeypatch):
    from test_generation_reliability import _project_world,_verdict
    from jobagent.steps import multi_agent as M
    R,ctx,value=_project_world(tmp_path,monkeypatch,'Built a React frontend.')
    ctx['cards']['ak-asu/cloudforge']['summary']='Built a React frontend.'
    finding=M.Finding(agent='projects',severity='error',issue='React is not shown by this project card.')
    monkeypatch.setattr(R.llm,'structured_invoke',lambda model,schema,messages,**kw:
        schema.model_validate(_verdict(0,'rejected','This same repository documents React.')))
    assert R.adjudicate(ctx,value,[finding])==[]


def test_a_source_denial_is_not_affirmative_technology_evidence():
    assert findings('Built a React frontend.',own_summary='Built a Python API. It does not use React.')


def test_a_source_future_plan_does_not_establish_a_present_implementation():
    assert findings('Built a React frontend.',own_summary='Built a Python API. React is planned for a future release.')
