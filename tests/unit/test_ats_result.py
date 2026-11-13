"""F06: one authoritative ATS result per resume version; review, summary and learning snapshot agree."""
import json
from pathlib import Path

import pytest

from jobagent import db
from jobagent.steps import submit as S
from jobagent.tools import ats_result, ats_sites, outcome_stats

JD = {"company": "Acme", "title": "Engineer", "apply_url": "https://e.test/a", "source_url": "https://e.test/a"}
PDF = b"%PDF-1.4 resume one" + b"." * 300
OTHER_PDF = b"%PDF-1.4 resume TWO" + b"." * 300
SITES = ats_sites.numeric_sites(ats_sites.AVERAGE_SITES)
LIMITED = [s for s in SITES if s in ats_sites.BUDGET_SITES]
UNLIMITED = [s for s in SITES if s not in ats_sites.BUDGET_SITES]


def make(tmp_path, pdf=PDF, jd_text="the posting text"):
    d = tmp_path / "outputs" / "acme" / "1"
    d.mkdir(parents=True)
    (d / "resume.pdf").write_bytes(pdf)
    (d / "cover_letter.pdf").write_bytes(b"%PDF-1.4 letter" + b"." * 300)
    (d / "jd.json").write_text(json.dumps(JD))
    (d / "jd.txt").write_text(jd_text)
    conn = db.connect(tmp_path / "jobs.sqlite")
    return d, conn


def add(conn, d, site, rnd, score, pdf=PDF, jd_text="the posting text", manual=False, fingerprint=True):
    import hashlib
    fp = ats_sites.job_fingerprint(jd_text, JD["title"], JD["company"])
    if manual:
        raw = {"manual": True, **({"job_fingerprint": fp} if fingerprint else {})}
    else:
        raw = {"site": site, "breakdown": ({"job_fingerprint": fp} if fingerprint else {})}
    conn.execute("INSERT INTO ats_runs (job_dir, site, round, score, missing, raw, resume_sha) VALUES (?, ?, ?, ?, '[]', ?, ?)",
                 (str(d), site, rnd, score, json.dumps(raw), hashlib.sha256(pdf).hexdigest()))
    conn.commit()


def split_rounds(conn, d):
    """The F06 example: unlimited sites scored in round 1, only the limited ones in the last round."""
    for site, score in zip(UNLIMITED, (60.0, 70.0)):
        add(conn, d, site, 1, score)
    for site, score in zip(LIMITED, (60.0, 80.0, 80.0)):
        add(conn, d, site, 3, score)


def snap_app(conn, d, state="submitted"):
    S.record(conn, d, JD, state=state, submitted_at="2026-11-01T10:00:00Z")


# --- scores split across rounds ------------------------------------------------------------------------------------------------

def test_scores_split_across_rounds_give_the_five_site_average_for_the_same_version(tmp_path):
    d, conn = make(tmp_path)
    split_rounds(conn, d)
    g = ats_result.gather(d, conn=conn)
    assert g["complete"] is True and len(g["scores"]) == len(SITES) and g["average"] == 70.0
    assert {p["round"] for p in g["provenance"].values()} == {1, 3}


def test_review_summary_and_learning_snapshot_agree(tmp_path):
    d, conn = make(tmp_path)
    split_rounds(conn, d)
    snap_app(conn, d)
    line = S.ats_line(conn, d)
    summary = ats_sites.write_summary(d, {}, conn=conn)                   # nothing fresh: the history alone completes it
    snap = outcome_stats.snapshot(conn, d)
    assert "average 70.0 over 5 required sites" in line
    assert summary["average"] == 70.0 and json.loads((d / "ats_summary.json").read_text())["average"] == 70.0
    assert snap["ats_average"] == 70.0 and json.loads(snap["ats_scores"]) == summary["scores"]
    assert summary["resume_sha256"] == snap["resume_pdf_sha256"] == ats_result.sha256_file(d / "resume.pdf")


