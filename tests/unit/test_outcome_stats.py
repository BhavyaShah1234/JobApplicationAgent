import json
from datetime import UTC, datetime, timedelta

import pytest

from jobagent import db
from jobagent.tools import outcome_stats as S

T0 = datetime(2026, 9, 1, 12, tzinfo=UTC)


def app(i=1, events=(), portal="greenhouse", **kw):
    return S.AppEvents(i, T0, [(s, T0 + timedelta(days=d)) for s, d in events], portal, **kw)


def label(a, day, cutoff=45):
    return S.status_of(a, T0 + timedelta(days=day), cutoff)


def test_silence_is_pending_until_the_cutoff_and_ghosted_after():
    a = app(events=[("confirmation", 0)])
    assert label(a, 10).label == "pending" and label(a, 45).label == "pending"
    g = label(a, 46)
    assert (g.label, g.weight) == ("ghosted", 0.5)


def test_a_positive_stage_wins_even_if_a_rejection_follows():
    a = app(events=[("confirmation", 0), ("interview", 9), ("rejection", 20)])
    st = label(a, 30)
    assert (st.label, st.furthest, round(st.first_response_days)) == ("positive", "interview", 9)
    assert label(app(events=[("confirmation", 0), ("rejection", 12)]), 30).label == "negative"
    assert label(app(events=[("confirmation", 1), ("withdrawn", 3)]), 30).label == "withdrawn"


def test_a_late_reply_reopens_a_ghosted_application_and_replays_by_date():
    a = app(events=[("confirmation", 0), ("interview", 60)])
    assert label(a, 55).label == "ghosted"          # as of day 55 the reply hasn't come yet
    assert label(a, 61).label == "positive"         # it has by day 61: the label is rebuilt from the events
    assert label(a, 61).first_response_days == pytest.approx(60)


def test_the_automated_confirmation_is_not_a_response():
    assert S.first_response(app(events=[("confirmation", 1)])) is None
    assert S.first_response(app(events=[("confirmation", 1), ("assessment", 4), ("rejection", 9)]))[1] == "assessment"


def test_kaplan_meier_matches_a_hand_computed_example():
    # responded at 5, 12, 20 days; still waiting (censored) at 10 and 30
    curve = S.km_curve([(5, True), (10, False), (12, True), (20, True), (30, False)])
    assert [(t, round(s, 4)) for t, s in curve] == [(5, 0.8), (12, 0.5333), (20, 0.2667)]
    assert S.survival_at(curve, 4) == 1.0 and S.survival_at(curve, 13) == pytest.approx(0.5333, abs=1e-3)
    assert S.km_median(curve) == 20
    assert S.km_median(S.km_curve([(5, True), (40, False), (40, False), (40, False)])) is None   # fewer than half respond


def test_a_thirty_day_figure_needs_enough_applications_old_enough_to_tell():
    as_of = T0 + timedelta(days=60)
    old_silent = [app(i, [("confirmation", 0)]) for i in range(100, 115)]                  # 15 applications, 60 days old
    some_responded = [app(i, [("confirmation", 0), ("interview", 10)]) for i in range(5)]
    g = S.group_by(some_responded + old_silent, S.statuses(some_responded + old_silent, as_of), lambda a: "x")[0]
    assert g.by_30d == pytest.approx(5 / 20)                    # 5 of 20 responded within 30 days
    # the same 5 responders next to 15 applications submitted 2 days ago: only 5 can tell, so no figure is shown
    fresh = [S.AppEvents(i, as_of - timedelta(days=2), [("confirmation", as_of - timedelta(days=2))]) for i in range(200, 215)]
    g2 = S.group_by(some_responded + fresh, S.statuses(some_responded + fresh, as_of), lambda a: "x")[0]
    assert g2.by_30d is None and g2.median_days is None and g2.too_few


def test_the_cutoff_is_45_days_until_there_are_twenty_responses_then_learned_and_clamped():
    few = [app(i, [("interview", 10)]) for i in range(19)]
    assert S.ghost_cutoff(few, T0 + timedelta(days=100)) == 45
    many = [app(i, [("interview", 10 + i)]) for i in range(25)]          # first responses on days 10..34, nobody silent
    cut, basis = S.ghost_cutoff_info(many, T0 + timedelta(days=100))
    assert cut == 32 and "Kaplan-Meier" in basis                          # day by which 90% of all applications had replied
    quick = [app(i, [("interview", 2)]) for i in range(25)]
    assert S.ghost_cutoff(quick, T0 + timedelta(days=100)) == 21          # clamped below
    slow = [app(i, [("interview", 200)]) for i in range(25)]
    assert S.ghost_cutoff(slow, T0 + timedelta(days=300)) == 120          # and above


def test_fast_responders_among_many_silent_applications_do_not_set_a_short_deadline():
    # 20 replies on day 2, 80 applications silent for 30 days: the responders alone say "2 days", all applications say
    # "only 20% had replied by day 30", so no deadline can be estimated and the default is used and labelled uncertain.
    fast = [app(i, [("confirmation", 0), ("interview", 2)]) for i in range(20)]
    silent = [app(100 + i, [("confirmation", 0)]) for i in range(80)]
    cut, basis = S.ghost_cutoff_info(fast + silent, T0 + timedelta(days=30))
    assert cut == 45 and "uncertain" in basis
    sts = S.statuses(fast + silent, T0 + timedelta(days=30))
    assert all(sts[100 + i].label == "pending" for i in range(80))        # nobody is called ghosted on day 30


