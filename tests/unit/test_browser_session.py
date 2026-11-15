"""F14: apply and submit share one browser-session abstraction; sending from the desktop browser is explicit; the queue knows modes."""
import contextlib
import subprocess
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from jobagent import db, main
from jobagent.config import get_settings
from jobagent.tools import jobqueue as Q
from jobagent.tools import session as S
from jobagent.tools.locations import Decision
from jobagent.tools.matching import Match
from test_apply_exit_codes import BASE, fake_world

READY = {**BASE, "state": "review", "ready_to_submit": True, "blockers": []}


# --- the rules ------------------------------------------------------------------------------------------------------------

def test_filling_accepts_all_three_modes_and_nothing_else():
    for m in ("docker", "desktop", "auto"):
        assert S.check_mode(m, submitting=False) == m
    with pytest.raises(ValueError):
        S.check_mode("firefox", submitting=False)


def test_submitting_needs_a_real_browser_and_the_desktop_needs_an_explicit_flag():
    assert S.check_mode("docker", submitting=True) == "docker"
    assert S.check_mode("desktop", submitting=True, allow_desktop_submit=True) == "desktop"
    with pytest.raises(ValueError, match="--allow-desktop-submit"):
        S.check_mode("desktop", submitting=True)
    with pytest.raises(ValueError, match="cannot start in one browser"):
        S.check_mode("auto", submitting=True, allow_desktop_submit=True)


def test_run_options_describe_the_session_the_same_way_for_apply_and_submit():
    docker = S.Session("docker", object(), "page")
    desktop = S.Session("desktop", object(), "page", policy="scope")
    assert docker.run_options(observe_s=30) == {"already_open": False, "browser_policy": None, "observe_s": 0, "desktop_fallback": False}
    assert docker.run_options(auto_fallback=True)["desktop_fallback"] is True            # apply --browser-mode auto, first pass
    assert desktop.run_options(observe_s=30) == {"already_open": True, "browser_policy": "scope", "observe_s": 30, "desktop_fallback": False}
    assert desktop.run_options()["observe_s"] == 0                                       # submit does not linger in the browser


# --- the commands ---------------------------------------------------------------------------------------------------------

@pytest.fixture
def world(monkeypatch, tmp_path):
    import jobagent.steps.apply as A
    import jobagent.steps.submit as SU
    import jobagent.tools.browser as B
    job = fake_world(monkeypatch, tmp_path, READY)
    seen = {"modes": [], "run": [], "hook": []}

    @contextlib.contextmanager
    def ctx_manager(mode, url, settings=None, *, related_urls=()):
        seen["modes"].append((mode, url, list(related_urls)))
        ctx = SimpleNamespace(pages=[object()], jobagent_page=object(), jobagent_policy="desk-policy" if mode == "desktop" else None)
        yield ctx
    monkeypatch.setattr(B, "application_context", ctx_manager)

    def run(page, job_dir, company, *a, **k):
        seen["run"].append(k)
        return dict(READY)
    monkeypatch.setattr(A, "run", run)
    monkeypatch.setattr(SU, "make_at_review", lambda *a, **k: seen["hook"].append(True) or (lambda *x: {"state": "skipped"}))
    return job, seen


def test_submit_in_docker_is_unchanged(world):
    job, seen = world
    r = CliRunner().invoke(main.app, ["submit", str(job), "acme"])
    assert r.exit_code == 0, r.output
    assert seen["modes"][0][0] == "docker" and seen["run"][0]["already_open"] is False and seen["run"][0]["browser_policy"] is None
    assert seen["hook"]                                                                  # the reviewed-submission hook is attached
    get_settings.cache_clear()


def test_submit_from_the_desktop_browser_is_refused_without_the_explicit_flag_and_nothing_opens(world):
    job, seen = world
    r = CliRunner().invoke(main.app, ["submit", str(job), "acme", "--browser-mode", "desktop"])
    assert r.exit_code == 2 and "--allow-desktop-submit" in r.output
    assert seen["modes"] == [] and seen["run"] == []                                     # no browser, no filling, no review prompt
    r = CliRunner().invoke(main.app, ["submit", str(job), "acme", "--browser-mode", "auto", "--allow-desktop-submit"])
    assert r.exit_code == 2 and seen["modes"] == []
    get_settings.cache_clear()


