"""Project prose is whole sentences of the project's OWN saved card, selected by number, never rewritten (GEN49 root correction).

The captured packet is the exact projects.json of the Coram native run (a9e3d0d) whose independent primary-source review rejected "enabling
real-time monitoring and control of robotic systems": the card describes monitoring and data flow only, and the repository's control page simulates
React state. The contract is structural, so these tests use no control/robot word list: ANY added verb, clause or feature fails the same way.
"""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from jobagent.config import get_settings
from jobagent.state import JobDescription
from jobagent.steps import ats as A, multi_agent as M
from jobagent.tools import documents as D, projects as P, tailor as T

PACKET = json.loads((Path(__file__).parent.parent / "fixtures" / "coram_a9e3d0d_projects.json").read_text())
DEVIL, CLOUD, WICS, VISION = ("bhavyashah1234/devilsinventhackathon", "ak-asu/cloudforge", "2026-asu-wics-opportunity-hack/24-sync-solve", "forgeopus/visionforge")
REJECTED = next(e for e in PACKET["ranked"] if e["repo"].casefold() == DEVIL)["paragraph"]
FILLED = T.Filled.model_construct(summary="Engineer with Python experience.", roles=[], skills=[], notes="")
# the owner's template titles for the packet's repositories (VisionForge's carries the hackathon win)
TEMPLATE = (r"\section{Projects}\resumeProjectHeading{\textbf{\href{https://github.com/ForgeOpus/visionforge}{VisionForge, Claude Builder Club Hackathon Winner}}"
            r" $|$ \emph{React}}{November 2025}\resumeProjectText{x}\section{Education}x")


