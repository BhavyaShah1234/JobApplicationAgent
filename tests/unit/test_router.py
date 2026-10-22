import pytest

from jobagent.config import get_settings
from jobagent.graph.router import route, split_companies


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


# --- URLs -------------------------------------------------------------------------------------------

def test_single_url():
    r = route("https://boards.greenhouse.io/acme/jobs/123")
    assert r.kind == "url" and [i.value for i in r.items] == ["https://boards.greenhouse.io/acme/jobs/123"]


def test_multiple_urls_any_separator_deduped():
    r = route("https://a.com/j/1, https://b.com/j/2\nhttps://a.com/j/1 ; https://c.com/x?id=3.")
    assert [i.value for i in r.items] == ["https://a.com/j/1", "https://b.com/j/2", "https://c.com/x?id=3"]


def test_link_without_scheme_counts_as_url():
    r = route("jobs.lever.co/acme/abc-123")
    assert r.kind == "url" and r.items[0].value == "https://jobs.lever.co/acme/abc-123"


def test_text_next_to_urls_is_reported():
    r = route("apply here https://a.com/j/1 please")
    assert r.kind == "url" and len(r.items) == 1 and r.warnings


# --- companies ----------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text,expected",
    [
        ("Google", ["Google"]),
        ("Google, Microsoft , NVIDIA", ["Google", "Microsoft", "NVIDIA"]),
        ("Acme, Inc., Globex Corporation; Initech, LLC", ["Acme, Inc.", "Globex Corporation", "Initech, LLC"]),
        ("Google\nMicrosoft\ngoogle", ["Google", "Microsoft"]),
        ("Tata Consultancy Services, Pvt Ltd", ["Tata Consultancy Services, Pvt Ltd"]),
        ("example.com", ["example.com"]),  # a bare domain without a path is a company, not a URL
    ],
)
def test_company_splitting(text, expected):
    assert split_companies(text) == expected
    r = route(text)
    assert r.kind == "companies" and [i.value for i in r.items] == expected


def test_empty_input():
    assert route("   ").warnings == ["empty input"]


# --- files --------------------------------------------------------------------------------------------

CSV = """Company,Contact Name,Contact Email,Referral Consent,Job URL
Example Corp,Jane Doe,jane.doe@example.com,yes,
Another Inc,,,no,
Third Co,John Roe,john@third.co,no,
Fourth LLC,,maria@fourth.io,yes,
Fifth Ltd,Bad Mail,not-an-email,yes,
,,,,https://boards.greenhouse.io/sixth/jobs/9
Example Corp,,,,
,,,,
"""


def test_csv_rows_referrals_and_warnings(data_dir):
    (data_dir / "targets.csv").write_text(CSV)
    r = route("targets.csv")
    assert r.kind == "file"
    got = [(i.kind, i.value, i.referral.name if i.referral else None) for i in r.items]
    assert got == [
        ("company", "Example Corp", "Jane Doe"),        # consent yes → referral
        ("company", "Another Inc", None),
        ("company", "Third Co", None),                   # contact without consent → no referral
        ("company", "Fourth LLC", None),                 # consent but no name → no referral
        ("company", "Fifth Ltd", None),                  # invalid email → no referral
        ("url", "https://boards.greenhouse.io/sixth/jobs/9", None),
    ]
    text = " | ".join(r.warnings)
    assert "row 4" in text and "without referral consent" in text
    assert "row 5" in text and "no contact name" in text
    assert "row 6" in text and "invalid email" in text
    assert "row 8" in text and "duplicate" in text
    assert "row 9" not in text  # fully empty rows are ignored silently


def test_semicolon_csv_and_header_aliases(data_dir):
    (data_dir / "t.csv").write_text("Organisation;Referrer;Email;Consent\nAcme;Ann Lee;ann@acme.com;TRUE\n")
    r = route(str(data_dir / "t.csv"))
    assert [(i.value, i.referral.email) for i in r.items] == [("Acme", "ann@acme.com")]


def test_xlsx(data_dir):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["company", "contact_name", "contact_email", "referral_consent"])
    ws.append(["Globex", "Hank Scorpio", "hank@globex.com", "Yes"])
    ws.append(["Initech", None, None, None])
    wb.save(data_dir / "targets.xlsx")
    r = route("targets.xlsx")
    assert [(i.value, bool(i.referral)) for i in r.items] == [("Globex", True), ("Initech", False)]


def test_missing_file_and_unknown_columns(data_dir):
    assert "file not found" in route("nope.csv").warnings[0]
    (data_dir / "x.csv").write_text("foo,bar\n1,2\n")
    assert "no company or job URL column" in route("x.csv").warnings[0]


def test_route_node_reads_latest_human_message():
    from langchain_core.messages import HumanMessage, SystemMessage

    from jobagent.graph.router import route_node

    out = route_node({"messages": [SystemMessage("bootstrap ok"), HumanMessage("Google, NVIDIA")]})
    assert [i["value"] for i in out["routed"]["items"]] == ["Google", "NVIDIA"]
