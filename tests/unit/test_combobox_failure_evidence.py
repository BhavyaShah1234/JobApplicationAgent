"""A long widget error must not discard the network evidence needed to diagnose it."""
import json
from types import SimpleNamespace

from jobagent.tools import forms


def test_combobox_failure_retains_structured_evidence_after_short_error_cap(tmp_path, monkeypatch):
    monkeypatch.setattr('jobagent.config.get_settings', lambda: SimpleNamespace(state_dir=tmp_path))
    class Locator:
        def evaluate(self, script):
            return {'tag': 'INPUT', 'role': 'combobox', 'id': 'city', 'cls': '', 'html': '<div>' + 'x' * 2400 + '</div>'}
    evidence = {'menu_html': '<div role="listbox">' + 'x' * 1400 + '</div>',
                'input': {'value': 'San Ramon', 'focused': True},
                'network': [{'ev': 'response', 'status': 429, 'url': 'https://example.test/places'}]}
    forms._save_combobox_failure(Locator(), forms.Field(id=0, kind='combobox', label='City *'),
                                'San Ramon', 'long error ' * 100, evidence=evidence)
    row = json.loads((tmp_path / 'logs/combobox-failures.jsonl').read_text())
    assert len(row['error']) == 200
    assert row['evidence'] == evidence


def test_combobox_failure_keeps_old_callers_compatible(tmp_path, monkeypatch):
    monkeypatch.setattr('jobagent.config.get_settings', lambda: SimpleNamespace(state_dir=tmp_path))
    class Locator:
        def evaluate(self, script):
            return {'tag': 'A', 'html': '<a role="combobox">India</a>'}
    forms._save_combobox_failure(Locator(), forms.Field(id=0, kind='combobox', label='Country'),
                                'India', RuntimeError('no matching option'))
    row = json.loads((tmp_path / 'logs/combobox-failures.jsonl').read_text())
    assert row['label'] == 'Country' and row['error'] == 'no matching option'
    assert 'evidence' not in row
