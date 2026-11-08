import re

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
    assert not same_value("Arizona State University - Tempe | Arizona State University", "Arizona State University - West", "combobox")


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
    assert answers[3].value == "" and answers[3].ask_human  # no referrer on file: the candidate says how
    ctx["REFERRAL"] = "Jane Doe (jane@adobe.com), who agreed to refer the candidate"
    answers[3] = Answer(id=3, value="Referral", source="applicant")
    enforce(fields, answers, ctx, lambda m: None)
    assert answers[3].value == "Referral"  # a referrer on file: the referral option stands


def test_skill_names_match_strictly():
    from jobagent.tools.forms import strict_option
    opts = ["Python IDLE", "Python (Programming Language)", "SAP R", "R", "C NMR", "Machine Learning (ML)"]
    assert strict_option("Python", opts) == "Python (Programming Language)"
    assert strict_option("R", opts) == "R" and strict_option("C", opts) is None
    assert strict_option("machine learning", opts) == "Machine Learning (ML)"


def test_list_answers_are_tried_in_order():
    from jobagent.steps.apply import Answer, apply_variants
    from jobagent.tools.forms import Field, close_option
    degrees = [{"school": "Arizona State University", "degree": ["Master of Science", "MS"],
                "field": ["Artificial Intelligence and Robotics", "Robotics"], "instructions": "pick by the JD"},
               {"school": ["Vellore Institute of Technology", "Other"], "degree": ["Bachelor of Technology", "B.Tech"],
                "field": ["Computer Science and Engineering", "Computer Science", "Computer and Informational Sciences"]}]
    ctx = {"_applicant": {"education": [{"question": "Degrees", "answer": degrees}]}, "_jd": {"company": "Adobe"}}
    fields = [Field(id=1, kind="text", label="School or University*", section="Education 2"),
              Field(id=2, kind="combobox", label="Field of Study", section="Education 2"),
              Field(id=3, kind="combobox", label="Field of Study", section="Education 1"),
              Field(id=4, kind="text", label="School or University*", section="Education 1")]
    answers = {1: Answer(id=1, value="Vellore Institute of Technology", source="applicant"),
               2: Answer(id=2, value="Computer Engineering", source="applicant"),
               3: Answer(id=3, value="Artificial Intelligence and Robotics", source="applicant"),
               4: Answer(id=4, value="Arizona State University", source="applicant")}
    apply_variants(fields, answers, ctx, lambda m: None)
    assert answers[2].value == "Computer Science and Engineering | Computer Science | Computer and Informational Sciences"
    assert answers[3].value == "Artificial Intelligence and Robotics"  # its degree has instructions: the plan stands
    assert answers[1].value == "Vellore Institute of Technology"
    opts = ["Computer Engineering", "Computer and Information Sciences", "Computer Science"]
    assert close_option("Computer and Informational Sciences", opts) == "Computer and Information Sciences"
    assert close_option("Computer Science and Engineering", opts) is None


def test_close_option_takes_an_abbreviation_not_a_campus():
    from jobagent.tools.forms import close_option
    opts = ["Other", "Vellore Institute of Technology (VIT) University", "Arizona State University - West"]
    assert close_option("Vellore Institute of Technology", opts) == "Vellore Institute of Technology (VIT) University"
    assert close_option("Arizona State University", opts) is None


def test_a_later_variant_in_the_field_is_tried_again():
    from jobagent.tools.forms import lower_variant
    want = "Vellore Institute of Technology | Other"
    assert lower_variant(want, "Other")
    assert not lower_variant(want, "Vellore Institute of Technology (VIT) University")
    assert not lower_variant("Masters", "Masters")