def test_the_last_round_alone_is_not_the_five_site_average(tmp_path):
    """The previous review line averaged only the final round's rows: 73.3 from three sites, not 70.0 from all five."""
    d, conn = make(tmp_path)
    split_rounds(conn, d)
    rows = conn.execute("SELECT score FROM ats_runs WHERE round = (SELECT MAX(round) FROM ats_runs)").fetchall()
    assert len(rows) == 3 and round(sum(r["score"] for r in rows) / 3, 1) == 73.3
    assert "average 70.0 over 5 required sites" in S.ats_line(conn, d)


# --- versions cannot mix ----------------------------------------------------------------------------------------------------

def test_scores_of_another_pdf_or_another_posting_are_never_mixed_in(tmp_path):
    d, conn = make(tmp_path)
    split_rounds(conn, d)
    add(conn, d, UNLIMITED[0], 4, 5.0, pdf=OTHER_PDF)                           # a later round, but for a different PDF
    add(conn, d, UNLIMITED[1], 4, 5.0, jd_text="a different posting text")       # same PDF, different job fingerprint
    add(conn, d, LIMITED[0], 4, 5.0, fingerprint=False)                        # no job identity and not manual
    g = ats_result.gather(d, conn=conn)
    assert g["average"] == 70.0 and g["excluded"] == 3


def test_a_manual_score_for_this_pdf_counts_and_the_latest_row_wins(tmp_path):
    d, conn = make(tmp_path)
    split_rounds(conn, d)
    add(conn, d, LIMITED[0], 5, 90.0, manual=True)
    g = ats_result.gather(d, conn=conn)
    assert g["scores"][LIMITED[0]] == 90.0 and g["provenance"][LIMITED[0]]["manual"] is True


def test_fresh_scores_for_this_pdf_override_the_history(tmp_path):
    d, conn = make(tmp_path)
    split_rounds(conn, d)
    g = ats_result.gather(d, conn=conn, fresh={UNLIMITED[0]: 100.0})
    assert g["scores"][UNLIMITED[0]] == 100.0 and g["provenance"][UNLIMITED[0]] == {"fresh": True}


# --- missing sites keep the average null --------------------------------------------------------------------------------------

def test_a_missing_required_site_leaves_the_average_null_everywhere(tmp_path):
    d, conn = make(tmp_path)
    for site in SITES[:-1]:
        add(conn, d, site, 1, 90.0)
    snap_app(conn, d)
    g = ats_result.gather(d, conn=conn)
    assert g["average"] is None and g["missing"] == [SITES[-1]] and g["complete"] is False
    assert "average unavailable (null)" in S.ats_line(conn, d) and SITES[-1] in S.ats_line(conn, d)
    assert ats_sites.write_summary(d, {}, conn=conn)["average"] is None
    snap = outcome_stats.snapshot(conn, d)
    assert snap["ats_average"] is None and len(json.loads(snap["ats_scores"])) == len(SITES) - 1


def test_no_scores_for_this_version_is_reported_as_such(tmp_path):
    d, conn = make(tmp_path)
    add(conn, d, SITES[0], 1, 90.0, pdf=OTHER_PDF)
    assert S.ats_line(conn, d) == "no ATS scores recorded for this resume version"


# --- the snapshot's artifact hashes do not depend on the checkers -------------------------------------------------------------

def test_hashes_are_frozen_even_when_no_site_was_reachable(tmp_path):
    d, conn = make(tmp_path)
    snap_app(conn, d)
    snap = outcome_stats.snapshot(conn, d)
    row = dict(conn.execute("SELECT * FROM applications").fetchone())
    assert row["resume_sha"] == row["resume_pdf_sha256"] == ats_result.sha256_file(d / "resume.pdf")
    assert row["cover_letter_sha256"] == ats_result.sha256_file(d / "cover_letter.pdf") and row["jd_fingerprint"]
    assert row["ats_average"] is None and row["snapshot_status"] == "reconstructed"          # no attempt payload: not verified


