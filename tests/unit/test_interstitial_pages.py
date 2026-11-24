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