@pytest.fixture(autouse=True)
def isolate_settings_cache():
    """A temporary DATA_DIR must not remain cached after monkeypatch restores the environment."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def packet():
    """A private copy of the captured packet."""
    return copy.deepcopy(PACKET)


def card(repo=DEVIL, st=None):
    """The saved evidence card of one repository of a packet."""
    return P.saved_evidence(st or PACKET)[repo]


def entry(st, repo=DEVIL):
    """The ranked entry of a repository."""
    return next(e for e in st["ranked"] if e["repo"].casefold() == repo)


def bound(st, keep=()):
    """A packet whose entries (except `keep`) carry the faithful whole-sentence variants of their own cards and card-supported titles."""
    st = copy.deepcopy(st)
    for e in st["ranked"]:
        if e["repo"].casefold() not in keep:
            c = P.saved_evidence(st)[e["repo"].casefold()]
            full, short = P.faithful_variants(c)
            owner = P.known_entries(TEMPLATE).get(e["repo"].casefold())
            e.update(paragraph=full, short=short or "", title=owner[0] if owner else P.card_title(c))
    return st


# --- the sentence pool and the binding check --------------------------------------------------------------------------------------------

SAMPLE = {"full_name": "o/r", "title": "Warehouse Monitor: inventory dashboard",
          "summary": ("A prototype dashboard that shows robot-collected inventory levels from a simulated warehouse. "
                      "It does not control any robot; control commands are a planned feature. "
                      "The README claims alerts reach staff within seconds, which was not measured."),
          "results": ["Reads sensor messages over rosbridge and stores them in SQLite for the dashboard.",
                      "fragment without a capital or end", "Ends without terminal punctuation but is long enough to look complete"]}


def test_pool_holds_only_complete_sentences_in_card_order_without_synthetic_terminators():
    pool = P.source_sentences(SAMPLE)
    assert pool[0].startswith("A prototype dashboard") and pool[-1].startswith("Reads sensor messages")
    assert len(pool) == 4 and all(s[-1] in ".!?" for s in pool)
    assert not any("fragment" in s or "Ends without" in s for s in pool)               # nothing is completed by adding a period


@pytest.mark.parametrize("text, pieces", [
    ("Uses tools, e.g. Redis and Kafka. Runs on Linux.", 2),
    ("Built for the U.S. Navy and its partners. Shipped in 2024.", 2),
    ("Latency fell from 3.5 s to 1.2 s. Accuracy rose.", 2),
    ("One sentence only.", 1)])
def test_an_abbreviation_never_ends_a_sentence_so_no_half_sentence_is_a_piece(text, pieces):
    assert len(P.split_sentences(text)) == pieces and " ".join(P.split_sentences(text)) == text


def test_the_card_sentences_are_bound_but_any_edit_of_them_is_not():
    pool = P.source_sentences(SAMPLE)
    assert P.bound_text(pool[0], SAMPLE) == pool[0]
    assert P.bound_text(f"{pool[3]} {pool[0]}", SAMPLE) == f"{pool[3]} {pool[0]}"      # selection order is the writer's
    for bad in (pool[0].rstrip("."),                                                   # a missing terminator is not completed
                pool[0][:40].rsplit(" ", 1)[0] + ".",                                  # a truncated half sentence with an invented period
                pool[0].replace("shows", "controls"),                                  # a changed verb
                pool[0][:-1] + ", enabling real-time control.",                        # an added clause
                pool[1].replace("does not control", "controls"),                       # a dropped negation
                pool[1].replace("; control commands are a planned feature", ""),       # a dropped qualifier
                f"{pool[0]} {pool[0]}",                                                # a repeated sentence
                f"{pool[0]} Supports predictive maintenance.",                         # another invented feature
                "", "   "):
        assert P.bound_text(bad, SAMPLE) is None, bad


def test_another_repositorys_sentence_is_never_bound_to_this_one():
    other = {"full_name": "o/other", "summary": "A cloud platform that provisions infrastructure from plain-English requirements."}
    assert P.bound_text(other["summary"], other) and P.bound_text(other["summary"], SAMPLE) is None


@pytest.mark.parametrize("sentence", ["Built a thing — with a spaced dash in it for show.", "Unbalanced (bracket opens and never closes here.",
                                      'It said "ready and then nothing closes it at all.', "Too short."])
def test_dashes_unbalanced_quotes_and_tiny_fragments_are_not_usable_source_sentences(sentence):
    assert P.source_sentences({"summary": sentence}) == []


# --- compose: selection by number ----------------------------------------------------------------------------------------------------

def test_a_selection_prints_exactly_the_chosen_sentences_in_the_chosen_order():
    pool = P.source_sentences(SAMPLE)
    assert P.compose(SAMPLE, [4, 1], 470, 100) == f"{pool[3]} {pool[0]}"
    assert P.compose(SAMPLE, [2], 470, 0) == pool[1]                                  # qualifiers and negation travel with their sentence


@pytest.mark.parametrize("ids", [[], [0], [5], [-1], [1, 1], [True], list(range(1, 8)), ["1"]])
def test_missing_duplicated_and_out_of_range_numbers_are_rejected(ids):
    with pytest.raises(ValueError):
        P.compose(SAMPLE, ids, 470, 0)


def test_sentences_over_the_limit_are_left_out_whole_never_cut():
    pool = P.source_sentences(SAMPLE)
    limit = len(pool[0]) + len(pool[1]) + 1
    assert P.compose(SAMPLE, [1, 2, 3], limit, 0) == f"{pool[0]} {pool[1]}"
    with pytest.raises(ValueError, match="no complete useful prefix"):
        P.compose(SAMPLE, [3], 40, 0)                                                  # one sentence longer than the limit: nothing to print
    with pytest.raises(ValueError, match="totalling"):
        P.compose(SAMPLE, [2], 470, 300)


def test_the_faithful_selection_keeps_qualifiers_prefers_posting_words_and_prints_in_card_order():
    terms = P.job_terms(JobDescription(title="Engineer", company="Acme", technical_skills=["rosbridge", "SQLite"]))
    text = P.faithful_description(SAMPLE, 200, 60, None, terms)
    assert text == "Reads sensor messages over rosbridge and stores them in SQLite for the dashboard."      # the relevant sentence is taken first
    full = P.faithful_description(SAMPLE, 470, 300)
    assert "prototype" in full and "does not control" in full and "not measured" in full and "planned" in full
    assert P.bound_text(full, SAMPLE)


# --- the exact rejected packet ----------------------------------------------------------------------------------------------------------

def test_the_rejected_coram_description_is_not_bound_and_the_card_never_says_control():
    assert "enabling real-time monitoring and control" in REJECTED
    assert P.bound_text(REJECTED, card()) is None and P.bound_text(entry(packet())["short"], card()) is None
    assert not any("control" in s.lower() for s in P.source_sentences(card()))        # the card's own words: monitoring and data flow
    findings = P.binding_findings(packet(), P.known_entries(TEMPLATE))
    mine = [f for f in findings if f.startswith("project 2 ")]
    assert any("full description is not made of complete sentences" in f and "monitoring and control" in f for f in mine)
    assert any("short description" in f and "enabling real-time monitoring and control" in f for f in mine)


def test_every_prose_variant_of_the_captured_packet_predates_the_contract_and_is_reported():
    findings = P.binding_findings(packet(), P.known_entries(TEMPLATE))
    assert {f.split(" ")[1] for f in findings if "source card" in f} == {"1", "2", "3", "4"}      # the legacy packet needs regeneration
    assert not any("template title" in f for f in findings)                         # titles were fine (VisionForge's is the owner's)


def test_the_faithful_replacement_of_the_rejected_packet_is_only_the_cards_own_sentence():
    full, short = P.faithful_variants(card())
    assert full == P.source_sentences(card())[0] and "automated alerting." in full and "control" not in full.lower()
    assert short is None                                                                # the only sentence is longer than a short variant: skipped, not cut


def test_a_different_invented_feature_fails_exactly_like_the_control_one():
    for invented in ("{p} It also enables predictive maintenance and automatic fault diagnosis.",
                     "Built a robotics web platform with {p_cut}, supporting fleet-wide over-the-air updates."):
        text = invented.format(p=P.source_sentences(card())[0], p_cut=P.source_sentences(card())[0].rstrip("."))
        assert P.bound_text(text, card()) is None


# --- the normal acceptance path: saved job, saved evidence, no flags ------------------------------------------------------------------

def normal_job(tmp_path, monkeypatch, state, template=TEMPLATE, d=None):
    """A job folder whose resume_findings runs the real hard_issues and projects.json checks; only layout/TeX/LLM machinery is stood in."""
    if d is None:
        d = tmp_path / "job"
        d.mkdir()
        (d / "resume.pdf").write_bytes(b"%PDF")
    (tmp_path / "data" / "templates").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "templates" / "resume.tex").write_text(template)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    get_settings.cache_clear()
    (d / "filled.json").write_text(FILLED.model_dump_json())
    (d / "jd.json").write_text('{"title":"Engineer","company":"Acme"}')
    (d / "projects.json").write_text(json.dumps(state))
    monkeypatch.setattr(T, "Filled", SimpleNamespace(model_validate_json=lambda s: FILLED))
    monkeypatch.setattr(T, "parse", lambda text: SimpleNamespace(roles=[], skills=[], tex=text))
    monkeypatch.setattr(M.layout, "check", lambda _: M.layout.Layout(pages=1, free_pt=0))
    for name, value in (("duplicates", lambda *a, **k: []), ("quality", lambda *a, **k: []), ("fill_checks", lambda *a, **k: ([], 0)),
                        ("declared_claims", lambda: {})):
        monkeypatch.setattr(T, name, value)
    monkeypatch.setattr(M, "summary_issues", lambda *a: [])
    monkeypatch.setattr(M, "_title_prefix", lambda *a: True)
    monkeypatch.setattr("jobagent.tools.skillgraph.violations", lambda *a: [])
    return d


def test_normal_resume_findings_blocks_the_exact_rejected_packet_from_its_own_saved_card(tmp_path, monkeypatch):
    found = D.resume_findings(normal_job(tmp_path, monkeypatch, packet()))
    assert found["evidence"]
    text = "\n".join(h for h in found["hard"] if h.startswith("project 2 "))
    assert "not made of complete sentences of its own source card" in text and "enabling real-time monitoring and control" in text
    assert not (tmp_path / "state" / "repo_cards").exists()                           # no shared cache decided anything


def test_after_faithful_replacement_the_normal_path_accepts_the_same_job(tmp_path, monkeypatch):
    st = packet()
    for e in st["ranked"]:
        c = P.saved_evidence(st)[e["repo"].casefold()]
        full, short = P.faithful_variants(c)
        e.update(paragraph=full, short=short or "")
    found = D.resume_findings(normal_job(tmp_path, monkeypatch, st))
    assert found["hard"] == [], found["hard"]
    assert "control" not in " ".join(e["paragraph"] for e in st["ranked"] if e["repo"].casefold() == DEVIL).lower()


def test_an_accepted_flag_or_copied_proof_text_cannot_stand_in_for_the_binding(tmp_path, monkeypatch):
    st = packet()
    st["bound"] = st["source_bound"] = st["accepted"] = True
    st["proof"] = {DEVIL: entry(st)["paragraph"]}
    e = entry(st)
    e["accepted"] = e["source_ids"] = True
    found = D.resume_findings(normal_job(tmp_path, monkeypatch, st))
    assert any("not made of complete sentences" in h and h.startswith("project 2 ") for h in found["hard"])


def test_missing_saved_evidence_is_still_a_blocker_for_the_new_contract(tmp_path, monkeypatch):
    st = bound(packet())
    del st["evidence"]
    hard = D.resume_findings(normal_job(tmp_path, monkeypatch, st))["hard"]
    assert hard and any("no saved source evidence" in h for h in hard)


def test_the_mutable_shared_card_cache_cannot_repair_or_break_the_binding(tmp_path, monkeypatch):
    st = bound(packet())
    d = normal_job(tmp_path, monkeypatch, st)
    before = D.resume_findings(d)["hard"]
    cache = tmp_path / "state" / "repo_cards"
    cache.mkdir(parents=True)
    (cache / "bhavyashah1234__devilsinventhackathon.json").write_text(json.dumps({**card(), "summary": "Controls robots. " * 30}))
    assert D.resume_findings(d)["hard"] == before == []


def test_evidence_under_another_repository_cannot_bind_a_projects_prose(tmp_path, monkeypatch):
    st = bound(packet())
    st["evidence"][DEVIL] = {**st["evidence"][CLOUD]}                                  # CloudForge's card stored under the Devil's Invent name
    hard = D.resume_findings(normal_job(tmp_path, monkeypatch, st))["hard"]
    assert any("no saved source evidence" in h and h.startswith("project 2 ") for h in hard)


def test_cross_repository_prose_is_not_bound_to_the_repository_it_is_filed_under(tmp_path, monkeypatch):
    st = bound(packet())
    entry(st)["paragraph"] = entry(st, CLOUD)["paragraph"]                             # CloudForge's whole sentences, shown under Devil's Invent
    findings = P.binding_findings(st, P.known_entries(TEMPLATE))
    assert [f for f in findings if f.startswith("project 2 ") and "full description" in f] and not [f for f in findings if f.startswith("project 1 ")]


def test_a_tampered_sentence_in_a_bound_packet_is_caught(tmp_path, monkeypatch):
    st = bound(packet())
    entry(st)["paragraph"] = entry(st)["paragraph"].replace("automated alerting", "automated alerting and robot control")
    hard = D.resume_findings(normal_job(tmp_path, monkeypatch, st))["hard"]
    assert any(h.startswith("project 2 ") and "not made of complete sentences" in h for h in hard)


def test_a_hidden_ranked_project_with_unbound_prose_is_reported_before_a_layout_change_can_show_it(tmp_path, monkeypatch):
    st = bound(packet())
    st["kept"] = 2
    entry(st, VISION)["short"] = "VisionForge simplifies deep learning for non-experts."
    assert any(f.startswith("project 4 ") and "short description" in f for f in P.binding_findings(st, {}))


def test_refresh_cannot_leave_an_old_freeform_packet_marked_usable(tmp_path, monkeypatch):
    from test_documents import GOOD_RESUME, assess, job
    d = job(tmp_path)
    D.save(d, assess(d, resume=GOOD_RESUME))
    assert D.load(d)["valid"] is True
    normal_job(tmp_path, monkeypatch, packet(), d=d)
    new = D.refresh(d, "warn", 90.0)
    assert new["valid"] is False and any("not made of complete sentences" in h for h in new["resume"]["hard_issues"]) and D.verify(d)["ok"] is False
    normal_job(tmp_path, monkeypatch, bound(packet()), d=d)                           # regeneration from the same evidence clears it, nothing else does
    assert not [h for h in D.refresh(d, "warn", 90.0)["resume"]["hard_issues"] if "source card" in h]


# --- title and date binding ----------------------------------------------------------------------------------------------------------

def test_a_generated_title_may_use_only_words_of_its_own_card_title():
    c = card()
    assert P.title_supported("Devil's Invent Hackathon", c) and P.title_supported("Devil's Invent: Autonomous Robotics", c)
    for invented in ("Real-Time Robot Control Platform", "Devil's Invent Hackathon Winner", "Autonomous Robotics Control", "Devil's Invent Robotics Award"):
        assert not P.title_supported(invented, c), invented
    assert P.title_supported("VisionForge, Claude Builder Club Hackathon Winner", card(VISION), "VisionForge, Claude Builder Club Hackathon Winner")   # the owner's own
    assert not P.title_supported("Hackathon Winner", card(VISION))


def test_an_invented_title_and_a_foreign_date_are_findings_but_owner_values_are_not():
    st = bound(packet())
    entry(st)["title"] = "Real-Time Robot Control Platform"
    st["years"][entry(st)["repo"]] = "June 2031"
    found = P.binding_findings(st, P.known_entries(TEMPLATE))
    assert any(f.startswith("project 2 ") and "title" in f for f in found) and any(f.startswith("project 2 ") and "date" in f for f in found)
    st = bound(packet())
    entry(st, VISION)["title"] = "VisionForge, Claude Builder Club Hackathon Winner"
    assert not P.binding_findings(st, P.known_entries(TEMPLATE))


def test_a_date_must_be_the_owners_or_the_repositorys_own_to_the_month():
    st = bound(packet())
    own = P.month_year(card()["created_at"])
    st["years"][entry(st)["repo"]] = own
    assert not [f for f in P.binding_findings(st, P.known_entries(TEMPLATE)) if "date" in f]
    for wrong in ("March 2031", "2025", own.split()[-1], "December " + own.split()[-1]):       # another year, a bare year, or another month of the same year
        st["years"][entry(st)["repo"]] = wrong
        assert [f for f in P.binding_findings(st, P.known_entries(TEMPLATE)) if "date" in f], wrong
    assert P.recover_years(st, P.known_entries(TEMPLATE)) == [entry(st)["repo"]] and not [f for f in P.binding_findings(st, {}) if "date" in f]
    assert st["years"][entry(st)["repo"]] == own


def test_recover_title_gives_the_cards_own_title_or_the_owners():
    e = {"repo": DEVIL, "title": "Autonomous Robot Control Platform"}
    assert P.recover_title(e, card()) and P.title_supported(e["title"], card()) and not P.recover_title(e, card())
    e = {"repo": VISION, "title": "Whatever"}
    assert P.recover_title(e, card(VISION), ("Owner Title", "2025")) and e["title"] == "Owner Title"


# --- entry_from_pick / from_card ----------------------------------------------------------------------------------------------------

def pick(**kw):
    """A writer selection for the Devil's Invent card."""
    return P.ProjectPick(**{"repo": DEVIL, "title": "Devil's Invent Hackathon", "stack": ["TypeScript", "React", "ROS 2"], "paragraph_sentences": [1], "short_sentences": [], **kw})