def write_payload(d, conn, held_resume, held_cover=None, resume_label="Resume", cover_label="Cover letter", resume_unreadable=False):
    """A frozen attempt payload as F04/F06 write it: attachments tied to their controls, and the identity derived from them."""
    import hashlib
    sha = lambda b: hashlib.sha256(b).hexdigest()   # noqa: E731
    atts = [{"frame": 0, "slot": 0, "label": resume_label, "files": None if resume_unreadable else [{"name": "r.pdf", "size": 1, "sha256": sha(held_resume)}]}]
    if held_cover is not None:
        atts.append({"frame": 0, "slot": 0, "label": cover_label, "files": [{"name": "c.pdf", "size": 1, "sha256": sha(held_cover)}]})
    roles = ats_result.attachment_roles(atts)
    payload = d / "application" / "submission-1.json"
    payload.parent.mkdir(exist_ok=True)
    payload.write_text(json.dumps({"attachments": atts, "identity": {
        "resume_sha256": roles["resume"], "cover_letter_sha256": roles["cover_letter"],
        "job_fingerprint": ats_sites.job_fingerprint("the posting text", JD["title"], JD["company"])}}))
    conn.execute("INSERT INTO submission_attempts (job_dir, attempt_no, state, payload_path, started_at) VALUES (?, 1, 'submitted', ?, 't')",
                 (str(d), str(payload)))
    conn.commit()


def five(conn, d, score, pdf):
    for i, site in enumerate(SITES):
        add(conn, d, site, 1, score, pdf=pdf)


def test_the_snapshot_describes_the_submitted_version_not_the_disk_file(tmp_path):      # F06 verification 1
    d, conn = make(tmp_path)                       # disk resume.pdf is PDF ("A")
    five(conn, d, 90.0, PDF)
    five(conn, d, 40.0, OTHER_PDF)                 # the submitted version ("B") has its own five scores
    write_payload(d, conn, OTHER_PDF)
    snap_app(conn, d)
    row = dict(conn.execute("SELECT * FROM applications").fetchone())
    import hashlib
    assert row["resume_sha"] == hashlib.sha256(OTHER_PDF).hexdigest() and row["ats_average"] == 40.0
    assert row["snapshot_status"] == "verified" and json.loads(row["snapshot_detail"])["disk_matches_submitted"] is False
    assert outcome_stats.load(conn)[0].ats_average == 40.0


def test_a_file_in_the_cover_letter_slot_does_not_verify_the_resume(tmp_path):
    d, conn = make(tmp_path)                       # disk resume is A
    five(conn, d, 90.0, PDF)
    five(conn, d, 40.0, OTHER_PDF)
    write_payload(d, conn, held_resume=OTHER_PDF, held_cover=PDF)      # A appears only in the cover-letter control
    snap_app(conn, d)
    row = dict(conn.execute("SELECT * FROM applications").fetchone())
    import hashlib
    assert row["resume_sha"] == hashlib.sha256(OTHER_PDF).hexdigest() and row["cover_letter_sha256"] == hashlib.sha256(PDF).hexdigest()
    assert row["ats_average"] == 40.0 and row["snapshot_status"] == "verified"


def test_an_unreadable_resume_control_is_unverifiable_and_no_scores_are_attributed(tmp_path):
    d, conn = make(tmp_path)
    five(conn, d, 90.0, PDF)                       # the disk file's scores must not be attributed to an unreadable upload
    write_payload(d, conn, PDF, resume_unreadable=True)
    snap_app(conn, d)
    row = dict(conn.execute("SELECT * FROM applications").fetchone())
    assert row["snapshot_status"] == "unverifiable" and row["resume_sha"] is None and row["ats_average"] is None
    assert row["ats_scores"] is None


def test_submitted_hashes_come_from_the_attempt_even_when_no_site_was_reachable(tmp_path):
    d, conn = make(tmp_path)
    write_payload(d, conn, PDF, held_cover=b"%PDF-1.4 letter" + b"." * 300)
    snap_app(conn, d)
    row = dict(conn.execute("SELECT * FROM applications").fetchone())
    assert row["resume_sha"] == ats_result.sha256_file(d / "resume.pdf") and row["ats_average"] is None


