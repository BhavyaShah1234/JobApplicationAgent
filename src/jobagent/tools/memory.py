"""Per-portal notes the agent keeps between visits: state/portals/<portal>.json (+ a readable .md)."""

import json
import re
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


class PortalMemory:
    def __init__(self, key: str, root: Path | None = None):
        self.key = key
        self.root = root or get_settings().state_dir / "portals"
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / f"{re.sub(r'[^a-z0-9.-]', '_', key)}.json"
        self.data: dict = json.loads(self.path.read_text()) if self.path.exists() else {
            "portal": key, "domains": [], "content_selector": None, "has_jsonld": None,
            "settle_ms": 2500, "visits": 0, "successes": 0, "notes": [],
        }

    @property
    def known(self) -> bool:
        return self.data["successes"] > 0

    def note(self, text: str) -> None:
        stamp = datetime.now(UTC).strftime("%Y-%m-%d")
        if not any(n.endswith(text) for n in self.data["notes"]):
            self.data["notes"].append(f"{stamp}: {text}")
            self.data["notes"] = self.data["notes"][-30:]

    def record_visit(self, url: str, ok: bool, **facts) -> None:
        host = urlparse(url).netloc.lower()
        if host and host not in self.data["domains"]:
            self.data["domains"].append(host)
        self.data["visits"] += 1
        self.data["successes"] += int(ok)
        self.data.update({k: v for k, v in facts.items() if v is not None})
        self.data["last_visit"] = datetime.now(UTC).isoformat(timespec="seconds")
        self.save()

    def save(self) -> None:
        self.path.write_text(json.dumps(self.data, indent=2))
        lines = [f"# Portal: {self.key}", "", f"- domains: {', '.join(self.data['domains'])}",
                 f"- job description selector: `{self.data['content_selector']}`",
                 f"- schema.org JobPosting JSON-LD: {self.data['has_jsonld']}",
                 f"- settle time: {self.data['settle_ms']} ms",
                 f"- visits: {self.data['visits']} (successful scrapes: {self.data['successes']})", "", "## Notes", ""]
        lines += [f"- {n}" for n in self.data["notes"]] or ["- (none yet)"]
        self.path.with_suffix(".md").write_text("\n".join(lines) + "\n")
