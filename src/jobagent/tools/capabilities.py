"""Lets the agent record harness gaps; Claude Code reads the file to update the Dockerfile.

The agent itself can never modify the image: the Dockerfile and source aren't mounted writable.
"""

from datetime import UTC, datetime
from pathlib import Path

HEADER = (
    "# Capabilities the agent needed but the harness image lacks.\n"
    "# Format: <timestamp> | <name> | <why> | <install hint>\n"
    "# Read by Claude Code when updating the Dockerfile; mark handled lines with a leading '# done '.\n"
)


def report_missing_capability(name: str, why: str, install_hint: str = "", path: Path | None = None) -> bool:
    """Append a missing capability once. Returns False if it was already reported (and not yet handled)."""
    if path is None:
        from jobagent.config import get_settings

        path = get_settings().missing_capabilities_path
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text() if path.exists() else HEADER
    for line in existing.splitlines():
        if not line.startswith("#") and line.split(" | ")[1:2] == [name]:
            return False
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    clean = lambda v: v.replace("|", "/").replace("\n", " ").strip()  # noqa: E731
    path.write_text(existing + f"{stamp} | {clean(name)} | {clean(why)} | {clean(install_hint)}\n")
    return True
