"""F29: a school is never swapped for a similar one. F30: the planner and the checker get the authoritative posting, and numeric
ranges are matched by meaning."""
from types import SimpleNamespace

import pytest
from playwright.sync_api import sync_playwright

from jobagent import llm
from jobagent.steps import apply as A
from jobagent.tools import forms

TRAVEL = ["Travel - 10% or less", "Travel - 30% or less", "Travel - 50% or more"]


# --- F29 -------------------------------------------------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def no_aliases():
    forms.set_approved_aliases({})
    yield
    forms.set_approved_aliases({})


def test_arizona_state_is_never_adams_state():
    assert forms.identity_option("Arizona State University", ["Adams State University", "Other"]) is None
    assert forms.identity_option("Arizona State University", ["Arizona State University (ASU)"]) == "Arizona State University (ASU)"
    assert forms.identity_option("Vellore Institute of Technology", ["Vellore Institute of Technology (VIT) University"])
    assert forms.identity_option("Stripe", ["Stripe Inc.", "Strip Club LLC"]) == "Stripe Inc."
    assert forms.identity_option("Acme Robotics", ["Acme Robotics, Inc.", "Other"]) == "Acme Robotics, Inc."      # a corporate suffix
    assert forms.identity_option("Acme Robotics, Inc.", ["Acme Robotics"]) == "Acme Robotics"                       # and the reverse
    assert forms.match_option("Arizona State University", ["Adams State University", "Other"])[0] is not None       # category rules untouched


@pytest.mark.parametrize("extension", ["Acme Robotics - Competitor", "Acme Robotics, Competitor", "Acme Robotics (Competitor)",
                                       "Acme Robotics: Rivals", "Acme Robotics / Rivals", "Acme Robotics Competitor", "Acme Robotics (AX)"])
def test_nothing_after_a_name_makes_a_different_organisation_the_same_one(extension):
    assert forms.identity_option("Acme Robotics", [extension, "Other"]) is None
    assert forms._choose("Employer", "Acme Robotics", [extension, "Other"], None) == "Other"      # Other, never the lookalike


def test_a_campus_form_is_the_same_school_only_when_the_candidate_approved_it():
    options = ["Adams State University", "Arizona State University - Tempe", "Other"]
    assert forms.identity_option("Arizona State University", options) is None                          # nothing approved: not assumed
    forms.set_approved_aliases({"Arizona State University": ["Arizona State University - Tempe"]})
    assert forms.identity_option("Arizona State University", options) == "Arizona State University - Tempe"
    assert forms.identity_option("Arizona State University", ["Arizona State University - Polytechnic"]) is None   # a campus NOT approved
    assert forms.identity_option("Some Other College", options) is None                                # aliases belong to one name only


def test_people_match_exactly_and_aliases_never_apply_to_them():
    assert forms.identity_option("Jane Doe", ["Jane Doe Jr", "Other"]) is None
    assert forms.identity_option("Jordan Lee", ["Jordan Leigh", "Jordan Lee Smith"]) is None
    forms.set_approved_aliases({"Jane Doe": ["Jane Doe - Recruiter"]})
    assert forms.identity_option("Jane Doe", ["Jane Doe - Recruiter"], person=True) is None
    assert forms.identity_option("Jane Doe", ["jane doe"], person=True) == "jane doe"


def test_the_applicant_files_aliases_are_the_only_source():
    data = {"Education": [{"question": "Education", "answer": [{"school": "Arizona State University",
                                                                 "aliases": ["Arizona State University - Tempe"], "degree": "MS"},
                                                                {"school": "Vellore Institute of Technology", "degree": "BTech"}]}]}
    from jobagent.tools import applicant
    assert applicant.identity_aliases(data) == {"Arizona State University": ["Arizona State University - Tempe"]}


def test_identity_questions_choose_the_real_option_then_other_then_stop():
    q = "Institutes"
    assert forms._choose(q, "Arizona State University", ["Adams State University", "Arizona State University (ASU)"], None) \
        == "Arizona State University (ASU)"
    assert forms._choose(q, "Arizona State University", ["Adams State University", "Other"], lambda *a: "Adams State University") == "Other"
    with pytest.raises(forms.FillError, match="needs the candidate"):
        forms._choose(q, "Arizona State University", ["Adams State University", "Boise State University"], lambda *a: "Adams State University")
    # category wording keeps its fuzzy matching and its model tie-break
    assert forms._choose("Degree", "Master of Science", ["Masters Degree", "Bachelors"], None) == "Masters Degree"


