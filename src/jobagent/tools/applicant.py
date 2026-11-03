"""The candidate's answers for application forms: `data/applicant.yaml`, one file of questions (with the wordings
portals use), answers, instructions and lookups, plus what the candidate answered on the spot (`learned`).

Older setups keep personal_info.yaml; its name and phone are still read when applicant.yaml is missing.
"""

from pathlib import Path

import yaml

from jobagent.clock import now
from jobagent.config import get_settings


def path() -> Path:
    return get_settings().data_dir / "applicant.yaml"


def text() -> str:
    p = path()
    return p.read_text() if p.exists() else ""


def load() -> dict:
    return yaml.safe_load(text()) or {}


def entries(data: dict | None = None) -> list[tuple[str, dict]]:
    """(section, entry) for every question in the file, learned answers included."""
    data = load() if data is None else data
    return [(sec, e) for sec, items in data.items() if isinstance(items, list)
            for e in items if isinstance(e, dict) and e.get("question")]


def answer(question: str, data: dict | None = None):
    """The answer of the entry with this question (exact wording), or None."""
    return next((e.get("answer") for _, e in entries(data) if e["question"] == question), None)


def name() -> dict:
    """{"first", "middle", "last", "preferred_first"}: legal name and the name the candidate goes by."""
    data = load()
    legal, pref = answer("Legal name", data) or {}, answer("Preferred name", data) or {}
    if not legal:  # an older setup: personal_info.yaml
        old = get_settings().data_dir / "personal_info.yaml"
        n = (yaml.safe_load(old.read_text()) or {}).get("name", {}) if old.exists() else {}
        return {"first": n.get("first", ""), "middle": n.get("middle", ""), "last": n.get("last", ""),
                "preferred_first": n.get("preferred_first", "")}
    return {"first": legal.get("first", ""), "middle": legal.get("middle", ""), "last": legal.get("last", ""),
            "preferred_first": pref.get("first", "")}


def phone() -> str:
    data = load()
    p = answer("Phone", data)
    if p is None:
        old = get_settings().data_dir / "personal_info.yaml"
        return str(((yaml.safe_load(old.read_text()) or {}).get("contact") or {}).get("phone", "")) if old.exists() else ""
    return str(p.get("number", "") if isinstance(p, dict) else p)


def degrees(data: dict | None = None) -> list[dict]:
    d = answer("Degrees", data)
    return d if isinstance(d, list) else []


def learn(question: str, options: list[str], ans: str, company: str, portal: str) -> None:
    """Append the candidate's on-the-spot answer to the `learned` section (the file's last section), keeping the
    rest of the file (and its comments) as written."""
    p = path()
    body = p.read_text() if p.exists() else "learned:\n"
    if "\nlearned:" not in "\n" + body:
        body = body.rstrip("\n") + "\n\nlearned:\n"
    item = {"question": question, "seen": [question], "answer": ans, "options": options[:30] or None,
            "company": company, "portal": portal, "date": now().strftime("%Y-%m-%d")}
    dumped = yaml.safe_dump([item], sort_keys=False, allow_unicode=True, width=120)
    p.write_text(body.rstrip("\n") + "\n" + "".join("  " + line + "\n" for line in dumped.splitlines()))
