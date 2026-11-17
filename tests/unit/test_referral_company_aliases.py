"""Referral contacts require established company identity, never a convenient partial name."""
import csv
from types import SimpleNamespace
import pytest
from jobagent.tools import referrals as R


@pytest.fixture
def contacts(tmp_path,monkeypatch):
    monkeypatch.setattr(R,'get_settings',lambda:SimpleNamespace(data_dir=tmp_path))
    with (tmp_path/'asu_contacts.csv').open('w') as f:
        writer=csv.DictWriter(f,fieldnames=['company','name','email']);writer.writeheader()
        writer.writerows([{'company':'Example Systems','name':'Old Name Employee','email':'old@example.test'},
                         {'company':'Example','name':'Exact Employee','email':'exact@example.test'},
                         {'company':'Example Systems North','name':'Different Employer','email':'other@example.test'}])
    return tmp_path/'company_aliases.yaml'


def test_aliases_are_explicit_and_exact_contacts_remain_preferred(contacts):
    contacts.write_text('Example:\n  aliases: [Example Systems, Example New Name]\n')
    assert R.contact_for_company('Example')['name']=='Exact Employee'
    assert R.contact_for_company('Example New Name')['name']=='Old Name Employee'
    assert R.company_keys('Example Systems North')=={'examplesystemsnorth'}


def test_no_suffix_or_substring_guess_can_select_a_referral_contact(contacts):
    assert R.contact_for_company('Example New Name')=={}
    assert R.company_keys('Example Systems')=={'examplesystems'}


@pytest.mark.parametrize('config',[
    'Example:\n  aliases: [Shared Name]\nOther Employer:\n  aliases: [Shared Name]\n',
    'Example:\n  aliases: Shared Name\n',
    '- Shared Name\n',
    'Example: [broken',
])
def test_ambiguous_or_malformed_aliases_do_not_expand_identity(contacts,config):
    contacts.write_text(config)
    assert R.company_keys('Shared Name')=={'sharedname'}
    assert R.contact_for_company('Shared Name')=={}
