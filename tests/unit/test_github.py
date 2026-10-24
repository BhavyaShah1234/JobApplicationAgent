"""GitHub helpers that need no network: resume header username, device-verification page detection, repo cache."""

import json
from datetime import UTC, datetime, timedelta

from jobagent.config import get_settings
from jobagent.tools import github


def test_profile_user_from_resume_header():
    tex = r"\faGithub$/$\href{https://github.com/BhavyaShah1234}{\underline{BhavyaShah1234}}"
    assert github.profile_user(tex) == "BhavyaShah1234"
    assert github.profile_user("no link here") == ""


def test_device_verification_page_detected():
    assert github._DEVICE.search("Device verification  Email  We just sent your authentication code via email")
    assert not github._DEVICE.search("Sign in to GitHub  Username or email address  Password")


def test_discover_repos_uses_fresh_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    get_settings.cache_clear()
    repo = github.Repo("u/x", "https://github.com/u/x", readme="hello")
    (tmp_path / "github").mkdir()
    (tmp_path / "github" / "u.json").write_text(json.dumps(
        {"fetched_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(), "repos": [repo.__dict__]}))
    assert github.discover_repos("u") == [repo]  # no network: served from the cache
    get_settings.cache_clear()
