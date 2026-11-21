"""A generated exclusivity claim (only/solely/exclusively) needs the project's own card to state that restriction."""
import json
from pathlib import Path

import pytest

from jobagent.steps import multi_agent as M
from jobagent.tools import projects as P

SOLBOT = json.loads((Path(__file__).parent.parent / "fixtures" / "solbot_scope_card.json").read_text())
# The exact entry of the live Samsara packet that both independent PDF reviewers rejected.
FAILED = {"repo": "BhavyaShah1234/SolBot", "title": "SolBot, Multi-Agent Chatbot", "stack": ["Python", "Ollama", "Chroma", "LangChain", "SQLite"],
          "paragraph": "SolBot is a fully local, self-auditing RAG chatbot designed to answer questions about ASU Research Computing using only official documentation. "
                       "The system decomposes compound questions into sub-questions, researches each independently, and audits findings for groundedness before "
                       "streaming cited replies. It ensures robustness through input sanitization, adversarial fuzz testing, and a self-auditing answer loop.",
          # fixture contract: a displayed variant is whole source sentences of its own card (this one is the card's results[1], unchanged)
          "short": SOLBOT["results"][1]}
CARDS = {"bhavyashah1234/solbot": SOLBOT}


def state(entry):
    """A one-project projects.json state."""
    return {"ranked": [dict(entry)], "kept": 1, "short": []}


def test_actual_failed_packet_is_rejected_by_the_production_acceptance_path():
    findings = P.content_findings(state(FAILED), CARDS)
    assert len(findings) == 1 and "only official documentation" in findings[0] and "restriction" in findings[0]
    # the identical hard checks without scope evidence (the unchanged baseline: completeness + stack + achieved standards) find nothing
    assert not [f for f in findings if "complete sentence" in f or "technologies" in f]
    assert not P.standard_outcome_claims(FAILED["paragraph"], SOLBOT)


def test_actual_packet_is_recovered_from_the_cards_own_sentences_after_bounded_repairs():
    ctx = {"cards": CARDS, "candidates": [], "log": lambda *_: None}
    st = state(FAILED)
    M._recover_scope(ctx, st)
    assert st["ranked"][0]["paragraph"] == FAILED["paragraph"]            # first failed repair: the model gets another try
    M._recover_scope(ctx, st)
    entry = st["ranked"][0]
    assert not P.content_findings(st, CARDS)
    assert entry["short"] == FAILED["short"]                              # a variant with no unsupported restriction is left alone
    text = entry["paragraph"]
    assert P.MIN_PARAGRAPH <= len(text) <= P.MAX_PARAGRAPH and text[-1] in ".!?" and " only " not in text and "\u2014" not in text
    sentences = [x for x in SOLBOT["results"] + [SOLBOT["summary"]]]
    for part in entry["paragraph"].split(". "):                         # every piece is the card's own complete sentence, no invented punctuation
        assert any(part.rstrip(".") in s for s in sentences)


def test_unrecoverable_entry_keeps_its_finding_instead_of_printing_a_false_text():
    card = {"summary": "Reads documents. Answers (e.g.", "results": []}
    entry = {"repo": "o/r", "title": "T", "stack": ["Python"], "paragraph": "It answers using only official documentation. " * 2, "short": ""}
    assert P.recover_scope(entry, card) == []
    assert entry["paragraph"].startswith("It answers using only")


@pytest.mark.parametrize("claim, card", [
    ("Answers questions using only official documentation.", {"summary": "The bot answers questions using only official documentation."}),
    ("Answers only from official docs.", {"summary": "Answers are drawn only from official documentation."}),
    ("Answers from official documentation only.", {"summary": "The assistant uses only official documentation."}),
    ("It runs solely on local hardware.", {"results": ["Runs solely on local hardware with no cloud calls."]}),
    ("It never uses the network, exclusively reading local files.", {"summary": "Exclusively reads local files."}),
])
def test_explicit_source_backed_restrictions_and_faithful_rewording_are_kept(claim, card):
    assert not P.scope_claims(claim, card)


@pytest.mark.parametrize("claim", [
    "It answers using only official documentation.",
    "It answers from official documentation exclusively.",
    "It works solely from the Confluence space.",
])
def test_primary_corpus_with_documented_fallback_does_not_support_exclusivity(claim):
    card = {"summary": "Retrieves from the official documentation Confluence space, with an open-web fallback and fetching of URLs the user pastes."}
    assert P.scope_claims(claim, card)


@pytest.mark.parametrize("claim", [
    "It supports not only Python but also Go.",
    "The system is not only a chatbot but also a sync engine.",
    "It does not rely only on vector search.",
    "It was the only entry that passed review.",
    "Only three agents run in parallel.",
    "It re-plans only once when verification finds gaps.",       # conditional/count wording about behaviour the card also states below
])
def test_negation_not_only_uniqueness_and_counts_are_not_restrictions(claim):
    card = {"summary": "Re-plans only once when verification finds gaps."}
    assert not P.scope_claims(claim, card)


def test_condition_is_not_dropped_into_an_unconditional_restriction():
    card = {"summary": "Streams replies only after verification passes."}
    assert not P.scope_claims("Streams replies only after verification passes.", card)
    assert P.scope_claims("Streams replies only from official documentation.", card)


def test_a_restriction_about_other_words_in_the_card_does_not_vouch():
    card = {"summary": "Writes only to a local SQLite file and reads the open web."}
    assert P.scope_claims("Answers using only official documentation.", card)


def test_unrelated_own_repo_evidence_does_not_support_another_repos_restriction():
    other = {"summary": "Uses only official documentation."}
    assert P.content_findings(state(FAILED), {"bhavyashah1234/solbot": SOLBOT, "o/other": other})
    assert P.scope_claims(FAILED["paragraph"], other)  == []


def test_ordinary_qualitative_benefits_and_source_established_numbers_are_untouched():
    paragraph = ("Built a robust, secure and production-grade RAG chatbot that ensures reliability and ease of use. "
                 "Fuzz-tested against 23 adversarial scenarios with a self-auditing answer loop.")
    entry = dict(FAILED, paragraph=paragraph, short=paragraph)
    assert P.content_findings(state(entry), CARDS) == []


def test_recovery_text_is_complete_card_sentences_without_punctuation_dashes():
    card = {"summary": "Built a thing \u2014 with a dash. It answers using only the open web.",
            "results": ["A working pipeline from sources to cited answers with sync.", "Verified findings are hedged, not stated as fact, before any reply."]}
    text = P.faithful_description(card, 470, 100)
    assert text and "\u2014" not in text and text.endswith("reply.") and "Built a thing" not in text
    assert P.faithful_description({"summary": "Fragment (e.g. one", "results": []}, 470, 100) is None
