import json
import math
import random
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from jobagent import db
from jobagent.tools import learning as L
from jobagent.tools import outcome_stats as S

T0 = datetime(2026, 1, 1, 12, tzinfo=UTC)


def simulate(n, seed, slope_a=0.0, kw_odds=1.0, role="swe", base=0.18, flip_after=None):
    """n submitted applications. Planted effects: site A's score (per SD) and the skill 'langgraph' change the odds of a
    positive reply; site B's score and the eight 'skillN' skills are noise. Most applications never get a reply."""
    rng = random.Random(seed)
    apps = []
    for i in range(n):
        sub = rng.uniform(0, 150)
        a_score, b_score = rng.gauss(70, 8), rng.gauss(70, 8)
        kws = {f"skill{j}" for j in range(8) if rng.random() < 0.5} | ({"langgraph"} if rng.random() < 0.5 else set())
        kw_effect = math.log(kw_odds) * (-1 if flip_after is not None and sub > flip_after else 1)   # the effect can reverse over time
        logit = math.log(base / (1 - base)) + slope_a * (a_score - 70) / 8 + (kw_effect if "langgraph" in kws else 0.0)
        events = [("confirmation", sub + 0.1)]
        if rng.random() < 1 / (1 + math.exp(-logit)):
            events.append((rng.choice(["interview", "assessment", "recruiter_screen"]), sub + rng.lognormvariate(math.log(9), 0.6)))
        elif rng.random() < 0.3:
            events.append(("rejection", sub + rng.lognormvariate(math.log(14), 0.5)))
        apps.append(S.AppEvents(i, T0 + timedelta(days=sub), [(s, T0 + timedelta(days=t)) for s, t in events],
                                rng.choice(["ashby", "workday"]), role, "", None, {"siteA": a_score, "siteB": b_score},
                                frozenset(kws), company=f"company{i}"))
    return apps


AS_OF = T0 + timedelta(days=260)    # every application is resolved: replied, rejected or past the cutoff


def run(apps):
    return apps, S.statuses(apps, AS_OF)


# --- the statistics ------------------------------------------------------------------------------------------------

def test_auc_ties_and_direction():
    assert L.auc([3, 4, 5], [1, 2]) == 1.0 and L.auc([1, 2], [3, 4, 5]) == 0.0
    assert L.auc([1, 2], [1, 2]) == 0.5 and L.auc([], [1]) == 0.5
    assert L.auc([2, 3], [1, 2]) == pytest.approx(0.875)


def test_ghosted_applications_count_as_half_a_negative_in_every_statistic():
    # one positive (score 2), a full negative (score 1) and a ghosted negative (score 3, weight 0.5)
    a, _ = L._auc_stat([(2.0, 1, 1.0), (1.0, 0, 1.0), (3.0, 0, 0.5)])
    assert a == pytest.approx(1 / 1.5)                              # not 1/2: the ghost is half as heavy
    assert L._auc_stat([(2.0, 1, 1.0), (1.0, 0, 1.0), (3.0, 0, 1.0)])[0] == pytest.approx(0.5)
    p1, p0, _ = L._rate_stat([(1, 1.0), (0, 0.5)], [(0, 1.0)])
    assert p1 == pytest.approx(1 / 1.5) and p0 == 0.0               # a ghost lowers a positive share half as much
    apps = [S.AppEvents(1, T0, [("confirmation", T0)], "x", "swe"), S.AppEvents(2, T0, [("rejection", T0)], "x", "swe")]
    sts = S.statuses(apps, T0 + timedelta(days=90))
    assert {r.app.id: r.w for r in L.resolved_rows(apps, sts)} == {1: 0.5, 2: 1.0}      # ghosted 0.5, rejected 1


def test_nothing_is_read_below_the_floors():
    apps, sts = run(simulate(40, 1, slope_a=2.0, kw_odds=4.0))
    assert all(e.verdict == "not enough data" for e in L.site_effects(apps, sts))
    assert L.keyword_effects(apps, sts, "swe") == [] and L.findings(apps, sts) == [] or all(
        not f["actionable"] for f in L.findings(apps, sts))


def test_pending_and_withdrawn_applications_are_left_out():
    apps = simulate(60, 2)
    young = S.AppEvents(999, AS_OF - timedelta(days=3), [("confirmation", AS_OF - timedelta(days=3))], "ashby", "swe",
                        ats_scores={"siteA": 99.0}, keywords=frozenset({"x"}))
    out = L.resolved_outcomes(apps + [young], S.statuses(apps + [young], AS_OF))
    assert 999 not in {a.id for a, _ in out}


