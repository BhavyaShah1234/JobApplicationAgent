"""scripts/agentctl against a fake `docker`: what it runs, what it refuses, and what goes into a backup."""
import os
import shutil
import stat
import subprocess
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

FAKE_DOCKER = r"""#!/usr/bin/env bash
# a stand-in for docker: records every call in $FAKE/calls, answers from files in $FAKE
echo "$*" >> "$FAKE/calls"
case "$1" in
  version) echo 27.0.0 ;;
  compose)
    case "$2" in
      version) echo "Docker Compose v2" ;;
      up) echo true > "$FAKE/running"; [ -f "$FAKE/next_rev" ] && cp "$FAKE/next_rev" "$FAKE/rev" ;;
      down) echo false > "$FAKE/running" ;;
    esac ;;
  inspect)
    case "$*" in
      *State.Running*) cat "$FAKE/running" 2>/dev/null || echo false ;;
      *Labels*) cat "$FAKE/rev" 2>/dev/null ;;
      *Config.Image*) echo jobagent-harness:latest ;;
      *.Image*) echo sha256:oldimage ;;
      *StartedAt*) echo 2026-11-18T00:00:00Z ;;
      *Mounts*) echo "    /x/data -> /app/data (rw)" ;;
    esac ;;
  exec)
    shift
    case "$*" in
      *"ps -eo args"*) [ -f "$FAKE/busy" ]; exit $? ;;
      *"config show"*) [ ! -f "$FAKE/smoke_fails" ]; exit $? ;;
      *"--print-url"*) echo "http://127.0.0.1:8765/?t=abc" ;;
      *) exit 0 ;;
    esac ;;
  image) [ "$2" = inspect ] && [ -f "$FAKE/has_previous" ]; exit $? ;;
  tag) touch "$FAKE/has_previous" ;;
  build|images) exit 0 ;;
esac
exit 0
"""


@pytest.fixture
def world(tmp_path):
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "agentctl", repo / "scripts" / "agentctl")
    shutil.copy(ROOT / "scripts" / "setup_env.sh", repo / "scripts" / "setup_env.sh")
    for d in ("data/templates", "state/chrome-profile", "state/logs", "outputs/acme"):
        (repo / d).mkdir(parents=True)
    (repo / "data" / "applicant.yaml").write_text("name: Test\n")
    (repo / "data" / "templates" / "resume.tex").write_text("tex")
    (repo / "state" / "vault.enc").write_text("encrypted")
    (repo / "state" / "jobs.sqlite").write_text("db")
    (repo / "state" / "chrome-profile" / "Cookies").write_text("secret cookies")
    (repo / "state" / "logs" / "run.log").write_text("log")
    (repo / "outputs" / "acme" / "resume.pdf").write_text("pdf")
    (repo / ".env").write_text("VAULT_KEY=abc123\nDASHBOARD_PORT=8765\n")
    (repo / ".env").chmod(0o600)
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    fake = bin_ / "docker"
    fake.write_text(FAKE_DOCKER)
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    state = tmp_path / "fake"
    state.mkdir()
    # run both through `bash FILE`: a temp folder on a noexec mount (TMPDIR=/dev/shm) cannot execute files directly
    env = {**os.environ, "DOCKER": f"bash {fake}", "FAKE": str(state), "AGENTCTL_WAIT": "2", "OPENAI_API_KEY": "k"}

    def run(*args):
        return subprocess.run(["bash", str(repo / "scripts" / "agentctl"), *args], cwd=repo, env=env, capture_output=True, text=True, timeout=60)

    def calls():
        p = state / "calls"
        return p.read_text().splitlines() if p.exists() else []
    return type("W", (), {"repo": repo, "state": state, "run": staticmethod(run), "calls": staticmethod(calls)})


def test_the_script_is_valid_shell():
    assert subprocess.run(["bash", "-n", str(ROOT / "scripts" / "agentctl")], capture_output=True).returncode == 0


def test_doctor_passes_on_a_sound_install_and_fails_loudly_on_a_missing_key(world):
    r = world.run("doctor")
    assert r.returncode == 0 and "docker daemon reachable" in r.stdout and "all required checks passed" in r.stdout
    (world.repo / ".env").write_text("OTHER=1\n")
    r = world.run("doctor")
    assert r.returncode == 1 and "FAIL" in r.stdout and "VAULT_KEY" in r.stdout


