import yaml

from jobagent.steps import apply
from jobagent.tools.forms import SUBMIT, NEXT, match_option, same_value


def test_country_synonyms_match_the_pages_option():
    opts = ["Select...", "Canada", "United States of America", "India"]
    assert match_option("USA", opts)[0] == "United States of America"
    assert match_option("United States", opts)[0] == "United States of America"


def test_yes_no_match_long_options():
    opts = ["Yes, I am authorized to work in the US", "No, I am not authorized"]
    assert match_option("Yes", opts)[0] == opts[0]
    assert match_option("No", opts)[0] == opts[1]


def test_no_close_option_is_left_to_the_tie_break():
    assert match_option("Before September 2026", ["January to March 2027", "April to July 2027"])[0] is None


def test_read_back_comparisons():
    assert same_value("6233329509", "(623) 332-9509", "tel")
    assert same_value("outputs/x/resume.pdf", "resume.pdf", "file")
    assert same_value("Backend; Machine Learning", "Machine Learning; Backend", "checkboxes")
    assert same_value("checked", "checked", "checkbox") and not same_value("checked", "", "checkbox")
    assert not same_value("Bhavya", "Bhavyaa Shah", "text")


def test_submit_buttons_are_recognised_and_next_is_not_submit():
    for b in ("Submit application", "Submit", "Apply", "Send"):
        assert SUBMIT.search(b)
    for b in ("Next", "Save and Continue", "Continue", "Review"):
        assert NEXT.search(b) and not SUBMIT.search(b)
    assert not SUBMIT.search("Apply Manually")


def test_learned_answers_replace_the_same_question_per_company(tmp_path, monkeypatch):
    monkeypatch.setattr(apply, "LEARNED", tmp_path / "learned_answers.yaml")
    apply.learn("Preferred office?", ["NYC", "SF"], "NYC", "doordash", "x.io")
    apply.learn("Preferred office?", ["NYC", "SF"], "SF", "doordash", "x.io")
    apply.learn("Preferred office?", [], "Remote", "adobe", "y.io")
    data = yaml.safe_load((tmp_path / "learned_answers.yaml").read_text())
    assert [(e["company"], e["answer"]) for e in data] == [("doordash", "SF"), ("adobe", "Remote")]
