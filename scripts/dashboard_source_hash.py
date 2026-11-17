"""A fingerprint of the dashboard front end's sources (frontend/src without tests, index.html, package-lock.json, vite.config.js). The build
records it in src/jobagent/dashboard/static/source.sha256; a test compares it, so a source change without a rebuild is caught."""
import hashlib
from pathlib import Path


def fingerprint(root: Path) -> str:
    fe = root / "frontend"
    files = sorted(p for p in (fe / "src").rglob("*") if p.is_file() and ".test." not in p.name and "/test/" not in p.as_posix())
    files += [fe / "index.html", fe / "package-lock.json", fe / "vite.config.js"]
    h = hashlib.sha256()
    for p in files:
        h.update(p.relative_to(fe).as_posix().encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()


if __name__ == "__main__":
    print(fingerprint(Path(__file__).resolve().parents[1]))
