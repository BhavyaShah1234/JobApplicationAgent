import math
import random
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from jobagent import db
from jobagent.tools import learning as L
from jobagent.tools import outcome_stats as S

T0 = datetime(2026, 1, 1, 12, tzinfo=UTC)


def simulate(n, seed, slope_a=0.0, kw_odds=1.0, role="swe", base=0.18):
    """n submitted applications. Planted effects: site A's score (per SD) and the skill 'langgraph' change the odds of a
    positive reply; site B's score and the eight 'skillN' skills are noise. Most applications never get a reply."""
    rng = random.Random(seed)
    apps = []
    for i in range(n):
        sub = rng.uniform(0, 150)
        a_score, b_score = rng.gauss(70, 8), rng.gauss(70, 8)
        kws = {f"skill{j}" for j in range(8) if rng.random() < 0.5} | ({"langgraph"} if rng.random() < 0.5 else set())
        logit = math.log(base / (1 - base)) + slope_a * (a_score - 70) / 8 + (math.log(kw_odds) if "langgraph" in kws else 0.0)
        events = [("confirmation", sub + 0.1)]
        if rng.random() < 1 / (1 + math.exp(-logit)):
            events.append((rng.choice(["interview", "assessment", "recruiter_screen"]), sub + rng.lognormvariate(math.log(9), 0.6)))
        elif rng.random() < 0.3:
            events.append(("rejection", sub + rng.lognormvariate(math.log(14), 0.5)))
        apps.append(S.AppEvents(i, T0 + timedelta(days=sub), [(s, T0 + timedelta(days=t)) for s, t in events],
                                rng.choice(["ashby", "workday"]), role, "", None, {"siteA": a_score, "siteB": b_score},
                                frozenset(kws)))
    return apps


AS_OF = T0 + timedelta(days=260)    # every application is resolved: replied, rejected or past the cutoff


def run(apps):
    return apps, S.statuses(apps, AS_OF)


# --- the statistics ------------------------------------------------------------------------------------------------

def test_auc_ties_and_direction():
    assert L.auc([3, 4, 5], [1, 2]) == 1.0 and L.auc([1, 2], [3, 4, 5]) == 0.0
    assert L.auc([1, 2], [1, 2]) == 0.5 and L.auc([], [1]) == 0.5
    assert L.auc([2, 3], [1, 2]) == pytest.approx(0.875)


def test_fisher_exact_and_benjamini_hochberg():
    assert L.fisher_exact(3, 1, 1, 3) == pytest.approx(0.4857, abs=1e-3)
    assert L.fisher_exact(10, 0, 0, 10) < 1e-4 and L.fisher_exact(5, 5, 5, 5) == pytest.approx(1.0)
    assert [round(x, 3) for x in L.bh_adjust([0.01, 0.04, 0.03, 0.005])] == [0.02, 0.04, 0.04, 0.02]


def test_nothing_is_read_below_the_floors():
    apps, sts = run(simulate(40, 1, slope_a=2.0, kw_odds=4.0))
    assert all(e.verdict == "not enough data" for e in L.site_effects(apps, sts))
    assert L.keyword_effects(apps, sts, "swe") == [] and L.findings(apps, sts) == []


def test_pending_and_withdrawn_applications_are_left_out():
    apps = simulate(60, 2)
    young = S.AppEvents(999, AS_OF - timedelta(days=3), [("confirmation", AS_OF - timedelta(days=3))], "ashby", "swe",
                        ats_scores={"siteA": 99.0}, keywords=frozenset({"x"}))
    out = L.resolved_outcomes(apps + [young], S.statuses(apps + [young], AS_OF))
    assert 999 not in {a.id for a, _ in out}


# --- simulated worlds ----------------------------------------------------------------------------------------------

def test_planted_effects_are_recovered_and_noise_is_not():
    """Power at 800 resolved applications over 10 simulated worlds (seeds not chosen: 100-109)."""
    site_hits = kw_hits = noise_flags = 0
    for seed in range(100, 110):
        apps, sts = run(simulate(800, seed, slope_a=0.9, kw_odds=2.5))
        sites = {e.site: e for e in L.site_effects(apps, sts)}
        site_hits += sites["siteA"].verdict == "predicts replies"
        kws = {k.keyword: k for k in L.keyword_effects(apps, sts, "swe")}
        kw_hits += kws["langgraph"].verdict == "more replies"
        noise_flags += sum(1 for k, v in kws.items() if k.startswith("skill") and v.verdict != "no clear relationship")
        noise_flags += sites["siteB"].verdict != "no clear relationship"
    assert site_hits >= 9 and kw_hits >= 8, (site_hits, kw_hits)       # at least 80% power; the effect is odds x2.5 (x2.2 net)
    assert noise_flags <= 2, noise_flags                               # skills and a site with no effect are almost never flagged


def test_a_world_with_no_effects_almost_never_produces_a_recommendation():
    false_alarms = 0
    for seed in range(30):
        apps, sts = run(simulate(400, 1000 + seed))
        false_alarms += any(f["actionable"] for f in L.findings(apps, sts))
    assert false_alarms <= 3, false_alarms          # at most 10% of null worlds


# --- lessons and the switches --------------------------------------------------------------------------------------

def test_reflect_writes_keeps_and_retires_lessons(tmp_path):
    conn = db.connect(tmp_path / "jobs.sqlite")
    apps, sts = run(simulate(500, 7, slope_a=0.9, kw_odds=2.5))
    first = L.reflect(conn, apps, sts, AS_OF)
    assert first["found"] >= 2 and first["new"] == first["found"] and first["retired"] == 0
    rows = conn.execute("SELECT key, scope, status, evidence FROM lessons").fetchall()
    assert any(r["key"] == "site:siteA" for r in rows) and any(r["key"] == "keyword:langgraph@swe" for r in rows)
    assert "applications" in rows[0]["evidence"]                      # every lesson names the applications it rests on
    again = L.reflect(conn, apps, sts, AS_OF)
    assert again["new"] == 0 and again["kept"] == first["found"]
    noisy, nsts = run(simulate(500, 8))                               # the data no longer supports them
    third = L.reflect(conn, noisy, nsts, AS_OF)
    assert third["retired"] >= 1 and conn.execute("SELECT COUNT(*) FROM lessons WHERE status = 'retired'").fetchone()[0] >= 1


def test_the_switches_are_off_by_default_and_each_controls_one_lever(tmp_path):
    conn = db.connect(tmp_path / "jobs.sqlite")
    apps, sts = run(simulate(500, 7, slope_a=0.9, kw_odds=2.5))
    L.reflect(conn, apps, sts, AS_OF)
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


def test_site_mix_drops_only_a_site_shown_to_carry_no_signal_and_never_the_last(tmp_path):
    conn = db.connect(tmp_path / "jobs.sqlite")
    on = SimpleNamespace(learning_use_site_mix=True, learning_use_tailoring_hints=False, learning_use_lessons=False)
    apps, sts = run(simulate(3000, 11, slope_a=0.9))                 # a great deal of data: B's interval is tight around 0.5
    mix = L.site_mix(("siteA", "siteB", "siteC"), conn, apps, sts, on)
    assert "siteA" in mix and "siteB" not in mix and "siteC" in mix  # C has no data: unknown, kept
    small, ssts = run(simulate(120, 12, slope_a=0.9))
    assert L.site_mix(("siteA", "siteB"), conn, small, ssts, on) == ("siteA", "siteB")   # too little to drop anything
    assert L.site_mix(("siteB",), conn, apps, sts, on) == ("siteB",)                      # never the last site


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
