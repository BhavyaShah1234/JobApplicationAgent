"""A mailing country or visa answer cannot prove possession of work documents."""
from types import SimpleNamespace

import pytest

from jobagent.filling.facts import Decision, Fact
from jobagent.filling.planned import Mappings, Mapping, PlannedFiller, Supported
from jobagent.filling.routing import binding_valid, resolve_routed, route
from jobagent.tools.forms import Field


QUESTION = ('If you are offered employment by an employer, can you provide documentation '
            'establishing your identity and right to work in the country where this position will be based?')
OWNER_QUESTION = 'Can you provide documents proving your identity and right to work?'


def field(label=QUESTION):
    return Field(id=0, kind='select', label=label, options=['Yes', 'No'])


@pytest.mark.parametrize('alias,value', [
    ('Country', 'United States of America'),
    ('Are you legally authorized to work in the United States?', 'Yes'),
    ('Will you now require sponsorship?', 'No'),
    ('Can you provide proof of your identity?', 'Yes'),
    ('Can you provide documentation establishing your right to work?', 'Yes'),
])
def test_other_facts_do_not_establish_identity_and_work_documentation(alias, value):
    wrong = Fact('wrong', [alias], value, 'applicant/profile')
    f = field()
    r = route(f, [wrong])
    assert r.family == 'work_documentation'
    assert resolve_routed(f, [wrong]).value is None
    assert not binding_valid(f, [wrong], 'Yes', r)


@pytest.mark.parametrize('answer', ['Yes', 'No'])
def test_explicit_owner_document_answer_keeps_its_own_source(answer):
    right = Fact('owner.documents', [OWNER_QUESTION], answer, 'applicant/work_authorization')
    result = resolve_routed(field(), [right])
    assert (result.value, result.fact_id) == (answer, right.id)


def test_current_sheet_conflict_and_non_owner_document_claims_remain_distinct():
    right = Fact('owner', [OWNER_QUESTION], 'Yes', 'applicant/work_authorization')
    current = Fact('application.answer.1', [QUESTION], 'No', 'applicant/application/answers.md')
    assert resolve_routed(field(), [right, current]).fact_id == current.id
    resume = Fact('resume', [OWNER_QUESTION], 'Yes', 'resume', authority='resume')
    assert resolve_routed(field(), [resume]).value is None


def test_source_resolver_and_accepting_model_cannot_use_country_as_evidence():
    wrong = Fact('country', ['Country'], 'United States of America', 'applicant/address')

    class BadModel:
        model_name = 'fixture'

        def with_structured_output(self, schema, **kwargs):
            return SimpleNamespace(invoke=lambda _: Mappings(mappings=[Mapping(
                field_id=0, fact_id=wrong.id, value='Yes')])
                if schema is Mappings else Supported(supported_ids=[0]))

    planner = PlannedFiller([wrong], lambda _: {}, BadModel(),
        source_resolver=lambda *_: {0: Decision('Yes', wrong.source, wrong.id)})
    assert planner.plan([field()], allow_human=False)[0].value is None


def test_employer_immigration_document_support_is_a_separate_predicate():
    q = ('Do you need immigration support that includes employer documentation '
         'for F-1 OPT, STEM OPT or CPT?')
    assert route(field(q), []).family == 'immigration_support'