def test_a_rule_reply_is_kept_for_every_company(tmp_path, monkeypatch):
    from jobagent.steps import apply as A
    from jobagent.tools import applicant
    from jobagent.tools.forms import Field
    from jobagent.tools import learned
    settings = type("S", (), {"data_dir": tmp_path, "state_dir": tmp_path / "state", "model_embed": "m"})()
    monkeypatch.setattr(learned, "get_settings", lambda: settings)
    hitl = type("H", (), {"ask": lambda self, prompt, timeout_s=None: "rule: select every office; else a random one"})()
    field = Field(id=1, kind="checkboxes", label="Which office are you applying to?", options=["SF", "NYC"])
    assert A.ask_human(hitl, field, "mercor", "www.mercor.com", lambda m: None) == \
        ("rule", "select every office; else a random one")
    entry = learned.load()[0]
    assert entry["company"] == "any" and entry["answer"] == "" and entry["instructions"].startswith("select every")
    plain = type("H", (), {"ask": lambda self, prompt, timeout_s=None: "NYC"})()
    assert A.ask_human(plain, field, "mercor", "www.mercor.com", lambda m: None) == ("answer", "NYC")
    assert learned.load()[1]["company"] == "mercor"


def test_learned_store_is_separate_locked_and_retrieved_by_closeness(tmp_path, monkeypatch):
    from jobagent.tools import applicant, learned
    settings = type("S", (), {"data_dir": tmp_path, "state_dir": tmp_path / "state", "model_embed": "m"})()
    (tmp_path / "state").mkdir()
    monkeypatch.setattr(learned, "get_settings", lambda: settings)
    monkeypatch.setattr(applicant, "get_settings", lambda: settings)
    monkeypatch.setattr(learned, "_vectors", lambda texts: None)  # wording match only in this test
    (tmp_path / "applicant.yaml").write_text("identity:\n  - question: Pronouns\n    answer: He/Him\n")
    learned.add("Which office are you applying to?", ["SF", "NYC"], "", "mercor", "m.com", rule="select all")
    learned.add("Do you have a security clearance?", [], "No", "intel", "i.com")
    learned.add("Which office are you applying to?", [], "", "intel", "i.com", rule="select all offices")
    entries = learned.load()
    assert len(entries) == 2 and entries[-1]["instructions"] == "select all offices"  # the newer rule replaced it
    assert "learned" not in applicant.load() and "learned" not in (tmp_path / "applicant.yaml").read_text()
    near = learned.relevant("Which office are you applying to? (Select both if appropriate)", "adobe")
    assert [e["question"] for e in near] == ["Which office are you applying to?"]
    # another company's literal answer to a differently worded question isn't offered
    assert learned.relevant("Clearance level held", "adobe") == []
    assert list((tmp_path / "state" / "backups").glob("learned-*.yaml"))


def test_united_states_never_picks_a_territory():
    opts = ["United States Minor Outlying Islands", "United States of America", "Uruguay"]
    assert match_option("United States", opts)[0] == "United States of America"
    codes = ["United States Minor Outlying Islands (+1)", "United States of America (+1)"]
    assert match_option("United States (+1)", codes)[0] == "United States of America (+1)"


def test_best_place_picks_the_same_city_in_the_same_state():
    from jobagent.tools.forms import best_place
    sugg = ["Tempe, Sala Consilina, Salerno, ITA", "Tempe, AZ, USA", "Tempe, Sulawesi Selatan, IDN"]
    assert best_place("Tempe, Arizona", sugg) == "Tempe, AZ, USA"
    assert best_place("Tempe, Arizona, United States", sugg) == "Tempe, AZ, USA"
    assert best_place("Phoenix, Arizona", sugg) is None


def test_a_field_is_left_alone_only_when_it_really_shows_the_answer():
    from jobagent.tools.forms import already_shows
    assert not already_shows("United States", "United States Minor Outlying Islands", "combobox")
    assert already_shows("United States", "United States of America", "combobox")
    assert already_shows("6233329509", "(623) 332-9509", "tel")
    assert not already_shows("Vellore Institute of Technology | Other", "Other", "combobox")


CARVANA_HTML = """<div id="apply-form">
<div class="captionContainer"><label for="Are you able to work 5 days on-site in Tempe, Arizona?"><div>Are you able to
 work 5 days on-site in Tempe, Arizona?</div><div>*</div></label><div class="select"><button aria-haspopup="listbox"
 aria-labelledby="Are you able to work 5 days on-site in Tempe, Arizona?_select_label Are you able?_select_selected"
 id="Are you able to work 5 days on-site in Tempe, Arizona?_select" type="button"><div><div
 id="Are you able to work 5 days on-site in Tempe, Arizona?_select_label" style="text-transform:uppercase">Select answer</div>
 <span class="select__inputValue">No</span></div></button></div></div>
<div class="question"><div class="vstack"><div class="captionContainer"><label for="What's a problem you've solved?">
 <div>What's a problem you've solved?</div><div>*</div></label><textarea id="input_What's a problem you've solved?"></textarea>
 <div>Remaining: 500</div></div></div></div>
<div><label for="first">First Name</label><input id="first"></div>
</div>"""


