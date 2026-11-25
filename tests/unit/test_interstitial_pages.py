"""Field-less intermediate screens (progress trackers, optional opt-ins, welcome pages): move on with a safe button, never opt in, never loop."""
import pytest

from jobagent.filling import adapter
from jobagent.tools import forms

AMAZON = ["menu", "logo link to home", "Applications", "Profile", "Preferences", "Software Development Engineer (ID: 10575303)", "Contact information",
          "SMS Notifications Optional", "General questions", "Education", "Resume", "Acknowledgement", "Edit", "Enable SMS updates", "Skip & continue",
          "Privacy Notice", "Impressum", "Disability accommodations", "Diversity and Inclusion"]      # the real button list of the page that blocked runs 12 and 14


def view(buttons, fields=(), headings=("Apply", "My progress")):
    return forms.PageView(fields=list(fields), buttons=list(buttons), errors=[], headings=list(headings), frames={})


@pytest.mark.parametrize("buttons,expected", [
    (AMAZON, "Skip & continue"),
    (["Next"], "Next"), (["Continue"], "Continue"), (["Save and continue"], "Save and continue"),
    (["Allow notifications", "Not now"], "Not now"), (["Maybe later", "Turn on alerts"], "Maybe later"), (["No thanks"], "No thanks"),
    (["Get started"], "Get started"), (["Start application"], "Start application"), (["Begin application"], "Begin application"), (["Skip"], "Skip"),
    (["Skip this step"], "Skip this step"), (["Skip for now", "Enable notifications"], "Skip for now"),
])
def test_a_fieldless_page_offers_its_safe_button(buttons, expected):
    assert forms.interstitial_button(view(buttons)) == expected


@pytest.mark.parametrize("buttons", [
    ["Enable SMS updates"], ["Allow notifications"], ["Subscribe"], ["Opt in to texts"], ["Accept and continue"], ["I agree"], ["Accept all"],      # consents
    ["Submit application"], ["Submit"], ["Apply"], ["Finish"], ["Send application"],                                                               # sends something
    [], ["menu", "Privacy Notice"], ["Skip the questions"],                                                                                         # nothing safe, or not a known pattern
])
def test_opt_ins_consents_and_submits_are_never_a_way_forward(buttons):
    assert forms.interstitial_button(view(buttons)) is None


def test_a_page_with_fields_is_never_treated_as_an_intermediate_screen():
    assert forms.interstitial_button(view(["Skip", "Next"], fields=[object()])) is None


# --- the adapter's decision ----------------------------------------------------------------------------------------------------

def test_the_adapter_moves_on_from_the_real_amazon_page_and_says_what_it_clicked():
    logged, env = [], {}
    out = adapter.fieldless_page(view(AMAZON), env, 1, logged.append)
    assert out == {"state": "page_complete", "blockers": [], "interstitial": "Skip & continue"}
    assert any("moving on with 'Skip & continue'" in x for x in logged) and env["_interstitial_streak"] == 1


def test_the_adapter_still_blocks_a_fieldless_page_with_no_safe_button():
    for buttons in ([], ["Enable SMS updates"], ["Submit application"]):
        out = adapter.fieldless_page(view(buttons), {}, 1, lambda m: None)
        assert out["state"] == "blocked" and out["blockers"] == ["no application fields discovered"]


def test_the_same_intermediate_page_coming_back_is_a_stuck_loop_not_an_endless_click():
    env, outs = {}, []
    for _ in range(adapter.MAX_INTERSTITIAL_STREAK + 1):
        outs.append(adapter.fieldless_page(view(["Skip & continue"]), env, 1, lambda m: None))
    assert [o["state"] for o in outs] == ["page_complete"] * adapter.MAX_INTERSTITIAL_STREAK + ["blocked"]
    assert "stuck on an intermediate page" in outs[-1]["blockers"][0]


def test_the_streak_is_per_run_state_so_a_new_run_starts_clean():
    assert adapter.fieldless_page(view(["Next"]), {}, 3, lambda m: None)["state"] == "page_complete"


def test_a_dash_inside_the_exact_posting_title_is_not_punctuation_the_writer_chose():
    """Intel's posting is titled 'Software Engineer – Datacenter'; the summary must open with that exact title and may not use dashes."""
    import re
    from jobagent.tools import tailor
    dash = tailor.DASH
    title = "Software Engineer – Datacenter"
    summary = f"{title} with 5 years building Python services, focused on reliability."
    assert dash.search(summary) and not dash.search(summary.replace(title, " "))
    assert dash.search((summary + " Built tools – fast ones.").replace(title, " "))      # a dash the writer added is still caught


def test_a_sign_in_form_is_a_credentials_page_but_an_application_page_with_account_fields_is_not():
    """iCIMS's candidate profile has Login / Password / Re-enter password plus sixty more fields: fill the rest, never the passwords."""
    from jobagent.filling.planned import credentials_only_page
    F = lambda kind, label: forms.Field(id=0, kind=kind, label=label)
    assert credentials_only_page([F("text", "Username or email address *"), F("password", "Password *")])
    assert credentials_only_page([F("password", "Password")])
    assert credentials_only_page([F("email", "Email"), F("password", "Password"), F("password", "Password (Re-enter)")])
    page = [F("text", "Login"), F("password", "Password"), F("password", "Password (Re-enter)"), F("text", "First Name"), F("text", "Phone")]
    assert not credentials_only_page(page)


