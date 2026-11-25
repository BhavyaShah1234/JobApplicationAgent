"""Extraction cannot invent or truncate the browser address used for an application."""
from types import SimpleNamespace

from jobagent.tools.scrape import grounded_apply_url


SOURCE = 'https://jobs.example.test/company/job/software-engineer/1426523300/'


def page(links=()):
    return SimpleNamespace(url=SOURCE, eval_on_selector_all=lambda *args: list(links))


def test_a_truncated_requisition_url_is_replaced_by_the_original_posting():
    truncated = 'https://jobs.example.test/company/job/software-engineer/'
    assert grounded_apply_url(page(), truncated, SOURCE) == SOURCE


def test_an_application_link_actually_exposed_by_the_page_is_retained():
    actual = 'https://identity.example.test/apply/1426523300'
    assert grounded_apply_url(page([actual]), actual, SOURCE) == actual


def test_the_source_posting_needs_no_separate_apply_link():
    assert grounded_apply_url(page(), SOURCE, SOURCE) == SOURCE


def test_missing_dom_does_not_make_an_extracted_link_trustworthy():
    def unavailable(*args):
        raise RuntimeError('page no longer readable')
    p = SimpleNamespace(url=SOURCE, eval_on_selector_all=unavailable)
    assert grounded_apply_url(p, 'https://identity.example.test/invented', SOURCE) == SOURCE