def test_a_valid_selection_is_printed_exactly_and_an_unfillable_short_is_left_empty():
    e = P.entry_from_pick(pick(), card())
    assert e.paragraph == P.source_sentences(card())[0] and e.short == "" and e.title == "Devil's Invent Hackathon"


@pytest.mark.parametrize("ids", [[2], [0], [1, 1], [99]])
def test_an_unusable_selection_is_replaced_by_the_faithful_selection_never_by_other_text(ids):
    logged = []
    e = P.entry_from_pick(pick(paragraph_sentences=ids), card(), log=logged.append)
    assert e.paragraph == P.source_sentences(card())[0] and logged


def test_a_card_that_cannot_fill_a_full_description_gives_no_entry():
    thin = {"full_name": "o/t", "title": "T", "summary": "Does one small thing in a short sentence.", "results": []}
    assert P.entry_from_pick(P.ProjectPick(repo="o/t", title="T", stack=["Python"], paragraph_sentences=[1]), thin) is None
    assert P.from_card({**thin, "tech_stack": ["Python"], "url": "u"}) is None
    over = {"full_name": "o/t", "title": "T", "summary": "Builds " + "a very long sentence " * 40 + "end.", "tech_stack": ["Python"]}
    assert P.from_card(over) is None                                                   # no truncation to a word budget, no synthetic period