def test_a_bare_city_in_a_sentence_style_education_or_employer_section_never_takes_the_current_or_job_address():
    """Garmin's iCIMS profile asked for the school's city and got the job's Olathe: the current address is not a school's or employer's."""
    from jobagent.filling.facts import Fact, scope_allows, loose_entity_scope
    address = Fact(id="applicant/address/Current address/answer/city", aliases=["City", "Current address"], value="Olathe", source="job lookup")
    edu = forms.Field(id=1, kind="text", label="City", section="Please enter the highest level of education you have attained")
    emp = forms.Field(id=2, kind="text", label="State", section="Please enter your last three (3) employers")
    contact = forms.Field(id=3, kind="text", label="City", section="Enter your information")
    assert loose_entity_scope(edu.section) == "education" and loose_entity_scope(emp.section) == "employment"
    assert loose_entity_scope("Enter your information") is None and loose_entity_scope("Education 2") is None
    assert not scope_allows(edu, address) and not scope_allows(emp, address)
    assert scope_allows(contact, address)         # the contact section still takes the current address


def test_a_password_the_account_already_holds_is_not_an_unverified_prefill_but_an_empty_required_one_still_blocks():
    from jobagent.steps import apply as A
    entry = {"filled": "", "answer": "", "verified": False, "planner_failed": False, "required": True, "answered": False, "clear": False}
    held = forms.Field(id=1, kind="password", label="Password", value="••••••••")
    empty = forms.Field(id=2, kind="password", label="Password", value="")
    assert A.outcome_of(dict(entry), held) == "verified"
    assert A.outcome_of(dict(entry), empty) != "verified"


def test_a_bare_repo_title_is_replaced_by_the_repo_name_with_its_words_split_and_no_model_is_called(monkeypatch):
    """Ericsson's Baseband resume failed its gate because the model could not title 'DynamicTaskAllocation' with words its card has."""
    from jobagent.steps import multi_agent as M
    card = {"full_name": "someone/DynamicTaskAllocation", "title": "DynamicTaskAllocation"}
    monkeypatch.setattr(M.P, "saved_evidence", lambda st: {"someone/dynamictaskallocation": card})
    st = {"kept": 2, "ranked": [{"repo": "someone/SolBot", "title": "SolBot"}, {"repo": "someone/DynamicTaskAllocation", "title": "DynamicTaskAllocation"}]}
    assert M._readable_titles(st, [2]) == {2: "Dynamic Task Allocation"}
    assert M._readable_titles(st, [3]) is None            # not a displayed project: the model path handles it
    card2 = {"full_name": "someone/hiring", "title": "hiring"}
    monkeypatch.setattr(M.P, "saved_evidence", lambda st: {"someone/hiring": card2})
    assert M._readable_titles({"kept": 1, "ranked": [{"repo": "someone/hiring"}]}, [1]) is None       # a one-word slug has no better title


def test_a_resume_upload_whose_help_text_mentions_cover_letters_is_still_the_resume():
    """Garmin's iCIMS: 'Please upload your resume ... Cover letters ... can be added to Additional Attachments' put cover_letter.pdf in the resume field."""
    from jobagent.steps.apply import upload_role
    caption = ("My Computer (Opens new window) Please upload your resume (max size: 5MB). Cover letters or other attachments can be added to the "
               '"Additional Attachments" field at the bottom of this application. / PortalProfileFields.Resume_File')
    assert upload_role(caption) == "resume"
    assert upload_role("Cover Letter / PortalProfileFields.CoverLetter_File") == "cover_letter"
    assert upload_role("Attach Additional Documents / PortalProfileFields.Additional_File") == ""


def test_an_empty_required_password_is_reported_as_the_candidates_to_type():
    from jobagent.steps import apply as A
    out = A.blockers_of([{"label": "Password", "page": 1, "required": True, "kind": "password", "outcome": "unanswered (required)"},
                         {"label": "Phone", "page": 1, "required": True, "kind": "text", "outcome": "unanswered (required)"}])
    assert "from the vault" in out[0] and "only in the browser window" in out[0] and "left empty" in out[1]


def test_a_borrowed_documents_run_is_never_ready_to_submit_and_says_so_in_the_report():
    from jobagent.steps import apply as A
    result = {"url": "https://x", "state": "review", "ready_to_submit": False, "blockers": [], "fields": [], "verified": 0, "flags": [], "outcomes": {},
              "borrowed_documents": "outputs/zoox/abc"}
    assert "TEST RUN" in A.report(result, {"company": "Acme", "title": "Engineer"}) and "outputs/zoox/abc" in A.report(result, {"company": "Acme", "title": "Engineer"})


def test_context_reads_the_resume_and_cover_letter_from_the_borrowed_folder(tmp_path):
    from pathlib import Path
    from jobagent.steps import apply as A
    job, docs = tmp_path / "job", tmp_path / "docs"
    job.mkdir(); docs.mkdir()
    (job / "jd.json").write_text('{"company": "Acme", "title": "Engineer", "locations_resolved": []}')
    (docs / "resume.tex").write_text("\\\\documentclass{article}")
    ctx = A.context(job, "acme", docs)
    assert ctx["_jd"]["company"] == "Acme" and ctx["COVER_LETTER"] == "(none)"          # nothing in the job folder was needed
