"""Live Veeva regression: an Accurate result cannot inherit the Anton paragraph context."""
from types import SimpleNamespace
import pytest
from jobagent.tools import cover_letter as C, letter_attribution as L
from jobagent.state import JobDescription

BANK = {
    'employment:0': {'entity': 'Anton Intelligence LLC', 'kind': 'employment',
                     'text': 'Created Playwright tests, cutting CI feedback loops by 25%. Reduced release time by 20%.'},
    'employment:1': {'entity': 'Accurate Industrial Controls Pvt Ltd', 'kind': 'employment',
                     'text': 'Authored Selenium and TestNg tests, increasing test coverage by 35%.'},
}
ANTON = 'At Anton Intelligence LLC, I created Playwright tests, cutting CI feedback loops by 25%. '
WRONG = 'I have also authored automated tests using Selenium and TestNg, increasing test coverage by 35%.'


def letter(*texts):
    return C.Letter(salutation='Dear Hiring Team,', closing='Sincerely',
                    paragraphs=[C.Paragraph(text=t, evidence=[]) for t in [*texts, *(['Thank you.'] * (3-len(texts)))]])


def test_live_cross_employer_figure_inherits_paragraph_context():
    issues = L.paragraph_metric_issues(letter(ANTON + WRONG), BANK)
    assert any('35%' in x and 'Anton Intelligence LLC' in x and 'name the actual source' in x for x in issues)


@pytest.mark.parametrize('text', [
    ANTON + 'At Accurate Industrial Controls Pvt Ltd, ' + WRONG,
    ANTON + 'At Accurate Industrial Controls, ' + WRONG,
    ANTON + 'I also reduced release time by 20%.',
    ANTON + 'I admire the company goal to increase coverage by 35%.',
    ANTON + 'I hope to increase coverage by 35%.',
])
def test_explicit_source_switch_same_source_company_fact_and_future_are_valid(text):
    assert not L.paragraph_metric_issues(letter(text), BANK)


def test_a_new_paragraph_does_not_inherit_an_old_employer():
    assert not L.paragraph_metric_issues(letter(ANTON, WRONG), BANK)


def test_word_percent_spelling_does_not_bypass_context():
    assert L.paragraph_metric_issues(letter(ANTON + WRONG.replace('35%', 'thirty-five percent')), BANK)


def test_default_consistency_uses_the_same_source_context_guard():
    resume = '\n'.join(s['entity'] + '\n' + s['text'] for s in BANK.values())
    issues = C.consistency(letter(ANTON + WRONG), resume, JobDescription(title='Software Engineer', company='Acme'), '', source_bank=BANK)
    assert any('employer/project context' in i for i in issues)


def test_incremental_binding_cannot_override_visible_paragraph_context():
    intro = L.Binding(claim=ANTON.strip(), source_id='employment:0', quote=BANK['employment:0']['text'])
    wrong = L.Binding(claim=WRONG, source_id='employment:1', quote=BANK['employment:1']['text'])
    draft = SimpleNamespace(paragraphs=[SimpleNamespace(text=ANTON+WRONG, bindings=[intro,wrong])])
    assert any('employer/project context' in i for i in L.binding_issues(draft, BANK))


def test_standalone_letter_extracts_employers_without_education_or_skills():
    tex = r'''\section{Experience}
    \resumeSubheading{Engineer}{2026}{Anton Intelligence LLC}{Tempe}
    \resumeItem{Cut CI feedback loops by 25\%.}
    \resumeSubheading{Engineer}{2024}{Accurate Industrial Controls Pvt Ltd}{India}
    \resumeItem{Increased test coverage by 35\%.}
    \end{document}'''
    bank = L.employment_sources(tex)
    assert len(bank) == 2
    assert L.paragraph_metric_issues(letter(ANTON+WRONG), bank)
