"""Docstring audit for src/jobagent (step 20): which public modules, classes, functions and methods have no docstring, and whether the ones that
exist follow the Google style the project uses (Args / Returns / Raises sections where there is something to say).

    python scripts/docstring_audit.py                 # a summary and the worst files
    python scripts/docstring_audit.py --list FILE     # every public name in FILE without a docstring
    python scripts/docstring_audit.py --check         # exit 1 if coverage fell below docs/docstring_baseline.json (a ratchet: it may only go up)
    python scripts/docstring_audit.py --write-baseline

"Public" means a name without a leading underscore (dunder methods other than __init__ are skipped). A function whose body is a single expression of
two lines or fewer is allowed to have a one-line docstring; a function with parameters or a non-trivial return should also say so in Args / Returns.
"""
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "jobagent"
BASELINE = ROOT / "docs" / "docstring_baseline.json"


def public(name: str) -> bool:
    return not name.startswith("_") or name == "__init__"


def scan() -> dict:
    files: dict[str, dict] = {}
    for p in sorted(SRC.rglob("*.py")):
        if "static" in p.parts:
            continue
        tree = ast.parse(p.read_text())
        missing, total, styled_missing = [], 0, []
        if not ast.get_docstring(tree):
            missing.append("<module>")
        total += 1
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and public(node.name) and node.name != "__init__":
                total += 1
                doc = ast.get_docstring(node)
                if not doc:
                    missing.append(f"{node.name}:{node.lineno}")
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    params = [a.arg for a in node.args.args + node.args.kwonlyargs if a.arg not in ("self", "cls")]
                    size = (node.end_lineno or node.lineno) - node.lineno
                    if params and size > 12 and "Args:" not in doc:
                        styled_missing.append(f"{node.name}:{node.lineno}")
        files[str(p.relative_to(ROOT))] = {"total": total, "missing": missing, "no_args_section": styled_missing}
    return files


def summary(files: dict) -> dict:
    total = sum(f["total"] for f in files.values())
    missing = sum(len(f["missing"]) for f in files.values())
    return {"public_items": total, "without_docstring": missing, "covered_percent": round(100 * (total - missing) / max(total, 1), 1),
            "long_functions_without_args_section": sum(len(f["no_args_section"]) for f in files.values())}


def main(argv: list[str]) -> int:
    files = scan()
    s = summary(files)
    if "--list" in argv:
        target = argv[argv.index("--list") + 1]
        for rel, f in files.items():
            if rel.endswith(target):
                print("\n".join(f["missing"]) or "nothing missing")
        return 0
    if "--write-baseline" in argv:
        BASELINE.write_text(json.dumps({"per_file_missing": {k: len(v["missing"]) for k, v in files.items() if v["missing"]}, **s}, indent=1) + "\n")
        print("baseline written:", s)
        return 0
    if "--check" in argv:
        base = json.loads(BASELINE.read_text())
        worse = {k: (base["per_file_missing"].get(k, 0), len(v["missing"])) for k, v in files.items() if len(v["missing"]) > base["per_file_missing"].get(k, 0)}
        for k, (was, now) in worse.items():
            print(f"more undocumented names in {k}: {was} -> {now}")
        print(s)
        return 1 if worse else 0
    print(json.dumps(s, indent=1))
    for rel, f in sorted(files.items(), key=lambda kv: -len(kv[1]["missing"]))[:15]:
        print(f"{len(f['missing']):4d} of {f['total']:<4d} {rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