def test_an_unsupported_title_in_a_selection_becomes_the_cards_own():
    e = P.entry_from_pick(pick(title="Real-Time Robot Control Platform"), card())
    assert e.title == P.card_title(card()) and "Control" not in e.title


def test_from_card_describes_a_project_only_with_its_own_whole_sentences():
    long = {"full_name": "o/e", "title": "COLLIDE \u2014 siting", "tech_stack": ["Python"], "url": "u",
            "summary": "Ranks candidate sites for data centres using grid and market data from several public sources. "
                       "Returns an explained shortlist to planners through a simple web interface with filters and maps.",
            "results": ["The scoring weights are a prototype and were not validated against real siting decisions."]}
    e = P.from_card(long)
    assert e.title == "COLLIDE" and P.bound_text(e.paragraph, long) == e.paragraph and len(e.paragraph) >= P.FULL_FLOOR
    assert "prototype and were not validated" in e.paragraph                          # the source's qualifier travels with its sentence
    assert e.short == "" or P.bound_text(e.short, long) == e.short


# --- choose(): the real selection control flow -----------------------------------------------------------------------------------------

JD = JobDescription(title="Robotics Software Engineer", company="Coram", responsibilities=["Build robot dashboards"], technical_skills=["ROS 2", "React"])


def choose_with(monkeypatch, picks_by_round, cards=None, log=None):
    """Run P.choose with the repo-card machinery and the model replaced by scripted answers; returns (Chosen, the messages sent each round)."""
    cards = cards or [{**c, "url": f"https://github.com/{c['full_name']}"} for c in P.saved_evidence(PACKET).values()]
    sent, rounds = [], iter(picks_by_round)
    monkeypatch.setattr(P.repo_cards, "refresh", lambda *a, **k: cards)
    monkeypatch.setattr(P.repo_cards, "rank", lambda cs, text, k=None, min_substance=None, log=None: [(1.0 - i * .1, c) for i, c in enumerate(cs)])

    def model(_, schema, messages, **kw):
        sent.append(messages)
        return P.Projects(projects=next(rounds), notes="n")
    monkeypatch.setattr(P.llm, "structured_invoke", model)
    chosen = P.choose(SimpleNamespace(tex=TEMPLATE), JD, object(), "BhavyaShah1234", log=(log or (lambda *_: None)))
    return chosen, sent