def test_a_heavily_censored_curve_falls_back_instead_of_inventing_a_deadline():
    # 20 replies, 10 old applications that never answered (so the curve cannot reach 90%) and 60 too young to say anything
    replies = [app(i, [("interview", 5 + i)]) for i in range(20)]
    replies += [app(300 + i, [("confirmation", 0)]) for i in range(10)]
    young = [S.AppEvents(100 + i, T0 + timedelta(days=60), [("confirmation", T0 + timedelta(days=60))]) for i in range(60)]
    cut, basis = S.ghost_cutoff_info(replies + young, T0 + timedelta(days=64))
    assert cut == 45 and "uncertain" in basis


def test_an_application_submitted_after_the_evaluation_date_is_absent_and_never_has_a_negative_age():
    old = app(1, [("confirmation", 0)])
    future = S.AppEvents(2, T0 + timedelta(days=40), [("confirmation", T0 + timedelta(days=40))])
    as_of = T0 + timedelta(days=30)
    sts = S.statuses([old, future], as_of)
    assert set(sts) == {1}
    assert all(st.age_days >= 0 for st in sts.values())
    assert [g.n for g in S.group_by([old, future], sts, lambda a: "x")] == [1]
    assert S.visible([old, future], as_of) == [old]


def test_a_late_reply_after_the_cutoff_updates_the_label():
    a = app(1, [("confirmation", 0)])
    assert label(a, 60).label == "ghosted"
    a2 = app(1, [("confirmation", 0), ("interview", 70)])
    assert label(a2, 60).label == "ghosted"                      # as of day 60 the reply had not come yet
    assert label(a2, 80).label == "positive"


def test_wilson_interval():
    lo, hi = S.wilson(5, 10)
    assert (round(lo, 3), round(hi, 3)) == (0.237, 0.763)
    assert S.wilson(0, 10)[0] == 0 and round(S.wilson(0, 10)[1], 3) == 0.278
    assert S.wilson(0, 0) == (0.0, 1.0)


def test_groups_report_rates_and_mark_small_ones():
    apps = ([app(i, [("confirmation", 0), ("interview", 8)], "workday") for i in range(1, 4)]
            + [app(i, [("confirmation", 0)], "workday") for i in range(4, 12)]        # silent past the cutoff: ghosted
            + [app(20, [("confirmation", 0), ("rejection", 5)], "lever"), app(21, [("withdrawn", 1)], "lever"),
               app(22, [("confirmation", 0)], "lever")])
    as_of = T0 + timedelta(days=60)
    sts = S.statuses(apps, as_of)
    groups = {g.key: g for g in S.group_by(apps, sts, lambda a: a.portal)}
    w = groups["workday"]
    assert (w.n, w.resolved, w.positive, w.ghosted) == (11, 11, 3, 8) and w.too_few
    assert w.positive_rate == pytest.approx(3 / 11) and w.response_ci[0] < w.response_rate < w.response_ci[1]
    assert groups["lever"].n == 2                                                  # the withdrawn one is left out
    assert S.band(None) == "no score" and S.band(69.9) == "under 70" and S.band(80) == "80 and over"


def test_snapshot_freezes_the_resume_scores_role_and_skills(tmp_path, monkeypatch):
    """F06: the hash is the submitted PDF's own, the scores are the required sites' for exactly that PDF and job."""
    import hashlib

    from jobagent.tools import ats_sites
    conn = db.connect(tmp_path / "jobs.sqlite")
    job = tmp_path / "outputs" / "acme" / "1"
    job.mkdir(parents=True)
    pdf = b"%PDF-1.4 submitted resume" + b"." * 300
    sha = hashlib.sha256(pdf).hexdigest()
    (job / "resume.pdf").write_bytes(pdf)
    (job / "jd.json").write_text(json.dumps({"company": "Acme", "title": "Engineer"}))
    (job / "jd.txt").write_text("the posting")
    (job / "filled.json").write_text(json.dumps({"skills": [{"terms": ["Python", "SQL"], "tools_for": [{"tools": ["PyTorch"]}]},
                                                            {"terms": ["python"]}]}))
    conn.execute("INSERT INTO jobs (url, status, role_id) VALUES ('https://acme.com/j/1', 'scraped', 'swe')")
    conn.execute("INSERT INTO applications (job_dir, company, url, state, submitted_at, portal) VALUES (?,?,?,?,?,?)",
                 (str(job), "Acme", "https://acme.com/j/1", "submitted", "2026-09-01T12:00:00Z", "ashby"))
    fp = ats_sites.job_fingerprint("the posting", "Engineer", "Acme")
    required = ats_sites.numeric_sites(ats_sites.AVERAGE_SITES)
    for rnd, site, score in [(0, required[0], 50)] + [(1, s, 60 + 5 * i) for i, s in enumerate(required)]:
        conn.execute("INSERT INTO ats_runs (job_dir, site, round, score, raw, resume_sha) VALUES (?,?,?,?,?,?)",
                     (str(job), site, rnd, score, json.dumps({"breakdown": {"job_fingerprint": fp}}), sha))
    conn.commit()
    snap = S.snapshot(conn, job)
    assert snap["resume_sha"] == sha and snap["ats_average"] == 70.0 and snap["role_id"] == "swe"
    assert json.loads(snap["keywords"]) == ["python", "sql", "pytorch"]
    row = S.load(conn)[0]
    assert (row.portal, row.role_id, row.resume_sha, row.ats_average) == ("ashby", "swe", sha[:8], 70.0)
    # a later re-tailor of the folder must not rewrite what was submitted
    conn.execute("INSERT INTO ats_runs (job_dir, site, round, score, resume_sha) VALUES (?,?,?,?,?)", (str(job), required[0], 2, 99, "ffff"))
    (job / "resume.pdf").write_bytes(b"%PDF-1.4 a later build" + b"." * 300)
    S.snapshot(conn, job)
    assert S.load(conn)[0].resume_sha == sha[:8]
