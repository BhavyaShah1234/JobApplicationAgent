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
    assert same_value("outputs/x/resume.pdf", "resume.pdf", "file") and not same_value("outputs/x/resume.pdf", "ATTACH RESUME/CV", "file")
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


RESUME_TEX = r"""\section{Experience}
  \resumeSubHeadingListStart
    \resumeSubheading
      {\underline{Robotics Researcher}}{January 2026 -- Present}
      {Logos Robotics Lab}{Tempe, Arizona}
      \resumeItemListStart
        \resumeItem{Built CNN models in PyTorch, improving precision by 25\%.}
      \resumeItemListEnd
    \resumeSubheading
      {\underline{Software Engineering Intern}}{May 2025 -- December 2025}
      {Acme \& Co}{Phoenix, Arizona}
      \resumeItemListStart
        \resumeItem{Shipped REST APIs.}
      \resumeItemListEnd
\section{Technical Skills}
     \textbf{Languages}{: Python, C++} \\
     \textbf{Tools}{: Docker, Python}
\end{document}"""


def test_experience_answers_come_from_the_tailored_resume(monkeypatch):
    from datetime import date
    from jobagent.tools import applicant
    monkeypatch.setattr(applicant, "date", type("D", (), {"today": staticmethod(lambda: date(2026, 4, 15))}))
    exp = applicant.resume_experience(RESUME_TEX)
    assert [r["company"] for r in exp["roles"]] == ["Logos Robotics Lab", "Acme & Co"]
    assert exp["roles"][0]["current"] and exp["roles"][0]["description"].endswith("by 25%.")
    assert exp["current_company"] == "Logos Robotics Lab" and exp["internships"] == 1
    assert exp["skills"] == ["Python", "C++", "Docker"]
    assert exp["years"] == 1.0  # May 2025 - Apr 2026, overlap counted once
    data = {"experience": [{"question": "Current company", "answer": "old"}]}
    assert applicant.answer("Current company", applicant.with_resume(data, exp)) == "Logos Robotics Lab"


def test_degree_level_wordings_match():
    assert match_option("Master of Science", ["Bachelors", "Masters", "Doctorate"])[0] == "Masters"
    assert match_option("B.Tech", ["Bachelor's Degree", "Master's Degree"])[0] == "Bachelor's Degree"
    assert match_option("Master's degree", ["Associates", "Masters"])[0] == "Masters"


def test_own_site_option_and_applicant_variants():
    from jobagent.steps.apply import in_applicant, own_site_option
    assert own_site_option(["Job Board > LinkedIn", "Adobe Source > Adobe MAX", "Adobe Source > Adobe.com"], "Adobe") \
        == "Adobe Source > Adobe.com"
    assert own_site_option(["Palantir Event", "Palantir Website", "Referral"], "Palantir Technologies") == "Palantir Website"
    data = {"education": [{"question": "Degrees", "answer": [{"degree": ["Master of Science", "MS"], "gpa": "4.0"}]}]}
    assert in_applicant("Masters", data) and in_applicant("4.0", data) and not in_applicant("3.57", data)


def test_range_options_and_variant_read_back():
    from jobagent.tools.forms import in_range
    assert in_range("3.75+", 4.0) and in_range("3.4 - 3.70", 3.5) and in_range("Below 3.4", 3.0)
    assert not in_range("3.4 - 3.70", 4.0) and not in_range("Below 3.4", 3.57)
    assert same_value("AI and Robotics | Robotics", "Robotics", "combobox")


def test_address_lookups_are_never_used(monkeypatch):
    from jobagent.steps.apply import Answer, enforce
    from jobagent.tools.forms import Field
    ctx = {"_applicant": {"address": [{"question": "Current address", "answer": {"line1": "", "city": ""}}]},
           "REFERRAL": "none", "_jd": {"company": "Adobe"}}
    fields = [Field(id=1, kind="text", label="Address Line 1"), Field(id=2, kind="text", label="City"),
              Field(id=3, kind="combobox", label="How Did You Hear About Us?*",
                    options=["Job Board > LinkedIn", "Adobe Source > Adobe.com", "Referral"])]
    answers = {1: Answer(id=1, value="", source="lookup", lookup="web: a street in San Jose"),
               2: Answer(id=2, value="San Jose", source="applicant"),
               3: Answer(id=3, value="Job Board > LinkedIn", source="applicant")}
    enforce(fields, answers, ctx, lambda m: None)
    assert answers[1].value == "" and answers[1].lookup == "" and answers[1].ask_human
    assert answers[2].value == "" and answers[2].ask_human
    assert answers[3].value == "Adobe Source > Adobe.com"


def test_skill_names_match_strictly():
    from jobagent.tools.forms import strict_option
    opts = ["Python IDLE", "Python (Programming Language)", "SAP R", "R", "C NMR", "Machine Learning (ML)"]
    assert strict_option("Python", opts) == "Python (Programming Language)"
    assert strict_option("R", opts) == "R" and strict_option("C", opts) is None
    assert strict_option("machine learning", opts) == "Machine Learning (ML)"