def sel(repo, ids, short=(), title=None, stack=None):
    """One scripted selection."""
    c = card(repo.casefold())
    return P.ProjectPick(repo=c["full_name"], title=title or P.card_title(c), stack=stack or P.card_terms(c)[:3], paragraph_sentences=list(ids), short_sentences=list(short))


def test_choose_prints_exactly_the_selected_sentences_of_each_repository(monkeypatch):
    chosen, sent = choose_with(monkeypatch, [[sel(VISION, [1, 3]), sel(DEVIL, [1])]])
    by = {e.repo.casefold(): e for e in chosen.entries}
    assert by[DEVIL].paragraph == P.source_sentences(card())[0]
    assert by[VISION].paragraph == " ".join(P.source_sentences(card(VISION))[i] for i in (0, 2))
    for e in chosen.entries:
        assert P.bound_text(e.paragraph, card(e.repo.casefold())) == e.paragraph and (not e.short or P.bound_text(e.short, card(e.repo.casefold())))
    assert "SOURCE SENTENCES" in sent[0][1].content and "[1] (" in sent[0][1].content and "paragraph_sentences" in sent[0][0].content


def test_choose_cannot_print_a_sentence_of_another_repository(monkeypatch):
    # the model "selects" Devil's Invent with numbers that exist only on VisionForge's card: they are checked against Devil's Invent's own pool
    chosen, _ = choose_with(monkeypatch, [[sel(DEVIL, [1, 2, 3, 4]), sel(VISION, [1, 2])]] * 3)
    e = next(e for e in chosen.entries if e.repo.casefold() == DEVIL)
    assert e.paragraph == P.source_sentences(card())[0] and "control" not in e.paragraph.lower()


def test_choose_with_invalid_selections_asks_again_a_bounded_number_of_times_then_uses_faithful_text(monkeypatch):
    logged = []
    chosen, sent = choose_with(monkeypatch, [[sel(DEVIL, [9]), sel(VISION, [1, 2])]] * 3, log=logged.append)
    assert len(sent) == 3 and any("unusable source sentence selection" in m for m in logged)
    e = next(e for e in chosen.entries if e.repo.casefold() == DEVIL)
    assert P.bound_text(e.paragraph, card()) == e.paragraph and len(chosen.entries) >= P.MIN_PROJECTS


def test_choose_skips_a_project_whose_card_has_no_complete_sentences_and_tries_another(monkeypatch):
    cards = [{**c, "url": "u"} for c in P.saved_evidence(PACKET).values()]
    empty = next(c for c in cards if c["full_name"].casefold() == VISION)
    empty.update(summary="fragment", results=[])
    chosen, _ = choose_with(monkeypatch, [[sel(DEVIL, [1]), sel(CLOUD, [1, 2])]] * 3, cards=cards)
    assert VISION not in {e.repo.casefold() for e in chosen.entries} and len(chosen.entries) >= P.MIN_PROJECTS


def test_choose_fails_clearly_when_fewer_than_the_minimum_projects_have_source_sentences(monkeypatch):
    cards = [{**c, "url": "u", "summary": "fragment", "results": []} for c in P.saved_evidence(PACKET).values()]
    cards[0] = {**cards[0], "summary": P.source_sentences(card(DEVIL))[0], "full_name": "BhavyaShah1234/DevilsInventHackathon"}
    with pytest.raises(RuntimeError, match="complete source sentences"):
        choose_with(monkeypatch, [[sel(DEVIL, [1])]] * 3, cards=cards)


def test_choose_never_lets_a_model_error_turn_into_prose(monkeypatch):
    monkeypatch.setattr(P.repo_cards, "refresh", lambda *a, **k: [{**c, "url": "u"} for c in P.saved_evidence(PACKET).values()])
    monkeypatch.setattr(P.repo_cards, "rank", lambda cs, text, k=None, min_substance=None, log=None: [(1.0, c) for c in cs])
    monkeypatch.setattr(P.llm, "structured_invoke", lambda *a, **k: (_ for _ in ()).throw(TimeoutError("slow")))
    chosen = P.choose(SimpleNamespace(tex=TEMPLATE), JD, object(), "u", log=lambda *_: None)
    assert len(chosen.entries) >= P.MIN_PROJECTS
    for e in chosen.entries:
        assert P.bound_text(e.paragraph, card(e.repo.casefold())) == e.paragraph


