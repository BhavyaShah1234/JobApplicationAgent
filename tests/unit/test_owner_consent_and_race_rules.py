"""Owner decisions of 2026-11-24: standing yes for the demographic-data consent and the AI-policy acknowledgement; a form that
splits 'Asian' by region gets the decline option, else 'Other', never a region chosen for the candidate."""
from jobagent.filling import routing
from jobagent.filling.facts import choice
from jobagent.tools.forms import Field

MONGO_CONSENT = ("By checking this box, I consent to MongoDB collecting, storing, and processing my responses to the demographic "
                 "data surveys above. *")


def test_the_two_new_consent_kinds_have_their_own_families_and_do_not_leak_into_others():
    assert routing.family(MONGO_CONSENT) == "demographic_consent"
    assert routing.family("AI Policy for Interviewers *") == "ai_policy_ack"
    assert routing.family("Consent to processing of voluntary demographic survey responses") == "demographic_consent"
    assert routing.family("Acknowledgement of the employer's AI policy for interviewers") == "ai_policy_ack"
    # unchanged neighbours
    assert routing.family("we may use AI notetakers to transcribe conversations") == "ai_consent"
    assert routing.family("Applicant Privacy Acknowledgement") == "privacy_consent"
    assert routing.family("Race *") == "race"


SAMSARA = ["Black / of African descent", "East Asian (inclusive of Chinese, Japanese, Korean, Mongolian, Tibetan, and Taiwanese)",
           "Latinx / Hispanic", "Middle Eastern", "South Asian (inclusive of Bangladeshi, Bhutanese, Indian, Nepali, Pakistani, and Sri Lankan)",
           "Two or more races", "White", "I prefer to self describe", "I don't wish to answer"]


def race(options):
    return choice(Field(1, "combobox", "How do you identify? (race/ethnicity) *", options=options), ["Asian"])


def test_asian_split_by_region_gets_the_decline_option_never_a_region():
    assert race(SAMSARA) == "I don't wish to answer"


def test_without_a_decline_option_the_answer_is_other_and_without_either_nothing_is_guessed():
    assert race(["East Asian", "South Asian", "White", "Other"]) == "Other"
    assert race(["East Asian", "South Asian", "White"]) is None


def test_a_plain_asian_option_is_used_as_it_is():
    assert race(["Asian", "Black or African American", "White", "I don't wish to answer"]) == "Asian"