def test_submit_continues_in_the_desktop_session_that_holds_the_application(world):
    job, seen = world
    r = CliRunner().invoke(main.app, ["submit", str(job), "acme", "--browser-mode", "desktop", "--allow-desktop-submit",
                                      "--desktop-allow-origin", "https://login.example.test"])
    assert r.exit_code == 0, r.output
    mode, url, related = seen["modes"][0]
    assert mode == "desktop" and url == "https://e.test/a" and "https://login.example.test" in related
    k = seen["run"][0]
    assert k["already_open"] is True and k["browser_policy"] == "desk-policy" and callable(k["at_review"])
    assert seen["hook"]                                                                  # the same approval hook, on the desktop page
    get_settings.cache_clear()


def test_apply_passes_the_same_session_options_and_still_never_submits(world):
    job, seen = world
    r = CliRunner().invoke(main.app, ["apply", str(job), "acme", "--browser-mode", "desktop"])
    assert r.exit_code == 0, r.output
    k = seen["run"][0]
    assert k["already_open"] is True and k["observe_s"] == 30 and "at_review" not in k and seen["hook"] == []
    r = CliRunner().invoke(main.app, ["apply", str(job), "acme", "--submit"])
    assert r.exit_code != 0                                                              # filling never becomes sending
    r = CliRunner().invoke(main.app, ["apply", str(job), "acme", "--browser-mode", "firefox"])
    assert r.exit_code == 2
    get_settings.cache_clear()


# --- the queue ------------------------------------------------------------------------------------------------------------

def queue_world(monkeypatch, tmp_path):
    from jobagent import cli_discovery as C
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.setenv("OUTPUTS_DIR", str(tmp_path / "outputs"))
    get_settings.cache_clear()
    conn = db.connect()
    Q.enqueue(conn, Q.Listing("Acme", "Engineer", "https://e.test/jobs/1", ["Austin, TX"], req_id="1"),
              Match("queued", "regex", "swe", 0.9), Decision("us", "t", "Austin"))
    with conn:
        conn.execute("UPDATE jobs SET job_id = '1', company = 'Acme' WHERE req_id = '1'")
    cmds = []
    from jobagent.tools.pipeline import Pipeline, PipelineResult
    def run(self, url, *, through, browser_mode, **kwargs):
        cmds.append([through, "--browser-mode", browser_mode])
        return PipelineResult("filled", str(tmp_path / "outputs" / "acme" / "1"))
    monkeypatch.setattr(Pipeline, "run", run)
    import typer
    from rich.console import Console
    app = typer.Typer()
    C.register(app, Console(width=200))
    return app, cmds


def test_the_queue_hands_its_browser_mode_to_apply_and_never_to_anything_that_sends(monkeypatch, tmp_path):
    app, cmds = queue_world(monkeypatch, tmp_path)
    r = CliRunner().invoke(app, ["queue", "run", "--through", "apply", "--limit", "1", "--browser-mode", "auto"])
    assert r.exit_code == 0, r.output
    apply_cmd = next(c for c in cmds if c[0] == "apply")
    assert apply_cmd[-2:] == ["--browser-mode", "auto"]
    assert not any(c[0] == "submit" for c in cmds)
    get_settings.cache_clear()


def test_the_queue_default_is_docker_and_a_bad_mode_is_refused(monkeypatch, tmp_path):
    app, cmds = queue_world(monkeypatch, tmp_path)
    CliRunner().invoke(app, ["queue", "run", "--through", "apply", "--limit", "1"])
    assert next(c for c in cmds if c[0] == "apply")[-2:] == ["--browser-mode", "docker"]
    cmds.clear()
    r = CliRunner().invoke(app, ["queue", "run", "--through", "apply", "--browser-mode", "netscape"])
    assert r.exit_code == 2 and cmds == []
    get_settings.cache_clear()