# --- simulated worlds ----------------------------------------------------------------------------------------------

def test_planted_effects_are_recovered_and_noise_is_not():
    """Power at 900 resolved applications over 6 simulated worlds (seeds 100-105, not chosen)."""
    site_hits = kw_hits = noise_flags = confirmed = 0
    for seed in range(100, 106):
        apps, sts = run(simulate(900, seed, slope_a=1.0, kw_odds=3.0))
        an = L.analyze(apps, sts, perms=99, looks=None)
        sites = {e.site: e for e in an.sites}
        site_hits += sites["siteA"].verdict == "predicts replies"
        kws = {k.keyword: k for k in an.keywords}
        kw_hits += kws["langgraph"].verdict == "more replies"
        confirmed += kws["langgraph"].holdout == "confirmed" and sites["siteA"].holdout == "confirmed"
        noise_flags += sum(1 for k, v in kws.items() if k.startswith("skill") and v.verdict != "no clear relationship")
        noise_flags += sites["siteB"].verdict != "no clear relationship"
    assert site_hits >= 5 and kw_hits >= 5, (site_hits, kw_hits)
    assert confirmed >= 4, confirmed                                   # the later applications agree with the earlier ones
    assert noise_flags <= 1, noise_flags                               # skills and a site with no effect are almost never flagged


def test_the_whole_recommendation_family_stays_within_five_percent_in_a_world_with_no_effects():
    """The complete procedure (every site and every skill, together) is run on effect-free worlds; the share of worlds in
    which ANYTHING clears the family-wise test must be about 5% (Monte-Carlo slack: 50 worlds, at most 6 alarms)."""
    flagged = actionable = 0
    for seed in range(50):
        apps, sts = run(simulate(300, 5000 + seed))
        an = L.analyze(apps, sts, perms=99, looks=None)
        flagged += any(s.verdict in ("predicts replies", "inverse") for s in an.sites) or any(
            k.verdict != "no clear relationship" for k in an.keywords)
        actionable += any(f["actionable"] for f in L.findings(apps, sts, an))
    assert flagged <= 6, flagged
    assert actionable <= flagged


def test_applications_to_one_company_are_not_counted_as_independent_evidence():
    """Every company's resumes share a skills list and the company decides for all of them: a skill that only tracks which
    companies are friendly must not be recommended (with each application shuffled alone it would be, again and again)."""
    flagged = 0
    for seed in range(100):
        rng = random.Random(9000 + seed)
        apps, i = [], 0
        for c in range(70):
            friendly = rng.random() < 0.35
            style = {"langgraph"} if rng.random() < 0.5 else set()      # this company's resumes all list the same skills
            for _ in range(4):
                sub = rng.uniform(0, 150)
                events = [("confirmation", sub + 0.1)] + ([("interview", sub + 9)] if rng.random() < (0.5 if friendly else 0.05) else [])
                apps.append(S.AppEvents(i, T0 + timedelta(days=sub), [(s, T0 + timedelta(days=t)) for s, t in events],
                                        "ashby", "swe", "", None, {"siteA": rng.gauss(70, 8)}, frozenset(style), company=f"co{c}"))
                i += 1
        apps_, sts = run(apps)
        an = L.analyze(apps_, sts, perms=99, looks=None)
        flagged += any(k.verdict != "no clear relationship" for k in an.keywords)
    # Measured over 200 such worlds: 6 alarms (3%) with companies shuffled together, 19 (9.5%) shuffling applications one by one.
    assert flagged <= 11, flagged                                      # the nominal rate is 5% of 100 worlds


def test_an_effect_that_reverses_in_the_latest_applications_is_not_a_recommendation():
    apps, sts = run(simulate(1200, 31, kw_odds=3.5, flip_after=105))   # strong early on, the opposite after day 105
    an = L.analyze(apps, sts, perms=99, looks=None)
    kw = {k.keyword: k for k in an.keywords}["langgraph"]
    assert kw.verdict == "more replies" and kw.holdout == "contradicted"
    f = {x["key"]: x for x in L.findings(apps, sts, an)}["keyword:langgraph@swe"]
    assert f["actionable"] is False and "CONTRADICTED" in f["text"] and "Association, not proof" in f["text"]


