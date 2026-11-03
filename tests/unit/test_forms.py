import yaml

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
    assert same_value("08/2024", "08/2024", "date_parts") and not same_value("Present", "", "date_parts")
    assert same_value("Job Board > LinkedIn", "LinkedIn", "combobox")


def test_submit_buttons_are_recognised_and_next_is_not_submit():
    for b in ("Submit application", "Submit", "Apply", "Send"):
        assert SUBMIT.search(b)
    for b in ("Next", "Save and Continue", "Continue", "Review"):
        assert NEXT.search(b) and not SUBMIT.search(b)
    assert not SUBMIT.search("Apply Manually")


def test_learned_answers_are_appended_to_the_applicant_file(tmp_path, monkeypatch):
    from jobagent.tools import applicant
    f = tmp_path / "applicant.yaml"
    f.write_text("# my notes\nidentity:\n  - question: Pronouns\n    answer: He/Him\n\nlearned:\n")
    monkeypatch.setattr(applicant, "path", lambda: f)
    applicant.learn("Preferred office?", ["NYC", "SF"], "SF", "doordash", "x.io")
    applicant.learn("Preferred office?", [], "Remote", "adobe", "y.io")
    data = yaml.safe_load(f.read_text())
    assert "# my notes" in f.read_text()  # the user's comments stay
    assert [(e["company"], e["answer"]) for e in data["learned"]] == [("doordash", "SF"), ("adobe", "Remote")]
    assert applicant.answer("Pronouns", data) == "He/Him"


def test_form_labels_find_their_applicant_entry(tmp_path, monkeypatch):
    from jobagent.steps.apply import hints
    from jobagent.tools import applicant
    from jobagent.tools.forms import Field
    f = tmp_path / "applicant.yaml"
    f.write_text(open("data/applicant.example.yaml").read())
    monkeypatch.setattr(applicant, "path", lambda: f)
    cases = {  # label as the portal shows it -> the entry's question
        "First Name*": "Legal name", "City": "Current address", "Country*": "Current address",
        "Phone Device Type*": "Phone", "Location (City)*": "Current location (city)",
        "Have you worked at Adobe?*": "Have you worked for this company before?",
        "Are you able to work on a daily basis in the work location listed for this position? If not, are you "
        "willing to relocate?*": "Able to work on site in the job's location?",
        "If you are offered employment by Adobe, can you provide documentation establishing your identity and right "
        "to work in the United States?*": "Can you provide documents proving your identity and right to work?",
        "Gender*": "Gender", "Do you identify as transgender?*": "Transgender?",
        "Will you now require immigration sponsorship by our company to attain or maintain your employment "
        "eligibility?*": "Will you now require sponsorship?",
    }
    fields = [Field(id=i, kind="text", label=label) for i, label in enumerate(cases)]
    fields.append(Field(id=99, kind="text", label="Job Title*", section="Work Experience 2"))
    got = hints(fields, "Adobe")
    for i, (label, question) in enumerate(cases.items()):
        assert repr(question) in got.get(i, ""), (label, got.get(i))
    assert "'Work history'" in got[99]