def fill_in_chrome(html, label, answer, kind="select"):
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(channel="chrome", headless=True)
        except Exception:
            pytest.skip("no Chrome here")
        try:
            page = browser.new_page()
            page.route("**/*", lambda r: r.fulfill(status=200, content_type="text/html", body=html) if r.request.resource_type == "document" else r.abort())
            page.goto("https://app.test/")
            view = forms.read_page(page)
            f = next(x for x in view.fields if label.lower() in x.label.lower())
            try:
                chosen = forms.fill_field(page, view, f, answer, {})
            except forms.FillError as e:
                return None, str(e), forms.read_page(page)
            return chosen, "", forms.read_page(page)
        finally:
            browser.close()


def school_select(*opts):
    return "<label>Institutes <select>" + "".join(f"<option>{o}</option>" for o in ("Select...", *opts)) + "</select></label>"


def test_the_filler_selects_the_real_school_and_never_the_neighbour():
    chosen, err, view = fill_in_chrome(school_select("Adams State University", "Arizona State University (ASU)", "Other"),
                                       "Institutes", "Arizona State University")
    assert chosen == "Arizona State University (ASU)" and view.fields[0].value == "Arizona State University (ASU)"


def test_a_missing_school_selects_other_or_stops_but_never_substitutes():
    chosen, err, view = fill_in_chrome(school_select("Adams State University", "Boise State University", "Other"), "Institutes",
                                       "Arizona State University")
    assert chosen == "Other"
    chosen, err, view = fill_in_chrome(school_select("Adams State University", "Boise State University"), "Institutes",
                                       "Arizona State University")
    assert chosen is None and "needs the candidate" in err and view.fields[0].value in ("", "Select...")


# --- F30 -------------------------------------------------------------------------------------------------------------------

def test_a_percentage_question_is_answered_by_meaning():
    assert forms.parse_interval("Travel - 30% or less") == (0, 30)
    assert forms.parse_interval("up to 25%") == (0, 25) and forms.parse_interval("50% or more") == (50, 100)
    assert forms.parse_interval("10-25%") == (10, 25) and forms.parse_interval("yes") is None
    assert forms.match_option("30% or less", TRAVEL)[0] == "Travel - 30% or less"
    assert forms.match_option("Up to 30%", TRAVEL)[0] == "Travel - 30% or less"
    assert forms.match_option("20%", TRAVEL)[0] == "Travel - 30% or less"             # the tightest bracket that contains it
    assert forms.match_option("100%", TRAVEL)[0] == "Travel - 50% or more"
    assert forms.match_option("60% or more", ["Travel - 10% or less", "Travel - 30% or less"])[0] is None   # no incompatible fuzzy fallback



def job(tmp_path, jd_txt, jd_md="# Cloud Solutions Engineer\n\nSummary without travel."):
    d = tmp_path / "outputs" / "pantex" / "1"
    d.mkdir(parents=True)
    (d / "jd.json").write_text('{"company": "Pantex", "title": "Engineer", "locations_resolved": []}')
    (d / "jd.txt").write_text(jd_txt)
    (d / "jd.md").write_text(jd_md)
    return d


def test_the_planner_gets_the_raw_posting_even_when_the_summary_lacks_the_fact(tmp_path, monkeypatch):
    monkeypatch.setattr(A.applicant, "load", lambda *a, **k: {})
    d = job(tmp_path, "Pantex\nCloud Solutions Engineer\nTravel - 30% or less\nDuties: build things")
    jd = A.context(d, "pantex")["JD"]
    assert "Travel - 30% or less" in jd and "jd.txt" in jd and "complete" in jd


def test_a_requirement_beyond_the_old_five_thousand_characters_is_still_seen(tmp_path, monkeypatch):
    monkeypatch.setattr(A.applicant, "load", lambda *a, **k: {})
    d = job(tmp_path, "Intro. " * 2000 + "\nThis role requires Travel - 30% or less.\n")
    ctx = A.context(d, "pantex")
    assert "Travel - 30% or less" in ctx["JD"] and len(ctx["_jd_text"]) > 5000


def test_a_very_long_posting_is_marked_and_searched_for_the_question(tmp_path, monkeypatch):
    monkeypatch.setattr(A.applicant, "load", lambda *a, **k: {})
    text = "Filler line about nothing.\n" * 4000 + "Travel requirement: 30% or less\n"
    d = job(tmp_path, text)
    ctx = A.context(d, "pantex")
    assert "first 15000 are shown" in ctx["JD"]                                          # the cut is announced, not silent
    f = forms.Field(id=1, kind="select", label="Travel", options=TRAVEL)
    assert "Travel requirement: 30% or less" in A.jd_passages(ctx["_jd_text"], [f])