def test_skills_of_a_resume_that_cannot_be_tied_to_the_submission_are_not_compared():
    apps = simulate(600, 4, kw_odds=3.0)
    for a in apps:
        a.snapshot_status = "mismatch"                                   # F06: the skills on disk are not what was sent
    sts = S.statuses(apps, AS_OF)
    assert L.analyze(apps, sts, perms=19, looks=None).keywords == []


# --- lessons and the switches --------------------------------------------------------------------------------------

def test_reflect_writes_keeps_and_retires_lessons(tmp_path):
    conn = db.connect(tmp_path / "jobs.sqlite")
    apps, sts = run(simulate(900, 100, slope_a=1.0, kw_odds=3.0))
    an = L.analyze(apps, sts, perms=99, looks=None)
    first = L.reflect(conn, apps, sts, AS_OF, an)
    assert first["actionable"] >= 2 and first["new"] >= first["actionable"] and first["retired"] == 0
    rows = conn.execute("SELECT key, scope, status, evidence FROM lessons").fetchall()
    assert any(r["key"] == "site:siteA" for r in rows) and any(r["key"] == "keyword:langgraph@swe" for r in rows)
    assert "applications" in rows[0]["evidence"]                      # every lesson names the applications it rests on
    again = L.reflect(conn, apps, sts, AS_OF, an)
    assert again["new"] == 0 and again["kept"] == first["new"]
    noisy, nsts = run(simulate(900, 8))                               # the data no longer supports them
    third = L.reflect(conn, noisy, nsts, AS_OF, L.analyze(noisy, nsts, perms=99, looks=None))
    assert third["retired"] >= 1 and conn.execute("SELECT COUNT(*) FROM lessons WHERE status = 'retired'").fetchone()[0] >= 1


def test_a_lesson_is_versioned_when_its_evidence_changes_even_if_the_wording_does_not(tmp_path):
    conn = db.connect(tmp_path / "jobs.sqlite")
    apps, sts = run(simulate(900, 100, slope_a=1.0, kw_odds=3.0))
    an = L.analyze(apps, sts, perms=99, looks=None)
    first = L.reflect(conn, apps, sts, AS_OF, an)
    before = conn.execute("SELECT text, snapshot FROM lessons WHERE status = 'active' ORDER BY key").fetchall()
    # a noise score of one application and one of its skills change; no sentence in any lesson can change
    apps[0].ats_scores["siteB"] = (apps[0].ats_scores["siteB"] or 0) + 0.5
    apps[1].keywords = apps[1].keywords | {"brand-new-skill"}
    second = L.reflect(conn, apps, sts, AS_OF, an)
    after = conn.execute("SELECT text, snapshot FROM lessons WHERE status = 'active' ORDER BY key").fetchall()
    assert [r["text"] for r in before] == [r["text"] for r in after]       # same prose
    assert {r["snapshot"] for r in after}.isdisjoint({r["snapshot"] for r in before})     # a new version of the evidence
    assert second["new"] == first["new"] and second["retired"] == 0 and second["kept"] == 0
    assert conn.execute("SELECT COUNT(*) FROM lessons WHERE status = 'retired'").fetchone()[0] == first["new"]


def test_the_snapshot_covers_scores_keywords_and_the_analysis_parameters():
    apps, sts = run(simulate(200, 3))
    rows = L.resolved_rows(apps, sts)
    base = L._snapshot(rows, {"perms": 99})
    assert L._snapshot(rows, {"perms": 199}) != base
    rows[0].app.ats_scores["siteA"] += 1
    assert L._snapshot(rows, {"perms": 99}) != base


def test_the_switches_are_off_by_default_and_each_controls_one_lever(tmp_path):
    conn = db.connect(tmp_path / "jobs.sqlite")
    apps, sts = run(simulate(900, 100, slope_a=1.0, kw_odds=3.0))
    L.reflect(conn, apps, sts, AS_OF, L.analyze(apps, sts, perms=99, looks=None))
    off = SimpleNamespace(learning_use_site_mix=False, learning_use_tailoring_hints=False, learning_use_lessons=False)
    assert L.prompt_hints(conn, "swe", off) == ""
    assert L.site_mix(("siteA", "siteB"), conn, apps, sts, off) == ("siteA", "siteB")
    hints = L.prompt_hints(conn, "swe", SimpleNamespace(**{**off.__dict__, "learning_use_tailoring_hints": True}))
    assert "langgraph" in hints and "list a skill only if" in hints and "siteA" not in hints
    lessons = L.prompt_hints(conn, "swe", SimpleNamespace(**{**off.__dict__, "learning_use_lessons": True}))
    assert "langgraph" in lessons
    assert L.prompt_hints(conn, "ml", SimpleNamespace(**{**off.__dict__, "learning_use_lessons": True})) == ""  # another role family
    from jobagent.config import Settings
    s = Settings()
    assert not (s.learning_use_site_mix or s.learning_use_tailoring_hints or s.learning_use_lessons)