def test_choose_propagates_configuration_errors(monkeypatch):
    monkeypatch.setattr(P.repo_cards, "refresh", lambda *a, **k: [{**c, "url": "u"} for c in P.saved_evidence(PACKET).values()])
    monkeypatch.setattr(P.repo_cards, "rank", lambda cs, text, k=None, min_substance=None, log=None: [(1.0, c) for c in cs])
    monkeypatch.setattr(P.llm, "structured_invoke", lambda *a, **k: (_ for _ in ()).throw(P.llm.LLMConfigError("no model")))
    with pytest.raises(P.llm.LLMConfigError):
        P.choose(SimpleNamespace(tex=TEMPLATE), JD, object(), "u", log=lambda *_: None)


def test_choose_pins_the_owners_template_title_and_keeps_only_grounded_stack(monkeypatch):
    chosen, _ = choose_with(monkeypatch, [[sel(VISION, [1, 3], title="Invented Award Winner", stack=["React", "Kubernetes"]), sel(DEVIL, [1])]])
    v = next(e for e in chosen.entries if e.repo.casefold() == VISION)
    assert v.title == "VisionForge, Claude Builder Club Hackathon Winner" and "Kubernetes" not in v.stack


def test_the_saved_state_carries_the_evidence_the_binding_is_checked_against(monkeypatch, tmp_path):
    chosen, _ = choose_with(monkeypatch, [[sel(DEVIL, [1]), sel(VISION, [1, 3])]])
    P.save_state(tmp_path, list(chosen.entries), chosen)
    saved = json.loads((tmp_path / "projects.json").read_text())
    assert P.binding_findings(saved, P.known_entries(TEMPLATE)) == [] and not P.missing_evidence(saved, P.saved_evidence(saved))


# --- the real repair loops (default and incremental), edits and swaps ---------------------------------------------------------------------

def edit_state(**kw):
    """A bound, evidence-carrying projects.json for the Coram cards (two displayed, two hidden)."""
    st = bound(packet())
    st.update(kept=2, short=[], **kw)
    return st


def loop(tmp_path, monkeypatch, incremental, repair, st):
    """Run edit_sections for real with the projects agent answering `repair()`."""
    d = tmp_path / ("inc" if incremental else "def")
    d.mkdir()
    (d / "projects.json").write_text(json.dumps(st))
    ctx = {"job_dir": d, "log": lambda *_: None, "incremental": incremental, "r": SimpleNamespace(roles=[], tex=TEMPLATE), "jd": JD, "models": {}}
    monkeypatch.setattr(T, "normalize_originals", lambda r, g: g)
    monkeypatch.setattr(M, "ensure_incremental_coverage", lambda c, g: g)
    monkeypatch.setattr(M, "_run_agent", lambda c, agent, ff, tasks, last: (agent, repair() if agent == "projects" else None))
    return ctx, d


def run_round(ctx, d, tasks):
    """One edit round; returns the saved state and the loop result."""
    out = M.edit_sections({"ctx": ctx, "f": FILLED, "tasks": {"projects": tasks}, "last": ""})
    return json.loads((d / "projects.json").read_text()), out


@pytest.mark.parametrize("incremental", [True, False])
def test_a_legacy_unbound_packet_reaches_faithful_recovery_in_the_first_failed_round(tmp_path, monkeypatch, incremental):
    st = packet()
    ctx, d = loop(tmp_path, monkeypatch, incremental, lambda: None, st)
    tasks = P.binding_findings(st, P.known_entries(TEMPLATE))
    assert tasks
    saved, out = run_round(ctx, d, tasks)
    assert P.binding_findings(saved, P.known_entries(TEMPLATE)) == []
    assert entry(saved)["paragraph"] == P.source_sentences(card())[0] and entry(saved)["short"] == "" or P.bound_text(entry(saved)["short"], card())
    if incremental:
        assert out.get("layout_pass") != M.LAYOUT_PASSES


@pytest.mark.parametrize("incremental", [True, False])
@pytest.mark.parametrize("invented", [
    "{s} It enables real-time control of robotic systems.",
    "{s} It also provides predictive maintenance for warehouse equipment.",
    "A robotics web platform enabling real-time monitoring and control of robotic systems and fleet-wide updates."])
def test_arbitrary_feature_prose_from_the_writer_is_refused_and_recovered_from_the_card(tmp_path, monkeypatch, incremental, invented):
    st = packet()
    text = invented.format(s=P.source_sentences(card())[0])
    repair = lambda: M.IncrementalProjectsOut(projects=[M.ProjectEdit(project=2, paragraph=text, short=text)])
    ctx, d = loop(tmp_path, monkeypatch, incremental, repair, st)
    saved, _ = run_round(ctx, d, P.binding_findings(st, P.known_entries(TEMPLATE)))
    assert entry(saved)["paragraph"] == P.source_sentences(card())[0] and "control" not in entry(saved)["paragraph"].lower()
    assert P.binding_findings(saved, P.known_entries(TEMPLATE)) == []


@pytest.mark.parametrize("incremental", [True, False])
def test_a_writer_selection_by_number_is_applied_exactly(tmp_path, monkeypatch, incremental):
    st = edit_state()
    repair = lambda: M.IncrementalProjectsOut(projects=[M.ProjectEdit(project=2, paragraph_sentences=[1])])
    st["ranked"][1]["paragraph"] = "x"          # forces a binding finding so the round runs
    ctx, d = loop(tmp_path, monkeypatch, incremental, repair, st)
    saved, _ = run_round(ctx, d, ["project 2 full description is not made of complete sentences of its own source card"])
    assert entry(saved)["paragraph"] == P.source_sentences(card())[0]
    assert P.binding_findings(saved, P.known_entries(TEMPLATE)) == []


