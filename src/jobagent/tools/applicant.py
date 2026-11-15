"""The candidate's answers for application forms: `data/applicant.yaml`, one file of questions (with the wordings
portals use), answers, instructions and lookups, plus what the candidate answered on the spot (`learned`).

The experience answers (work history, current company, skills, years, internships) come from the job's tailored
resume when a form is filled (`with_resume`), so the form says what the uploaded resume says.
"""

import re
from datetime import date, datetime
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
    """The answer file, without any `learned` section (what the agent learns lives in data/learned.yaml)."""
    data = yaml.safe_load(text()) or {}
    data.pop("learned", None)
    return data


def identity_aliases(data: dict | None = None) -> dict[str, list[str]]:
    """{a school's / employer's real name: [the other forms the candidate has APPROVED for it]} from every record in the file that
    has an `aliases:` list beside a `school`, `institution`, `company` or `employer` name. Only what the candidate wrote counts:
    a campus form ("Arizona State University - Tempe") is the same institution only if it is listed here."""
    data = load() if data is None else data
    out: dict[str, list[str]] = {}

    def walk(x) -> None:
        if isinstance(x, dict):
            name = next((x[k] for k in ("school", "institution", "university", "company", "employer") if isinstance(x.get(k), str) and x[k]), None)
            if name and isinstance(x.get("aliases"), list):
                out.setdefault(name, []).extend(str(a) for a in x["aliases"] if a)
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(data)
    return out


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
    return {"first": legal.get("first", ""), "middle": legal.get("middle", ""), "last": legal.get("last", ""),
            "preferred_first": pref.get("first", "")}


def phone() -> str:
    p = answer("Phone")
    return str(p.get("number", "") if isinstance(p, dict) else (p or ""))


def degrees(data: dict | None = None) -> list[dict]:
    d = answer("Degrees", data)
    return d if isinstance(d, list) else []


def _tex(t: str) -> str:
    t = re.sub(r"\\(underline|textbf|textit|emph|href\{[^}]*\})\{(.*?)\}", r"\2", t)
    t = t.replace("\\&", "&").replace("\\%", "%").replace("\\$", "$").replace("\\_", "_").replace("--", "-")
    return re.sub(r"\\[a-zA-Z]+\*?|[{}]|\$", "", t).strip()


def _month(text: str, end: bool = False) -> date | None:
    text = text.strip()
    if re.match(r"(?i)present|current|now", text):
        return date.today()
    for fmt in ("%B %Y", "%b %Y", "%b. %Y", "%m/%Y", "%Y"):
        try:
            d = datetime.strptime(text, fmt).date()
            return d.replace(month=12) if fmt == "%Y" and end else d
        except ValueError:
            continue
    return None


def resume_experience(tex: str) -> dict:
    """What the tailored resume says about experience: its roles (with bullets), the current company, its skills,
    the years its roles cover (overlaps counted once) and how many roles are internships."""
    sec = re.search(r"\\section\{Experience\}(.*?)(?=\\section\{|\\end\{document\})", tex, re.S)
    roles = []
    if sec:
        parts = re.split(r"\\resumeSubheading\s*", sec.group(1))[1:]
        for part in parts:
            m = re.match(r"\{(.*?)\}\{(.*?)\}\s*\{(.*?)\}\{(.*?)\}", part, re.S)
            if not m:
                continue
            title, dates, company, location = (_tex(x) for x in m.groups())
            start, _, stop = dates.partition(" - ")
            bullets = [_tex(b) for b in re.findall(r"\\resumeItem\{(.*?)\}\s*$", part, re.M)]
            roles.append({"title": title, "company": company, "location": location, "from": start.strip(),
                          "to": stop.strip(), "current": bool(re.match(r"(?i)present|current", stop.strip())),
                          "description": " ".join(bullets)})
    months = set()
    for r in roles:
        a, b = _month(r["from"]), _month(r["to"], end=True)
        if a and b:
            months |= {(y, m) for y in range(a.year, b.year + 1) for m in range(1, 13)
                       if (a.year, a.month) <= (y, m) <= (b.year, b.month)}
    sk = re.search(r"\\section\{Technical Skills\}(.*?)(?=\\section\{|\\end\{document\})", tex, re.S)
    skills = []
    if sk:
        for line in re.findall(r"\{:\s*(.*?)\}", sk.group(1)):
            skills += [x.strip() for x in _tex(line).split(",") if x.strip()]
    return {"roles": roles, "current_company": next((r["company"] for r in roles if r["current"]), ""),
            "skills": list(dict.fromkeys(skills)), "years": round(len(months) / 12, 1),
            "internships": sum(1 for r in roles if re.search(r"(?i)\bintern(ship)?\b", r["title"]))}


def with_resume(data: dict, exp: dict) -> dict:
    """The answer file with its experience answers taken from the job's tailored resume."""
    if not exp.get("roles"):
        return data
    values = {"Work history": exp["roles"], "Current company": exp["current_company"],
              "Skills": exp["skills"], "Years of relevant experience": exp["years"],
              "Number of internships": exp["internships"]}
    for _, e in entries(data):
        if e["question"] in values:
            e["answer"] = values[e["question"]]
            e["source"] = "the job's tailored resume"
    return data


def learn(question: str, options: list[str], ans: str, company: str, portal: str, rule: str = "") -> dict:
    """Keep the candidate's on-the-spot reply in data/learned.yaml (applicant.yaml is only ever edited by them)."""
    from jobagent.tools import learned
    return learned.add(question, options, ans, company, portal, rule=rule)
