"""Per-portal notes the agent keeps between visits: state/portals/<portal>.json (+ a readable .md)."""

import json
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from jobagent import db
from jobagent.config import get_settings

# Hosted ATS families → a stable portal key; anything else is keyed by its registrable domain.
KNOWN_PORTALS = {
    "greenhouse": re.compile(r"(^|\.)greenhouse\.io$"),
    "lever": re.compile(r"(^|\.)lever\.co$"),
    "workday": re.compile(r"(^|\.)myworkdayjobs\.com$|(^|\.)myworkdaysite\.com$"),
    "ashby": re.compile(r"(^|\.)ashbyhq\.com$"),
    "icims": re.compile(r"(^|\.)icims\.com$"),
    "smartrecruiters": re.compile(r"(^|\.)smartrecruiters\.com$"),
    "successfactors": re.compile(r"(^|\.)successfactors\.(com|eu)$"),
    "taleo": re.compile(r"(^|\.)taleo\.net$"),
    "jobvite": re.compile(r"(^|\.)jobvite\.com$"),
}


def portal_key(url: str) -> str:
    host = urlparse(url).netloc.lower()
    for key, pattern in KNOWN_PORTALS.items():
        if pattern.search(host):
            return key
    return db.registrable_domain(host)


# Parallel scrapes can hit the same portal: updates re-read the file and merge under this lock.
_LOCK = threading.Lock()


class PortalMemory:
    def __init__(self, key: str, root: Path | None = None):
        self.key = key
        self.root = root or get_settings().state_dir / "portals"
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / f"{re.sub(r'[^a-z0-9.-]', '_', key)}.json"
        self.data: dict = self._load()
        self._pending_notes: list[str] = []

    def _load(self) -> dict:
        if self.path.exists():
            return json.loads(self.path.read_text())
        return {"portal": self.key, "domains": [], "content_selector": None, "has_jsonld": None,
                "settle_ms": 2500, "visits": 0, "successes": 0, "notes": []}

    @property
    def known(self) -> bool:
        return self.data["successes"] > 0

    def note(self, text: str) -> None:
        """Queued until record_visit(), which merges it into the latest file contents."""
        if not any(n.endswith(text) for n in self.data["notes"] + self._pending_notes):
            self._pending_notes.append(f"{datetime.now(UTC):%Y-%m-%d}: {text}")

    def record_visit(self, url: str, ok: bool, **facts) -> None:
        host = urlparse(url).netloc.lower()
        with _LOCK:
            data = self._load()  # another worker may have updated it meanwhile
            if host and host not in data["domains"]:
                data["domains"].append(host)
            data["visits"] += 1
            data["successes"] += int(ok)
            data.update({k: v for k, v in facts.items() if v is not None})
            for n in self._pending_notes:
                if not any(existing.endswith(n.split(": ", 1)[1]) for existing in data["notes"]):
                    data["notes"].append(n)
            data["notes"] = data["notes"][-30:]
            data["last_visit"] = datetime.now(UTC).isoformat(timespec="seconds")
            self.data, self._pending_notes = data, []
            self.save()

    def save(self) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.data, indent=2))
        tmp.replace(self.path)
        lines = [f"# Portal: {self.key}", "", f"- domains: {', '.join(self.data['domains'])}",
                 f"- job description selector: `{self.data['content_selector']}`",
                 f"- schema.org JobPosting JSON-LD: {self.data['has_jsonld']}",
                 f"- settle time: {self.data['settle_ms']} ms",
                 f"- visits: {self.data['visits']} (successful scrapes: {self.data['successes']})", "", "## Notes", ""]
        lines += [f"- {n}" for n in self.data["notes"]] or ["- (none yet)"]
        self.path.with_suffix(".md").write_text("\n".join(lines) + "\n")
