import pytest

from jobagent.filling.facts import Fact, expanded_immigration_support
from jobagent.filling.routing import resolve_routed, route
from jobagent.tools.forms import Field


QUESTION = ('Will you in the future require immigration sponsorship by our company to attain or maintain '
            'your employment eligibility (e.g., H-1B, E-3, TN, O-1, STEM OPT, or any immigration work '
            'authorization requiring a written submission from the company to a government agency)?')


@pytest.mark.parametrize('question', [
    QUESTION,
    QUESTION.replace('in the future', 'now'),
    'Do you require sponsorship (for example, STEM OPT or CPT)?',
])
def test_opt_example_lists_require_the_broader_owner_fact(question):
    field = Field(id=0, kind='select', label=question, options=['Yes', 'No'])
    ordinary = Fact('ordinary', ['Will you in the future require sponsorship?'], 'No', 'applicant/work_authorization')
    assert expanded_immigration_support(question)
    assert route(field, [ordinary]).family == 'immigration_support'
    assert resolve_routed(field, [ordinary]).value is None


def test_exact_owner_answer_can_resolve_the_expanded_question():
    field = Field(id=0, kind='select', label=QUESTION, options=['Yes', 'No'])
    ordinary = Fact('ordinary', ['Will you in the future require sponsorship?'], 'No', 'applicant/work_authorization')
    explicit = Fact('application.answer.1', [QUESTION], 'Yes', 'applicant/application/answers.md')
    result = resolve_routed(field, [ordinary, explicit])
    assert result.value == 'Yes' and result.fact_id == explicit.id


@pytest.mark.parametrize('question', [
    'Will you require visa sponsorship (e.g., H-1B or O-1)?',
    'Will you require visa sponsorship (e.g., H-1B, but not OPT or CPT)?',
    'Will you require visa sponsorship (for example H-1B; OPT is excluded)?',
    'What is your current OPT expiration date?',
])
def test_ordinary_sponsorship_and_excluded_opt_examples_are_distinct(question):
    assert not expanded_immigration_support(question)