def test_the_site_mix_switch_is_reported_as_unsupported_and_never_removes_a_required_scorer(tmp_path):
    conn = db.connect(tmp_path / "jobs.sqlite")
    on = SimpleNamespace(learning_use_site_mix=True, learning_use_tailoring_hints=False, learning_use_lessons=False)
    off = SimpleNamespace(learning_use_site_mix=False, learning_use_tailoring_hints=False, learning_use_lessons=False)
    sites = ("autoapplymax", "hireflow", "jobscan", "atsalign", "atsfriendly")
    assert L.site_mix(sites, conn, settings=on) == sites                      # nothing is dropped, whatever the history says
    assert L.site_mix_unsupported(off) == ""
    msg = L.site_mix_unsupported(on)
    assert "not supported" in msg and "no effect" in msg


def test_the_tailor_prompt_gets_a_hint_only_when_a_switch_is_on(tmp_path, monkeypatch):
    from jobagent.config import get_settings
    from jobagent.tools import tailor as T
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    get_settings.cache_clear()
    conn = db.connect()
    conn.execute("INSERT INTO jobs (url, status, role_id) VALUES ('https://acme.com/j/1', 'scraped', 'swe')")
    conn.execute("INSERT INTO lessons (key, scope, text, evidence, snapshot) VALUES ('keyword:langgraph@swe', 'swe', "
                 "'In swe applications, resumes listing ''langgraph'' went with more replies (x).', '{}', 'abc')")
    conn.commit()
    jd = SimpleNamespace(source_url="https://acme.com/j/1")
    assert T.learning_hint(jd) == ""                                       # both switches off: nothing, no database read
    monkeypatch.setenv("LEARNING_USE_TAILORING_HINTS", "true")
    get_settings.cache_clear()
    assert "langgraph" in T.learning_hint(jd) and "only if the candidate really has it" in T.learning_hint(jd)
    assert T.learning_hint(SimpleNamespace(source_url="https://other.com/x")) == ""   # another role family: no lesson
    get_settings.cache_clear()


# --- F23: the deployed fixed-look protocol ------------------------------------------------------------------------------------

def test_nothing_is_analysed_before_the_first_declared_look_and_each_look_uses_its_own_cohort():
    apps, sts = run(simulate(60, 3, slope_a=2.0, kw_odds=5.0))
    an = L.analyze(apps, sts, perms=19)
    assert an.params["look"] == 0 and an.params["next_look"] == L.LOOKS[0] and an.sites == [] and an.keywords == []
    assert L.findings(apps, sts, an) == []
    apps, sts = run(simulate(250, 4))
    an = L.analyze(apps, sts, perms=19)
    assert an.params["look"] == 2 and an.params["train"] + an.params["holdout"] == L.LOOKS[1]        # exactly the first 200
    assert an.params["fwer"] == pytest.approx(L.FWER / len(L.LOOKS)) and an.params["next_look"] == L.LOOKS[2]


def test_the_whole_deployed_policy_over_repeated_looks_stays_within_five_percent_in_null_streams():
    """A null stream of 800 applications is analysed at EVERY declared look up to it (100, 200, 400, 800), as `reflect` would
    do over time; the share of streams in which ANY look recommends anything must stay near or under 5%."""
    flagged = 0
    streams = 40
    for seed in range(streams):
        apps = simulate(800, 7000 + seed)
        any_alarm = False
        for look in L.LOOKS[:4]:
            sub = apps[:look]                       # the first `look` applications, resolved by the end of the stream
            sts = S.statuses(sub, AS_OF)
            an = L.analyze(sub, sts, perms=99)
            assert an.params["look"] >= 1
            any_alarm |= any(s.verdict in ("predicts replies", "inverse") for s in an.sites) or any(
                k.verdict != "no clear relationship" for k in an.keywords)
        flagged += any_alarm
    assert flagged <= 5, flagged                    # nominal 5% of 40 streams = 2; P(6 or more | 5%) is about 1%