def test_a_numbered_edit_is_applied_through_the_real_merge_with_title_and_owner_pinning():
    st = edit_state()
    pool = P.source_sentences(card(CLOUD))
    ids = [i for i, s in enumerate(pool, 1) if len(s) < 330][:2]
    refused = P.apply_edits(st, [M.ProjectEdit(project=1, paragraph_sentences=ids, title="CloudForge"),
                                 M.ProjectEdit(project=3, paragraph_sentences=[1])], P.known_entries(TEMPLATE))
    assert entry(st, CLOUD)["paragraph"] == " ".join(pool[i - 1] for i in ids) and not refused           # project 3 is hidden: ignored, not an error
    assert P.binding_findings(st, P.known_entries(TEMPLATE)) == []


def test_text_edits_are_accepted_only_when_they_copy_whole_source_sentences():
    st = edit_state()
    own = entry(st, CLOUD)["paragraph"]
    refused = P.apply_edits(st, [M.ProjectEdit(project=1, paragraph=own[:-1] + " and deploys it everywhere.")])
    assert refused and entry(st, CLOUD)["paragraph"] == own
    assert not P.apply_edits(st, [M.ProjectEdit(project=1, paragraph=own)])


def test_validator_rejects_unbound_text_and_bad_numbers_inside_the_bounded_correction_loop(tmp_path, monkeypatch):
    from pydantic import ValidationError
    st = edit_state()
    (tmp_path / "projects.json").write_text(json.dumps(st))
    monkeypatch.setattr(M.llm, "writer", lambda *a, **kw: object())
    monkeypatch.setattr(M, "_context", lambda *a, **kw: "Sources")
    seen = []

    def generate(ctx, stage, model, schema, messages, **kwargs):
        seen.append(messages)
        for bad in ({"project": 1, "paragraph": "A platform enabling real-time monitoring and control."}, {"project": 1, "paragraph_sentences": [99]},
                    {"project": 1, "paragraph_sentences": [1, 1]}, {"project": 7, "paragraph_sentences": [1]}):
            with pytest.raises(ValidationError):
                schema.model_validate({"projects": [bad]})
        return schema.model_validate({"projects": [{"project": 1, "paragraph_sentences": [1, 2]}]})
    monkeypatch.setattr(M, "_invoke", generate)
    agent, out = M._run_agent({"job_dir": tmp_path, "models": {"projects": "m"}, "log": lambda _: None, "r": SimpleNamespace(roles=[], tex=TEMPLATE)},
                              "projects", None, ["Repair project 1."], "")
    assert out is not None and out.projects[0].paragraph == " ".join(P.source_sentences(card(CLOUD))[:2])
    assert "SOURCE SENTENCES" in seen[0][1].content and "[1] (" in seen[0][1].content and "PROJECT 1" in seen[0][1].content


def test_a_swap_selects_the_candidates_own_sentences_and_saves_its_evidence(tmp_path):
    st = edit_state()
    (tmp_path / "projects.json").write_text(json.dumps(st))
    wics = {**card(VISION), "full_name": "ak-asu/another-project", "title": "Another Project: visual builder", "url": "https://github.com/ak-asu/another-project"}
    outs = {"projects": SimpleNamespace(projects=[], swaps=[SimpleNamespace(project=2, entry=P.ProjectPick(
        repo=wics["full_name"], title="Real-Time Control Platform", stack=["React", "Kubernetes"], paragraph_sentences=[1, 3], short_sentences=[]))])}
    ctx = {"job_dir": tmp_path, "r": SimpleNamespace(tex=TEMPLATE, roles=[]), "cards": {wics["full_name"].casefold(): wics}, "jd": JD, "log": lambda *_: None}
    M._merge(ctx, FILLED, outs)
    saved = json.loads((tmp_path / "projects.json").read_text())
    swapped = saved["ranked"][1]
    assert swapped["repo"] == wics["full_name"] and wics["full_name"].casefold() in saved["evidence"]
    assert P.bound_text(swapped["paragraph"], wics) == swapped["paragraph"] and swapped["title"] == P.card_title(wics) and "Kubernetes" not in swapped["stack"]
    assert P.binding_findings(saved, P.known_entries(TEMPLATE)) == []


def test_a_swap_to_a_card_without_usable_sentences_changes_nothing(tmp_path):
    st = edit_state()
    (tmp_path / "projects.json").write_text(json.dumps(st))
    thin = {"full_name": "o/thin", "url": "u", "title": "Thin", "summary": "Tiny.", "results": [], "tech_stack": ["Python"]}
    outs = {"projects": SimpleNamespace(projects=[], swaps=[SimpleNamespace(project=2, entry=P.ProjectPick(repo="o/thin", title="Thin", stack=["Python"], paragraph_sentences=[1]))])}
    M._merge({"job_dir": tmp_path, "r": SimpleNamespace(tex=TEMPLATE, roles=[]), "cards": {"o/thin": thin}, "jd": JD, "log": lambda *_: None}, FILLED, outs)
    assert json.loads((tmp_path / "projects.json").read_text())["ranked"] == st["ranked"]


# --- ATS and the shared acceptance points ---------------------------------------------------------------------------------------------

def test_the_ats_keyword_pass_cannot_reintroduce_arbitrary_prose():
    st = edit_state()
    before = json.dumps(st, indent=1)
    prose = T.ProjectFix(project=2, paragraph=REJECTED)
    assert A.apply_project_fixes(before, [prose], P.known_entries(TEMPLATE)) == before            # refused: the exact state text comes back
    own = T.ProjectFix(project=1, paragraph=entry(st, CLOUD)["paragraph"])
    assert A.apply_project_fixes(before, [own], P.known_entries(TEMPLATE)) == before             # an identical copy changes nothing either
    assert "projects" in T.KeywordPass.model_fields and "leave `projects` empty" in T.KEYWORD_PROMPT