def test_the_checker_validates_a_jd_dependent_answer_against_the_posting_it_is_given(tmp_path, monkeypatch):
    f = forms.Field(id=1, kind="select", label="Travel", options=TRAVEL)
    data = {"Screening": [{"question": "Travel", "instructions": "Use the percentage in the JD; if none is given choose 100%."}]}
    ans = {1: A.Answer(id=1, value="Travel - 50% or more", source="applicant", why="no travel in the summary")}
    seen = {}

    def fake(model, schema, msgs, first=None, log=None):
        seen["packet"] = msgs[-1].content
        return SimpleNamespace(field_1=A.Check(id=1, supported=False, value="Travel - 30% or less"))
    monkeypatch.setattr(llm, "structured_invoke", fake)
    ctx = {"_applicant": data, "_jd": {"company": "Pantex"}, "JD": "[source: jd.txt]\nTravel - 30% or less", "_jd_text": "Travel - 30% or less",
           "APPLICANT": "x", "ANSWERS_FOR_THIS_APPLICATION": "(none)"}
    A.check([f], ans, ctx, object(), lambda m: None)
    assert "Travel - 30% or less" in seen["packet"]                                      # the posting reached the checker
    assert ans[1].value == "Travel - 30% or less" and "corrected by the check" in ans[1].flag   # the instruction did not wave it through


def test_vague_nickname_instruction_without_the_nickname_is_not_support(monkeypatch):
    f = forms.Field(id=1, kind="select", label="Preferred name", options=["Bo", "Bob"])
    data = {"Names": [{"question": "Preferred name", "instructions": "Use my nickname."}]}
    ans = {1: A.Answer(id=1, value="Bo", source="applicant")}
    monkeypatch.setattr(llm, "structured_invoke", lambda *a, **k: SimpleNamespace(field_1=A.Check(id=1, supported=False, ask_human=True)))
    A.check([f], ans, {"_applicant": data, "_jd": {}, "JD": "", "_jd_text": ""}, object(), lambda m: None)
    assert ans[1].value == "" and ans[1].ask_human


def test_an_explicit_literal_owner_rule_still_counts_as_support(monkeypatch):
    f = forms.Field(id=1, kind="select", label="Preferred name", options=["Bo", "Bob"])
    data = {"Names": [{"question": "Preferred name", "instructions": 'Use "Bo".'}]}
    ans = {1: A.Answer(id=1, value="Bo", source="applicant")}
    monkeypatch.setattr(llm, "structured_invoke", lambda *a, **k: SimpleNamespace(field_1=A.Check(id=1, supported=False, ask_human=True)))
    A.check([f], ans, {"_applicant": data}, object(), lambda m: None)
    assert ans[1].value == "Bo" and not ans[1].ask_human


BRACKETS = ["Travel - 10% or less", "30%-50%", "50% or more"]


@pytest.mark.parametrize("answer,expected", [
    ("<30%", None), ("< 30%", None), ("<=30%", None), ("≤30%", None),            # no offered bracket lies wholly below/at 30 here
    (">50%", "50% or more"), ("> 50 %", "50% or more"), (">=50%", "50% or more"), ("≥50%", "50% or more"),
    (">=30%", None), ("<=10%", "Travel - 10% or less"), ("<10%", "Travel - 10% or less"),
])
def test_comparison_symbols_keep_their_meaning(answer, expected):
    assert forms.interval_option(answer, BRACKETS)[0] == expected
    assert forms.match_option(answer, BRACKETS)[0] == expected                        # and through the fill-path matcher


def test_an_approximation_or_an_uninterpretable_percentage_is_left_to_the_candidate_not_guessed():
    for a in ("~30%", "about 30%", "approximately 40%", "roughly half, 40%"):
        assert forms.interval_option(a, BRACKETS) == (None, True)
        assert forms.match_option(a, BRACKETS)[0] is None
    assert forms.interval_option("about 30%", ["Yes", "No"]) == (None, False)         # not a range question: ordinary matching applies


def test_the_bracket_ends_are_boundaries_not_fuzz():
    assert forms.parse_interval("<30%")[1] < 30 and forms.parse_interval("<=30%")[1] == 30
    assert forms.parse_interval(">50%")[0] > 50 and forms.parse_interval(">=50%")[0] == 50
    assert forms.parse_interval("between 10 and 25%") == (10, 25)


def test_a_compatible_bracket_is_chosen_when_it_is_offered():
    offered = BRACKETS + ["30% or more", "Under 30%"]
    assert forms.interval_option(">=30%", offered)[0] == "30% or more"
    assert forms.interval_option("<30%", offered)[0] == "Under 30%"