def test_a_snapshot_is_one_coherent_unit_and_a_later_version_cannot_leak_into_it(tmp_path):    # F06 verification 2
    d, conn = make(tmp_path)
    for site in SITES[:-1]:
        add(conn, d, site, 1, 90.0)                 # version A: four of five sites
    write_payload(d, conn, PDF)
    snap_app(conn, d)
    first = dict(conn.execute("SELECT * FROM applications").fetchone())
    assert first["ats_average"] is None and len(json.loads(first["ats_scores"])) == 4 and first["snapshot_status"] == "verified"
    (d / "resume.pdf").write_bytes(OTHER_PDF)       # re-tailored: version B, all five scores
    five(conn, d, 20.0, OTHER_PDF)
    outcome_stats.snapshot(conn, d)
    S.record(conn, d, JD, state="confirmed", confirmed_at="2026-11-02T10:00:00Z")           # confirmation re-runs the snapshot
    assert dict(conn.execute("SELECT * FROM applications").fetchone())["resume_sha"] == first["resume_sha"]
    after = {k: v for k, v in dict(conn.execute("SELECT * FROM applications").fetchone()).items()
             if k in ("resume_sha", "resume_pdf_sha256", "ats_average", "ats_scores", "ats_sites", "snapshot_status", "jd_fingerprint")}
    assert after == {k: first[k] for k in after}                                            # still the coherent, incomplete A result
    assert outcome_stats.snapshot(conn, d)["ats_average"] is None                           # returns the persisted one
    add(conn, d, SITES[-1], 2, 90.0)                                                         # A's own fifth score arrives
    done = outcome_stats.snapshot(conn, d)
    assert done["ats_average"] == 90.0 and done["resume_sha"] == first["resume_sha"] and len(json.loads(done["ats_scores"])) == 5


def test_no_pdf_at_all_is_unknown_not_invented(tmp_path):
    d, conn = make(tmp_path)
    (d / "resume.pdf").unlink()
    snap_app(conn, d)
    outcome_stats.snapshot(conn, d)
    row = dict(conn.execute("SELECT * FROM applications").fetchone())
    assert row["resume_sha"] is None and row["snapshot_status"] == "unknown"


def test_a_later_backfill_does_not_overwrite_the_frozen_snapshot_and_a_new_attempt_clears_it(tmp_path):
    d, conn = make(tmp_path)
    snap_app(conn, d)
    outcome_stats.snapshot(conn, d)
    first = dict(conn.execute("SELECT resume_sha, snapshot_status FROM applications").fetchone())
    (d / "resume.pdf").write_bytes(OTHER_PDF)                                                  # edited after submission
    outcome_stats.snapshot(conn, d)
    assert dict(conn.execute("SELECT resume_sha, snapshot_status FROM applications").fetchone()) == first
    S.record(conn, d, JD, state="failed")                                                      # (a failed attempt may be retried)
    conn.execute("UPDATE applications SET state = 'approved'")
    conn.commit()
    S.begin_attempt(conn, d, "sha", {"fields": [], "attachments": []}, "t")
    row = dict(conn.execute("SELECT resume_sha, resume_pdf_sha256, snapshot_status, jd_fingerprint FROM applications").fetchone())
    assert set(row.values()) == {None}


def test_the_stats_loader_carries_the_snapshot_status(tmp_path):
    d, conn = make(tmp_path)
    snap_app(conn, d)
    outcome_stats.snapshot(conn, d)
    assert outcome_stats.load(conn)[0].snapshot_status == "reconstructed"


# --- F06 verification 3: a manual score names its PDF and its job like every other row --------------------------------------------

def record_cli(monkeypatch, tmp_path, d, site, score):
    from typer.testing import CliRunner

    from jobagent import main
    from jobagent.config import get_settings
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    get_settings.cache_clear()
    r = CliRunner().invoke(main.app, ["ats-record", str(d), site, str(score)])
    get_settings.cache_clear()
    return r