def test_hard_issues_and_check_layout_both_enforce_the_binding(tmp_path, monkeypatch):
    from jobagent.state import JobDescription as JDesc
    st = packet()
    d = tmp_path / "job"
    d.mkdir()
    (d / "projects.json").write_text(json.dumps(st))
    (d / "resume.pdf").write_bytes(b"%PDF")
    monkeypatch.setattr(M.layout, "check", lambda _: M.layout.Layout(pages=1, free_pt=0))
    ctx = {"job_dir": d, "r": SimpleNamespace(tex=TEMPLATE, roles=[]), "log": lambda *_: None}
    assert any(h.startswith("project 2 ") and "source card" in h for h in M.hard_issues(ctx, FILLED))
    # the validator routes the same finding to the projects agent, whose repair loop then runs
    f = T.Filled.model_construct(summary="Engineer building Python services with reliable deployment.", roles=[], skills=[T.SkillLine(category="Languages", terms=["Python"])] * 3, notes="")
    monkeypatch.setattr(M, "render", lambda _: (f, "", [], 1, ""))
    monkeypatch.setattr(M.layout, "review", lambda *a, **kw: [])
    monkeypatch.setattr(M, "_reviewed_content", lambda *a: {"content_findings": [], "content_error": ""})
    monkeypatch.setattr(M, "summary_issues", lambda *a: [])
    checked = M.check_layout({"ctx": {"r": SimpleNamespace(roles=[], skills=[], tex=TEMPLATE), "jd": JDesc(title="Engineer", company="Acme"), "job_dir": d, "log": lambda _: None}, "f": f})
    assert any(t.startswith("project 2 ") and "source card" in t for t in checked["tasks"]["projects"])
    assert any(h.startswith("project 2 ") for h in checked["hard"]) and M.after_layout(checked) == "fix"


def test_the_acceptance_gate_reaches_a_bound_packet_only_after_recovery_in_the_real_loop(tmp_path, monkeypatch):
    st = packet()
    ctx, d = loop(tmp_path, monkeypatch, True, lambda: None, st)
    saved, _ = run_round(ctx, d, P.binding_findings(st, P.known_entries(TEMPLATE)))
    ctx2 = {"job_dir": d, "r": SimpleNamespace(tex=TEMPLATE, roles=[]), "log": lambda *_: None}
    monkeypatch.setattr(M.layout, "check", lambda _: M.layout.Layout(pages=1, free_pt=0))
    (d / "resume.pdf").write_bytes(b"%PDF")
    assert not [h for h in M.hard_issues(ctx2, FILLED) if "source card" in h or "template title" in h or "date" in h]


def test_cached_project_stages_are_invalidated_when_the_contract_code_changes(tmp_path, monkeypatch):
    from jobagent.tools import job_context
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "job_context.py").write_text("x")
    monkeypatch.setattr(job_context, "__file__", str(tools / "job_context.py"))
    (tools / "projects.py").write_text("v1")
    before = {stage: job_context.model_version(stage) for stage in ("projects", "project-selection", None)}
    (tools / "projects.py").write_text("v2")
    assert all(job_context.model_version(stage) != before[stage] for stage in before)


def test_rollback_restores_the_bound_version_with_its_evidence(tmp_path, monkeypatch):
    good = bound(packet())
    d = tmp_path / "job"
    d.mkdir()
    for name, content in (("projects.json", json.dumps(good)), ("resume.tex", "x"), ("filled.json", FILLED.model_dump_json())):
        (d / name).write_text(content)
    (d / "resume.pdf").write_bytes(b"%PDF-1.4 " + b"x" * 300)
    monkeypatch.setattr(M.layout, "check", lambda _: M.layout.Layout(pages=1, free_pt=0))
    best = M._snapshot({"job_dir": d}, FILLED, [])
    (d / "projects.json").write_text(json.dumps(packet()))                              # a later edit round left unbound prose in the files
    M._rollback({"ctx": {"job_dir": d}, "best": best}, "undone")
    restored = json.loads((d / "projects.json").read_text())
    assert restored == good and P.binding_findings(restored, P.known_entries(TEMPLATE)) == [] and P.saved_evidence(restored)


def test_no_module_other_than_projects_writes_project_prose():
    import re
    root = Path(P.__file__).parents[1]
    pattern = re.compile(r"""\[['"](?:paragraph|short)['"]\]\s*=(?!=)|\.paragraph\s*=(?!=)""")      # (`proj.short = frozenset(...)` is the set of repos shown short, not prose)
    offenders = []
    for path in root.rglob("*.py"):
        if path.name in ("projects.py", "cover_letter.py", "letter_attribution.py"):
            continue
        offenders += [f"{path.name}:{n}" for n, line in enumerate(path.read_text().splitlines(), 1) if pattern.search(line)]
    assert offenders == []


def test_choose_selects_a_short_variant_only_from_whole_sentences_within_its_bounds(monkeypatch):
    short_ids = [i for i, s in enumerate(P.source_sentences(card(VISION)), 1) if P.SHORT_FLOOR <= len(s) <= P.SHORT_LIMIT][:1]
    assert short_ids
    chosen, _ = choose_with(monkeypatch, [[sel(VISION, [1, 3], short=short_ids), sel(DEVIL, [1], short=[1])]])
    by = {e.repo.casefold(): e for e in chosen.entries}
    assert by[VISION].short == P.source_sentences(card(VISION))[short_ids[0] - 1]
    assert by[DEVIL].short == ""                                                         # its only sentence is over the short bound: skipped, not cut
