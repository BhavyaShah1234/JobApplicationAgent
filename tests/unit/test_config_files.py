"""Step 21: config/*.yaml sits between .env and the defaults, and `describe` says where each value came from."""

from jobagent import config as C


def _settings(monkeypatch, tmp_path, yaml_text=None, env=None):
    (tmp_path / "queue.yaml").write_text(yaml_text or "")
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    return C.Settings()


def test_yaml_overrides_the_default(monkeypatch, tmp_path):
    s = _settings(monkeypatch, tmp_path, "pacing_max_per_company: 5\n")
    assert s.pacing_max_per_company == 5 and s.pacing_window_days == 7


def test_environment_beats_yaml(monkeypatch, tmp_path):
    s = _settings(monkeypatch, tmp_path, "pacing_max_per_company: 5\n", {"PACING_MAX_PER_COMPANY": "9"})
    assert s.pacing_max_per_company == 9


def test_describe_names_the_source_and_flags_unknown_keys(monkeypatch, tmp_path):
    s = _settings(monkeypatch, tmp_path, "pacing_max_per_company: 5\nnot_a_setting: 1\n", {"PACING_WINDOW_DAYS": "3"})
    rows = {r["key"]: r for r in C.describe(s)}
    assert rows["pacing_max_per_company"]["source"] == "yaml:queue.yaml"
    assert rows["pacing_window_days"]["source"] == "env"
    assert rows["queue_lease_seconds"]["source"] == "default"
    assert rows["not_a_setting"]["source"].startswith("unknown key")


def test_every_listed_setting_exists():
    assert all(k in C.Settings.model_fields for keys in C.CONCERN.values() for k in keys)


def test_environment_only_and_wrong_file_keys_are_not_read_from_yaml(monkeypatch, tmp_path):                  # R09
    (tmp_path / "models.yaml").write_text("state_dir: /elsewhere\nopenai_api_key: leaked\ndisplay: ':5'\npacing_window_days: 3\nmodel_writer: m\n")
    s = _settings(monkeypatch, tmp_path, "pacing_max_per_company: 4\n")
    assert str(s.state_dir) == "/app/state" and s.openai_api_key.get_secret_value() != "leaked" and s.display == ":99"
    assert s.pacing_window_days == 7 and s.model_writer == "m" and s.pacing_max_per_company == 4
    rows = {(r["concern"], r["key"]): r["source"] for r in C.describe(s)}
    for key in ("state_dir", "openai_api_key", "display", "pacing_window_days"):
        assert rows[("models", key)].startswith("rejected")
    assert "leaked" not in str(C.describe(s))


def test_nonsense_numbers_are_refused_with_the_field_name(monkeypatch, tmp_path):                            # R10
    import pytest
    from pydantic import ValidationError
    for text, field in (("queue_lease_seconds: -1", "queue_lease_seconds"), ("pacing_window_days: -7", "pacing_window_days")):
        with pytest.raises(ValidationError, match=field):
            _settings(monkeypatch, tmp_path, text + "\n")
    monkeypatch.setenv("APPLY_MAX_PAGES", "0")
    with pytest.raises(ValidationError, match="apply_max_pages"):
        _settings(monkeypatch, tmp_path, "")
    monkeypatch.delenv("APPLY_MAX_PAGES")
    ok = _settings(monkeypatch, tmp_path, "pacing_max_per_company: 0\nqueue_lease_seconds: 30\n")      # 0 = pacing off; the boundary is valid
    assert ok.pacing_max_per_company == 0 and ok.queue_lease_seconds == 30
