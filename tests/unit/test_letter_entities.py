"""Letter attribution: an achievement must stay with the employer or project it came from, however the employer's name is written."""
import pytest

from jobagent.tools import letter_attribution as L
from types import SimpleNamespace

BANK = {"employment:0": {"entity": "Anton Intelligence LLC", "kind": "employment", "text": "Engineer at Anton Intelligence LLC\nCut latency by 30%. Led a platform migration."},
        "employment:1": {"entity": "Accurate Industrial Controls Pvt Ltd", "kind": "employment", "text": "Engineer at Accurate Industrial Controls Pvt Ltd\nBuilt PLC dashboards."},
        "employment:2": {"entity": "Meta", "kind": "employment", "text": "Engineer at Meta\nBuilt feeds."},
        "skills": {"entity": "Technical Skills", "kind": "skills", "text": "Languages: Python"}}


def letter(sentence, source_id=None, quote="Cut latency by 30%."):
    bindings = [L.Binding(claim=sentence, source_id=source_id, quote=quote)] if source_id else []
    return SimpleNamespace(paragraphs=[SimpleNamespace(text=sentence, evidence=[], bindings=bindings)])


def issues(sentence, source_id=None, **kw):
    return L.binding_issues(letter(sentence, source_id, **kw), BANK)


def test_a_suffix_free_employer_name_still_binds_the_achievement_to_that_employer():
    wrong = issues("At Accurate Industrial Controls I cut latency by 30%.", "employment:0")
    assert any("different employer" in i for i in wrong), wrong
    assert not issues("At Anton Intelligence I cut latency by 30%.", "employment:0")
    assert not issues("At Anton Intelligence LLC I cut latency by 30%.", "employment:0")


def test_a_quantified_claim_naming_two_employers_must_be_split():
    got = issues("At Anton Intelligence and Accurate Industrial Controls I cut latency by 30%.", "employment:0")
    assert any("another employer" in i or "different employer" in i for i in got), got


def test_an_employer_named_in_a_plain_sentence_still_needs_a_bound_quote_when_written_without_its_suffix():
    got = L.binding_issues(letter("At Anton Intelligence I led a platform migration."), BANK)
    assert any("lacks an entity-bound quote" in i for i in got), got


def test_employer_names_match_whole_words_only():
    assert not issues("I built metadata pipelines, cutting load time 30%.", "employment:0")         # 'meta' is inside 'metadata'
    assert not issues("I cut latency by 30% on a plain platform.", "employment:0")
    got = issues("At Meta I cut latency by 30%.", "employment:0")
    assert any("different employer" in i for i in got), got


def test_aliases_drop_corporate_suffixes_but_never_become_generic():
    assert L.aliases("Anton Intelligence LLC") >= {"anton intelligence llc", "anton intelligence"}
    assert L.aliases("Accurate Industrial Controls Pvt Ltd") >= {"accurate industrial controls"}
    assert L.aliases("Logos Robotics Lab") >= {"logos robotics lab", "logos robotics"}
    assert L.aliases("Acme Inc.") >= {"acme"} and "inc" not in L.aliases("Acme Inc.")
    assert L.aliases("Acme Co Inc") >= {"acme"} and "co" not in L.aliases("Acme Co Inc") and "acme co" in L.aliases("Acme Co Inc")


@pytest.mark.parametrize("employer", ["SAP", "IBM", "AMD", "ASU"])
def test_a_short_owner_employer_is_still_bound_and_never_matches_inside_another_word(employer):
    bank = {**BANK, "employment:9": {"entity": employer, "kind": "employment", "text": f"Engineer at {employer}\nCut costs by 40%."}}
    plain = L.binding_issues(letter(f"At {employer} I cut costs by 40%.", "employment:0", quote="Cut latency by 30%."), bank)
    assert any("different employer" in i for i in plain), plain                            # the achievement is Anton's, not the short employer's
    assert not L.binding_issues(letter(f"At {employer} I cut costs by 40%.", "employment:9", quote="Cut costs by 40%."), bank)
    inside = L.binding_issues(letter("I measured and casually reviewed 30% of the samples.", "employment:0", quote="Cut latency by 30%."), bank)
    assert not any("different employer" in i for i in inside), inside                       # 'asu' in 'measured/casually' is not ASU
