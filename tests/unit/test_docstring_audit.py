"""Step 20: the docstring ratchet. Coverage may go up, never down."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def audit():
    spec = importlib.util.spec_from_file_location("docstring_audit", ROOT / "scripts" / "docstring_audit.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_no_file_has_more_undocumented_public_names_than_the_baseline():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "docstring_audit.py"), "--check"], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_baseline_matches_a_real_count_of_the_tree():
    base = json.loads((ROOT / "docs" / "docstring_baseline.json").read_text())
    now = audit().summary(audit().scan())
    assert now["without_docstring"] <= base["without_docstring"], "coverage fell: document the new names or run --write-baseline after documenting"
    assert base["covered_percent"] >= 60


def test_the_audit_sees_what_it_should(tmp_path, monkeypatch):
    mod = audit()
    pkg = tmp_path / "src" / "jobagent"
    pkg.mkdir(parents=True)
    (pkg / "m.py").write_text('"""Module."""\n\ndef documented():\n    """Yes."""\n\ndef bare():\n    pass\n\ndef _private():\n    pass\n\nclass K:\n    def method(self):\n        pass\n')
    monkeypatch.setattr(mod, "SRC", pkg)
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    f = mod.scan()["src/jobagent/m.py"]
    assert [m.split(":")[0] for m in f["missing"]] == ["bare", "K", "method"] or sorted(m.split(":")[0] for m in f["missing"]) == ["K", "bare", "method"]
    assert f["total"] == 5                                                      # the module, documented, bare, K, method; the private one is not counted