def test_a_manual_score_recorded_for_one_posting_does_not_complete_another_postings_result(tmp_path, monkeypatch):
    d, conn = make(tmp_path)                                    # posting A
    assert record_cli(monkeypatch, tmp_path, d, "atsfriendly", 90).exit_code == 0       # the real CLI, manual score for A
    (d / "jd.txt").write_text("a different posting text")        # the folder is re-pointed at posting B; same PDF bytes
    for site in SITES:
        if site != "atsfriendly":
            add(conn, d, site, 1, 90.0, jd_text="a different posting text")
    g = ats_result.gather(d, conn=db.connect(tmp_path / "jobs.sqlite"))
    assert g["complete"] is False and g["average"] is None and g["missing"] == ["atsfriendly"]
    assert record_cli(monkeypatch, tmp_path, d, "atsfriendly", 80).exit_code == 0       # recorded again for posting B
    g2 = ats_result.gather(d, conn=db.connect(tmp_path / "jobs.sqlite"))
    assert g2["complete"] is True and g2["scores"]["atsfriendly"] == 80 and g2["provenance"]["atsfriendly"]["manual"] is True


def test_a_manual_row_with_a_conflicting_or_missing_fingerprint_never_counts(tmp_path):
    d, conn = make(tmp_path)
    for site in SITES[:-1]:
        add(conn, d, site, 1, 90.0)
    add(conn, d, SITES[-1], 2, 90.0, manual=True, jd_text="another posting")                # an explicit different fingerprint
    assert ats_result.gather(d, conn=conn)["complete"] is False
    add(conn, d, SITES[-1], 3, 90.0, manual=True, fingerprint=False)                         # a legacy manual score: no identity
    g = ats_result.gather(d, conn=conn)
    assert g["complete"] is False and g["unidentified"] == [SITES[-1]]
    assert "record again with `jobagent ats-record`" in S.ats_line(conn, d)


def test_manual_scores_for_the_loop_follow_the_same_rule(tmp_path, monkeypatch):
    d, conn = make(tmp_path)
    assert record_cli(monkeypatch, tmp_path, d, "atsfriendly", 77).exit_code == 0
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    from jobagent.config import get_settings
    get_settings.cache_clear()
    assert ats_sites.manual_scores(d) == {"atsfriendly": 77}
    (d / "jd.txt").write_text("a different posting text")
    assert ats_sites.manual_scores(d) == {}
    get_settings.cache_clear()


# --- F06 second verification: disk-derived skills only when the submitted resume is the disk resume ---------------------------------

SKILLS = json.dumps({"skills": [{"terms": ["disk_only_skill"]}]})


def keywords_case(tmp_path, held_resume=None, unreadable=False, with_payload=True):
    d, conn = make(tmp_path)
    (d / "filled.json").write_text(SKILLS)
    if with_payload:
        write_payload(d, conn, held_resume if held_resume is not None else PDF, resume_unreadable=unreadable)
    snap_app(conn, d)
    return d, conn, dict(conn.execute("SELECT * FROM applications").fetchone())


def test_an_unverifiable_resume_does_not_get_the_disk_files_skills(tmp_path):
    d, conn, row = keywords_case(tmp_path, unreadable=True)
    assert row["snapshot_status"] == "unverifiable" and row["keywords"] is None
    assert outcome_stats.load(conn)[0].keywords == frozenset()                          # the actual loader / learning input
    assert json.loads(row["snapshot_detail"])["disk_keywords_withheld"] == ["python"] or \
        json.loads(row["snapshot_detail"])["disk_keywords_withheld"] == ["disk_only_skill"]


def test_a_verified_match_supplies_its_own_keywords(tmp_path):
    d, conn, row = keywords_case(tmp_path, held_resume=PDF)
    assert row["snapshot_status"] == "verified" and json.loads(row["keywords"]) == ["disk_only_skill"]
    assert outcome_stats.load(conn)[0].keywords == frozenset({"disk_only_skill"})


def test_a_known_different_disk_version_does_not_supply_keywords(tmp_path):
    d, conn, row = keywords_case(tmp_path, held_resume=OTHER_PDF)                       # submitted B, disk A
    assert row["snapshot_status"] == "verified" and row["keywords"] is None
    assert outcome_stats.load(conn)[0].keywords == frozenset()


def test_the_labelled_legacy_reconstruction_still_uses_the_disk_files_skills(tmp_path):
    d, conn, row = keywords_case(tmp_path, with_payload=False)
    assert row["snapshot_status"] == "reconstructed" and json.loads(row["keywords"]) == ["disk_only_skill"]
