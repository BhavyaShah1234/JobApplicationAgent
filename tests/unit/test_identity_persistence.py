"""F25: one job is one record however its folder or address is spelled; vault updates are not lost; a resume version is published whole."""
import json
import threading
from pathlib import Path

import pytest

from jobagent import db
from jobagent.steps import multi_agent as M
from jobagent.steps import submit as S
from jobagent.tools import bundle, documents
from jobagent.tools import jobqueue as Q
from jobagent.tools.vault import Vault

JD = {"company": "Acme", "title": "Engineer", "apply_url": "https://apply.acme.test/a/1?utm_source=x", "source_url": "https://acme.test/jobs/1"}


# --- job identity ---------------------------------------------------------------------------------------------------------

def test_every_spelling_of_a_job_folder_is_one_key():
    spellings = ["outputs/acme/1", "./outputs/acme/1/", "/app/outputs/acme/1", "/home/me/Projects/J/outputs/acme/1",
                 "outputs//acme/./1", Path("/app/outputs/acme/1")]
    assert {db.job_key(s) for s in spellings} == {"outputs/acme/1"}
    assert db.job_key("/tmp/elsewhere/job") == "/tmp/elsewhere/job"                 # no outputs directory: kept as normalised
    assert "/app/outputs/acme/1" in db.job_key_aliases("outputs/acme/1")


def test_relative_and_absolute_spellings_reach_the_same_application(tmp_path):
    conn = db.connect(tmp_path / "jobs.sqlite")
    S.record(conn, Path("outputs/acme/1"), JD, state="submitted", submitted_at="2026-11-01T00:00:00Z")
    S.record(conn, Path("/app/outputs/acme/1"), JD, state="filled", filled_at="2026-11-02T00:00:00Z")   # a different spelling
    assert conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == 1
    assert conn.execute("SELECT state FROM applications").fetchone()[0] == "submitted"          # and the sent state held
    stop = S.guard(conn, Path("/home/me/J/outputs/acme/1"))
    assert stop is not None and "submitted" in stop["message"].lower() + stop["state"]


def test_legacy_rows_are_rewritten_once_and_a_conflict_is_left_alone_and_flagged(tmp_path):
    path = tmp_path / "jobs.sqlite"
    conn = db.connect(path)
    with conn:
        # Construct a genuine pre-relationship database: old writers did not have
        # the canonicalising/linking triggers installed by today's connect().
        for trigger in ("link_legacy_application", "link_legacy_attempt", "validate_attempt_parent",
                        "validate_attempt_parent_update", "require_application_parent"):
            conn.execute(f"DROP TRIGGER {trigger}")
        conn.execute("DELETE FROM schema_migrations WHERE version='relationships_v1'")
        conn.execute("INSERT INTO applications (job_dir, company, state) VALUES ('/app/outputs/acme/1', 'Acme', 'submitted')")
        conn.execute("INSERT INTO submission_attempts (job_dir, attempt_no, state, started_at) VALUES ('/app/outputs/acme/1', 1, 'submitted', 'x')")
        conn.execute("INSERT INTO ats_runs (job_dir, site, round, score) VALUES ('/app/outputs/acme/1', 'jobscan', 0, 70)")
        conn.execute("INSERT INTO applications (job_dir, company, state) VALUES ('/app/outputs/beta/2', 'Beta', 'filled')")
        conn.execute("INSERT INTO applications (job_dir, company, state) VALUES ('outputs/beta/2', 'Beta', 'filled')")   # taken already
        conn.execute("DELETE FROM kv WHERE key LIKE 'job_key%'")
    conn.close()
    conn = db.connect(path)
    rows = {r["job_dir"] for r in conn.execute("SELECT job_dir FROM applications")}
    assert "outputs/acme/1" in rows and "/app/outputs/beta/2" in rows and "outputs/beta/2" in rows
    assert conn.execute("SELECT job_dir FROM submission_attempts").fetchone()[0] == "outputs/acme/1"
    assert conn.execute("SELECT job_dir FROM ats_runs").fetchone()[0] == "outputs/acme/1"
    assert json.loads(conn.execute("SELECT value FROM kv WHERE key = 'job_key_conflicts'").fetchone()[0]) == ["/app/outputs/beta/2"]
    assert db.canonicalize_job_keys(conn) == 0                                                  # idempotent


# --- addresses --------------------------------------------------------------------------------------------------------------

def test_the_apply_link_and_the_posting_address_find_the_same_job(tmp_path):
    conn = db.connect(tmp_path / "jobs.sqlite")
    with conn:
        conn.execute("INSERT INTO jobs (url, status, role_id) VALUES (?, 'scraped', 'swe')", (Q.canonical_url(JD["source_url"]),))
    assert db.job_for_urls(conn, JD["apply_url"]) is None                         # nothing links the two yet
    S.record(conn, Path("outputs/acme/1"), JD, state="filled")                    # recording the application records the alias
    row = db.job_for_urls(conn, JD["apply_url"])
    assert row is not None and row["role_id"] == "swe"
    assert db.job_for_urls(conn, "https://acme.test/jobs/1/?utm_campaign=z")["role_id"] == "swe"   # tracking noise does not matter
    assert db.job_for_urls(conn, "https://other.test/x") is None


