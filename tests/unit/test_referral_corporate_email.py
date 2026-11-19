"""Corporate-email fields must use a matching employer address, never a convenient university address."""
import csv
import json
from types import SimpleNamespace

import pytest

from jobagent.tools import referrals as R


@pytest.fixture
def setup_contacts(tmp_path, monkeypatch):
    data = tmp_path / "data"
    state = tmp_path / "state"
    data.mkdir()
    (state / "companies").mkdir(parents=True)
    monkeypatch.setattr(R, "get_settings", lambda: SimpleNamespace(data_dir=data, state_dir=state))

    def setup(rows, profiles=(), aliases=""):
        with (data / "asu_contacts.csv").open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["company", "name", "email", "alternate_email"])
            writer.writeheader()
            writer.writerows(rows)
        (data / "company_aliases.yaml").write_text(aliases)
        for i, profile in enumerate(profiles):
            (state / "companies" / f"{i}.json").write_text(json.dumps(profile))

    return setup


def test_prefer_same_rows_corporate_alternate_email(setup_contacts):
    setup_contacts([{"company": "Example", "name": "Employee", "email": "employee@university.edu",
                     "alternate_email": "employee@example.com"}],
                   [{"name": "Example", "website": "https://www.example.com"}])
    assert R.contact_for_company("Example")["email"] == "employee@example.com"


def test_prefer_complete_corporate_contact_from_explicit_company_alias(setup_contacts):
    setup_contacts([{"company": "Example", "name": "University Contact", "email": "first@university.edu"},
                    {"company": "Example Systems", "name": "Corporate Employee", "email": "employee@example.com"}],
                   [{"name": "Example", "website": "https://www.example.com"}],
                   "Example:\n  aliases: [Example Systems]\n")
    assert R.contact_for_company("Example") == {"name": "Corporate Employee", "first_name": "Corporate",
                                               "last_name": "Employee", "email": "employee@example.com"}


@pytest.mark.parametrize("profiles", [[], [{"name": "Different Employer", "website": "https://example.com"}],
    [{"name": "Example", "website": "https://one.example.com"},
     {"name": "Example", "website": "https://other-company.com"}],
    [{"name": "Example", "website": "not a verified website"}],
])
def test_missing_unrelated_or_ambiguous_website_keeps_existing_choice(setup_contacts, profiles):
    setup_contacts([{"company": "Example", "name": "First Contact", "email": "first@university.edu",
                     "alternate_email": "second@example.com"}], profiles)
    assert R.contact_for_company("Example")["email"] == "first@university.edu"


def test_a_corporate_domain_never_selects_another_companys_row(setup_contacts):
    setup_contacts([{"company": "Example", "name": "Right Employer", "email": "right@university.edu"},
                    {"company": "Other", "name": "Wrong Employer", "email": "wrong@example.com"}],
                   [{"name": "Example", "website": "https://www.example.com"}])
    assert R.contact_for_company("Example")["name"] == "Right Employer"


def test_domain_suffix_lookalike_is_not_a_company_email(setup_contacts):
    setup_contacts([{"company": "Example", "name": "Lookalike", "email": "look@example.com.evil.test"},
                    {"company": "Example", "name": "Employee", "email": "employee@example.com"}],
                   [{"name": "Example", "website": "https://www.example.com"}])
    assert R.contact_for_company("Example")["email"] == "employee@example.com"