def test_custom_widgets_take_the_question_above_them_not_their_caption():
    import pytest
    from playwright.sync_api import sync_playwright

    from jobagent.tools.forms import read_page
    with sync_playwright() as pw:
        try:
            b = pw.chromium.launch(channel="chrome", headless=True)
        except Exception:
            pytest.skip("no Chrome here")
        p = b.new_page()
        p.set_content(CARVANA_HTML)
        got = {f.id: (f.kind, re.sub(r"\s+\*", "*", f.label), f.value) for f in read_page(p).fields}
        b.close()
    labels = [x[1] for x in got.values()]
    assert ("combobox", "Are you able to work 5 days on-site in Tempe, Arizona?*", "No") in got.values()
    assert "What's a problem you've solved?*" in labels  # the label, without "Remaining: 500"
    assert "First Name" in labels
    assert not any(re.search(r"(?i)^select", lab) for lab in labels)


def test_character_counters_are_not_part_of_the_question():
    from jobagent.tools.forms import clean_label
    assert clean_label("What's a problem you've solved using AI?* Remaining: 500") == "What's a problem you've solved using AI?*"
    assert clean_label("Tell us about yourself 0/1000") == "Tell us about yourself"
    assert clean_label("Why us? 250 characters left") == "Why us?"
    assert clean_label("Question 1/5") == "Question 1/5"


def test_entries_close_in_meaning_are_shown_as_related_hints_only(tmp_path, monkeypatch):
    from jobagent.steps import apply as A
    from jobagent.tools import applicant, learned
    from jobagent.tools.forms import Field
    f = tmp_path / "applicant.yaml"
    f.write_text(open("data/applicant.example.yaml").read())
    monkeypatch.setattr(applicant, "path", lambda: f)
    salary = {"desired salary", "salary expectations", "expected compensation"}
    # fake embeddings: the compensation question is close to the salary entry's wordings only
    monkeypatch.setattr(learned, "_vectors", lambda texts: [
        [1.0, 0.0] if ("compensation range" in t or t in salary) else [0.0, 1.0] if "favourite colour" in t
        else [0.6, 0.8] for t in texts])
    fields = [Field(id=1, kind="text", label="What is your desired compensation range for this position?*")]
    h = A.hints(fields)
    assert h[1].startswith("RELATED (1.00") and "'Desired salary'" in h[1]
    assert 1 not in A.match_entries(fields)  # never a code-applied match
    assert A.hints(fields, related=False) == {}  # the plan checker doesn't count it as the file saying so


def test_a_reply_that_is_one_of_the_options_is_never_questioned(monkeypatch):
    from jobagent import llm
    from jobagent.steps import apply as A
    from jobagent.tools.forms import Field
    monkeypatch.setattr(llm, "structured", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no model call")))
    assert A.misfit(Field(id=1, kind="select", label="Sponsorship?", options=["Yes", "No"]), "no") == ""


def test_the_candidates_answer_sheet_for_a_job_wins_and_is_pinned_to_its_field():
    from jobagent.steps.apply import sheet_hints, sheet_rows
    from jobagent.tools.forms import Field
    text = ("| # | Question | Answer |\n|---|---|---|\n| 8 | Portfolio or professional website link | https://github.com/x |\n"
            "| 14 | ⚠ Desired compensation range | **$100,000 - $125,000** (no range posted) |\n")
    assert sheet_rows(text)[0] == ("Portfolio or professional website link", "https://github.com/x")
    fields = [Field(id=1, kind="text", label='Portfolio or professional website link ("N/A" if not applicable)*'),
              Field(id=2, kind="text", label="What is your desired compensation range for this position?*"),
              Field(id=3, kind="text", label="First Name*")]
    got = sheet_hints(fields, text)
    assert "https://github.com/x" in got[1] and "$100,000 - $125,000" in got[2] and 3 not in got