def test_doctor_warns_when_the_image_is_not_built_from_this_checkout(world):
    (world.state / "running").write_text("true")
    (world.state / "rev").write_text("0" * 40)
    r = world.run("doctor")
    assert "container jobagent is running" in r.stdout and "built from 0000000" in r.stdout and "scripts/agentctl update" in r.stdout


def test_backup_holds_the_data_and_vault_but_never_the_env_file_or_the_browser_profile(world):
    r = world.run("backup")
    assert r.returncode == 0 and "NOT inside: .env" in r.stdout
    arc = next((world.repo / "backups").glob("*.tar.gz"))
    names = tarfile.open(arc).getnames()
    assert "state/vault.enc" in names and "data/applicant.yaml" in names and "state/jobs.sqlite" in names
    assert not any(n.startswith(".env") or "chrome-profile" in n or "state/logs" in n or n.startswith("outputs") for n in names)
    assert stat.S_IMODE(arc.stat().st_mode) == 0o600 and Path(f"{arc}.sha256").exists()
    import time
    time.sleep(1.1)                                                                                   # archives are named by the second
    assert world.run("backup", "--with-outputs").returncode == 0
    newest = sorted((world.repo / "backups").glob("*.tar.gz"))[-1]
    assert any(n.startswith("outputs/") for n in tarfile.open(newest).getnames())


def test_restore_refuses_without_yes_or_while_running_and_otherwise_moves_the_old_folders_aside(world):
    world.run("backup")
    arc = next((world.repo / "backups").glob("*.tar.gz"))
    assert world.run("restore", str(arc)).returncode == 1
    (world.state / "running").write_text("true")
    r = world.run("restore", str(arc), "--yes")
    assert r.returncode == 1 and "stop the container" in r.stderr
    (world.state / "running").write_text("false")
    (world.repo / "state" / "vault.enc").write_text("changed since the backup")
    r = world.run("restore", str(arc), "--yes")
    assert r.returncode == 0 and (world.repo / "state" / "vault.enc").read_text() == "encrypted"
    aside = list(world.repo.glob("state.before-restore-*"))
    assert aside and (aside[0] / "vault.enc").read_text() == "changed since the backup"           # nothing was deleted


def test_a_corrupted_archive_is_refused(world):
    world.run("backup")
    arc = next((world.repo / "backups").glob("*.tar.gz"))
    arc.write_bytes(arc.read_bytes()[:-20])
    assert world.run("restore", str(arc), "--yes").returncode == 1


def test_update_backs_up_builds_with_the_revision_label_recreates_and_smoke_tests(world):
    (world.state / "running").write_text("true")
    r = world.run("update")
    assert r.returncode == 0 and "update complete" in r.stdout
    calls = world.calls()
    kinds = [c.split()[0] + (" " + c.split()[1] if c.startswith("compose") else "") for c in calls]
    assert "tag" in kinds and "build" in kinds and "compose up" in kinds
    build = next(c for c in calls if c.startswith("build"))
    assert "org.opencontainers.image.revision=" in build and "-t jobagent-harness:latest" in build
    assert any(c.startswith("compose up -d --force-recreate") for c in calls)
    assert list((world.repo / "backups").glob("*.tar.gz"))                                         # the backup came first
    assert calls.index(next(c for c in calls if c.startswith("tag"))) < calls.index(build)         # the old image was kept before the build


def test_update_refuses_while_a_run_is_in_progress_unless_forced(world):
    (world.state / "running").write_text("true")
    (world.state / "busy").write_text("1")
    r = world.run("update")
    assert r.returncode == 1 and "run is in progress" in r.stderr and not any(c.startswith("build") for c in world.calls())
    assert world.run("update", "--force").returncode == 0


def test_a_failed_smoke_test_goes_back_to_the_old_image(world):
    (world.state / "running").write_text("true")
    (world.state / "smoke_fails").write_text("1")
    r = world.run("update")
    assert r.returncode == 1 and "going back to jobagent-harness:previous" in r.stdout
    calls = world.calls()
    assert any(c.startswith("tag") and c.endswith("jobagent-harness:latest") for c in calls)       # :previous became :latest again
    assert sum(c.startswith("compose up -d --force-recreate") for c in calls) == 2                  # new, then the rollback


def test_status_and_url(world):
    assert "not running" in world.run("status").stdout
    (world.state / "running").write_text("true")
    (world.state / "rev").write_text("f" * 40)
    out = world.run("status").stdout
    assert "running since" in out and "NOT built from this commit" in out and "http://127.0.0.1:8765/?t=abc" in out


def test_rollback_needs_a_previous_image(world):
    assert world.run("rollback").returncode == 1