def test_planted_effects_are_still_found_under_the_deployed_protocol_once_there_is_enough_data():
    hits = 0
    for seed in range(300, 304):
        apps, sts = run(simulate(1500, seed, slope_a=1.0, kw_odds=3.0))
        an = L.analyze(apps, sts, perms=99)
        assert an.params["look"] == 4
        hits += {e.site: e for e in an.sites}["siteA"].verdict == "predicts replies"
    assert hits >= 3, hits


# --- F23: a consumed look is frozen -------------------------------------------------------------------------------------------

def resolved_count(apps, as_of_days):
    sts = S.statuses(apps, T0 + timedelta(days=as_of_days))
    return len(L.resolved_rows(apps, sts)), sts


def steps(apps, days=range(10, 270, 10)):
    for d in days:
        n, sts = resolved_count(apps, d)
        yield d, n, sts


def test_a_look_is_frozen_when_first_reached_and_later_data_cannot_change_it(tmp_path):
    conn = db.connect(tmp_path / "jobs.sqlite")
    apps = simulate(300, 21)
    first = None
    for d, n, sts in steps(apps):
        if first is None and n >= 100:
            first = (d, n, L.analyze_frozen(conn, apps, sts, perms=99))
        elif first is not None and n < 200:                     # many calls between the thresholds, on changing data
            again = L.analyze_frozen(conn, apps, sts, perms=99)
            assert again.params["cohort_sha"] == first[2].params["cohort_sha"]
            assert [(s.site, s.verdict, s.p_adj) for s in again.sites] == [(s.site, s.verdict, s.p_adj) for s in first[2].sites]
            assert [(k.keyword, k.verdict, k.p_adj) for k in again.keywords] == [(k.keyword, k.verdict, k.p_adj) for k in first[2].keywords]
    assert first is not None
    assert conn.execute("SELECT COUNT(*) FROM learning_looks").fetchone()[0] == 1       # one look consumed, however often it was asked
    row = conn.execute("SELECT * FROM learning_looks").fetchone()
    cohort = json.loads(row["cohort"])
    assert len(cohort) == 100 and row["n"] == 100 and all(len(c) == 3 for c in cohort)  # the data it was decided on is kept


def test_an_older_application_resolving_later_does_not_change_a_consumed_look(tmp_path):
    conn = db.connect(tmp_path / "jobs.sqlite")
    apps = simulate(300, 22)
    seen = {}
    for d, n, sts in steps(apps):
        if n >= 100:
            an = L.analyze_frozen(conn, apps, sts, perms=49)
            seen.setdefault(an.params["look"], set()).add(an.params["cohort_sha"])
    assert all(len(shas) == 1 for shas in seen.values()), seen                          # each look: one cohort, whatever resolved since
    assert set(seen) >= {1}
    rows = conn.execute("SELECT look FROM learning_looks ORDER BY look").fetchall()
    assert [r["look"] for r in rows] == sorted(seen)                                      # and each consumed look is stored once


def test_the_repeated_use_policy_with_progressive_resolution_stays_within_five_percent_in_null_streams(tmp_path):
    """The DEPLOYED procedure end to end: effect-free streams whose applications resolve progressively (resolution order differs
    from submission order, ghosted ones turn positive late), asked for the analysis every 10 days; looks are consumed on first reach
    and replayed. The share of streams in which ANY consumed look recommends anything must stay near or under 5%."""
    flagged, streams = 0, 30
    for seed in range(streams):
        conn = db.connect(tmp_path / f"s{seed}.sqlite")
        apps = simulate(900, 8000 + seed)
        for d, n, sts in steps(apps):
            L.analyze_frozen(conn, apps, sts, perms=99)
        verdicts = [json.loads(r["result"]) for r in conn.execute("SELECT result FROM learning_looks")]
        flagged += any(s["verdict"] in ("predicts replies", "inverse") for v in verdicts for s in v["sites"]) or any(
            k["verdict"] != "no clear relationship" for v in verdicts for k in v["keywords"])
        conn.close()
    assert flagged <= 4, flagged                          # nominal 5% of 30 streams = 1.5; P(5 or more | 5%) is about 1.6%