def test_the_snapshot_finds_the_role_family_through_the_apply_link(tmp_path, monkeypatch):
    from jobagent.tools import outcome_stats
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    from jobagent.config import get_settings
    get_settings.cache_clear()
    conn = db.connect()
    folder = tmp_path / "outputs" / "acme" / "1"
    folder.mkdir(parents=True)
    (folder / "jd.json").write_text(json.dumps(JD))
    (folder / "resume.pdf").write_bytes(b"%PDF-1.4 fixture")
    with conn:
        conn.execute("INSERT INTO jobs (url, status, role_id) VALUES (?, 'scraped', 'data')", (Q.canonical_url(JD["source_url"]),))
    S.record(conn, folder, JD, state="submitted", submitted_at="2026-11-01T00:00:00Z")
    snap = outcome_stats.snapshot(conn, "outputs/acme/1")
    assert snap["role_id"] == "data"                                                # the application's URL is the apply link
    get_settings.cache_clear()


# --- the vault --------------------------------------------------------------------------------------------------------------

def test_concurrent_vault_updates_keep_every_entry(tmp_path):
    from cryptography.fernet import Fernet
    key = Fernet.generate_key().decode()
    path = tmp_path / "vault.enc"
    errors = []

    def writer(i):
        try:
            Vault(path, key).set(f"site{i}", password=f"pw{i}")        # its own object, as a separate process would have
        except Exception as e:                                           # pragma: no cover
            errors.append(e)
    threads = [threading.Thread(target=writer, args=(i,)) for i in range(24)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors and Vault(path, key).names() == sorted(f"site{i}" for i in range(24))
    assert Vault(path, key).get("site7") == {"password": "pw7"}


# --- coherent resume versions -----------------------------------------------------------------------------------------------

def old_version(d):
    for name, data in {"resume.tex": "OLD tex", "resume.pdf": b"OLD pdf", "projects.json": "OLD projects", "filled.json": "OLD filled"}.items():
        (d / name).write_bytes(data.encode() if isinstance(data, str) else data)


def state(d):
    return {n: (d / n).read_bytes().decode() if (d / n).exists() else None for n in bundle.BUNDLE_FILES}


def test_publish_moves_every_file_and_removes_the_one_the_version_does_not_have(tmp_path):
    old_version(tmp_path)
    bundle.publish(tmp_path, {"resume.tex": "NEW tex", "resume.pdf": b"NEW pdf", "projects.json": None, "filled.json": "NEW filled"})
    assert state(tmp_path) == {"resume.tex": "NEW tex", "resume.pdf": "NEW pdf", "projects.json": None, "filled.json": "NEW filled"}
    assert not list(tmp_path.glob(".*.new")) and not (tmp_path / bundle.JOURNAL).exists()


def test_a_crash_before_the_journal_changes_nothing_and_leftovers_are_cleared(tmp_path, monkeypatch):
    old_version(tmp_path)
    real = bundle._write_synced

    def dying(path, data):
        if path.name.startswith(bundle.JOURNAL):
            raise KeyboardInterrupt("killed while writing the journal")
        real(path, data)
    monkeypatch.setattr(bundle, "_write_synced", dying)
    with pytest.raises(KeyboardInterrupt):
        bundle.publish(tmp_path, {"resume.tex": "NEW tex", "resume.pdf": b"NEW pdf"})
    assert state(tmp_path)["resume.tex"] == "OLD tex" and state(tmp_path)["resume.pdf"] == "OLD pdf"     # the old version stands
    monkeypatch.setattr(bundle, "_write_synced", real)
    bundle.recover(tmp_path)
    assert not list(tmp_path.glob(".*.new"))


def test_a_crash_after_the_journal_is_completed_never_left_half_applied(tmp_path, monkeypatch):
    old_version(tmp_path)
    real_apply = bundle._apply
    monkeypatch.setattr(bundle, "_apply", lambda d: (_ for _ in ()).throw(KeyboardInterrupt("killed while moving files")))
    with pytest.raises(KeyboardInterrupt):
        bundle.publish(tmp_path, {"resume.tex": "NEW tex", "resume.pdf": b"NEW pdf", "projects.json": None, "filled.json": "NEW filled"})
    assert (tmp_path / bundle.JOURNAL).exists()
    monkeypatch.setattr(bundle, "_apply", real_apply)
    assert documents.verify(tmp_path)["ok"] is False and "interrupted" in " ".join(documents.verify(tmp_path)["reasons"])
    assert bundle.recover(tmp_path) is True
    assert state(tmp_path) == {"resume.tex": "NEW tex", "resume.pdf": "NEW pdf", "projects.json": None, "filled.json": "NEW filled"}


def test_a_rollback_restores_source_pdf_and_every_piece_of_metadata(tmp_path):
    from jobagent.state import JobDescription  # noqa: F401
    (tmp_path / "resume.tex").write_text("LATER tex")
    (tmp_path / "resume.pdf").write_bytes(b"LATER pdf")
    (tmp_path / "projects.json").write_text("LATER projects")                  # a later attempt created a projects layout
    f = type("F", (), {"model_copy": lambda self, deep=False: self, "model_dump_json": lambda self, indent=1: "BEST filled"})()
    best = {"tex": "BEST tex", "pdf": b"BEST pdf", "projects": None, "f": f, "ev": {"missing": [], "advice": []}}
    out = M._rollback({"ctx": {"job_dir": tmp_path}, "best": best, "ats_round": 3}, "undone")
    assert state(tmp_path) == {"resume.tex": "BEST tex", "resume.pdf": "BEST pdf", "projects.json": None, "filled.json": "BEST filled"}
    assert out["history"] == ["rollback"]


def test_two_builds_of_one_job_cannot_run_at_once(tmp_path):
    with bundle.job_lock(tmp_path, "tailor"):
        with pytest.raises(bundle.JobBusy, match="already running a tailor"):
            with bundle.job_lock(tmp_path, "tailor"):
                pass
        with pytest.raises(bundle.JobBusy):
            with bundle.job_lock(tmp_path, "refresh"):
                pass
    with bundle.job_lock(tmp_path, "tailor"):                                      # released when the first build ended
        pass
