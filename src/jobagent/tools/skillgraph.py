"""The skill graph (step 4c): Role -> Duty -> Skill, many to many, persisted and versioned in state/skills/.

Roles are the entries of data/target_roles.yaml. A DUTY is a kind of work ("model training", "model deployment and hosting",
"databases") that a posting's responsibilities describe; a SKILL is a technology a posting names (PyTorch, Triton Inference Server,
PostgreSQL). One posting adds: role -> each duty it asks for -> each skill used for that duty. Two skills are neighbours when they share a
duty, ranked by how many postings support the link, so the graph needs no hand-written skill-to-skill edges and grows with every
posting the agent reads (`ingest`) and with every new role in the roles file (`build`, `jobagent skills sync`).

Use in tailoring (`suggest`): from the skills a posting names, the neighbours under the same duties. Neighbours the candidate OWNS (their
template, master profile, GitHub repo stacks, approved skills) may fill spare room on the resume; every other neighbour is only listed for
review. The graph never decides what is claimed beyond that: it adds nothing the candidate has not got.

Everything that needs a model takes it as a parameter (`ask`, `embed`), so the logic is tested without one.
"""

import hashlib
import json
import math
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

CATEGORIES = ("language", "framework", "database", "cloud", "tool", "method")
GENERAL_DUTY = "general tooling"          # skills a posting names without tying them to a duty

# spellings that are the same technology; extended automatically when two names merge by similarity
BUILTIN_ALIASES = {
    "js": "javascript", "ts": "typescript", "k8s": "kubernetes", "tf": "tensorflow", "torch": "pytorch", "postgres": "postgresql",
    "psql": "postgresql", "golang": "go", "mongo": "mongodb", "node": "nodejs", "node.js": "nodejs", "reactjs": "react",
    "react.js": "react", "vuejs": "vue", "vue.js": "vue", "nextjs": "next.js", "sklearn": "scikit-learn", "scikit learn": "scikit-learn",
    "hf": "huggingface", "hugging face": "huggingface", "huggingface transformers": "huggingface", "gcp": "google cloud",
    "google cloud platform": "google cloud", "amazon web services": "aws", "ci/cd": "cicd", "c plus plus": "c++", "cpp": "c++",
    "open cv": "opencv", "triton": "triton inference server", "nvidia triton": "triton inference server",
}


# --- names -------------------------------------------------------------------------------------------------------------------------

def norm(term: str) -> str:
    """A comparison key: lower case, no parenthesised detail, no version number, one spelling per known alias."""
    t = re.sub(r"\([^)]*\)", " ", str(term or "")).lower().strip()
    t = re.sub(r"\b(v|version)?\s*\d+(\.\d+)*\+?$", "", t).strip(" -,;:/")
    t = re.sub(r"\s+", " ", t)
    return BUILTIN_ALIASES.get(t, t)


def clean_skills(terms: list[str]) -> list[str]:
    """A posting's technical_skills as individual skills: comma/slash lists split, junk and long phrases dropped, duplicates by `norm` removed."""
    out, seen = [], set()
    for raw in terms or []:
        for part in re.split(r",|;|\band\b(?= [A-Z])|\|", str(raw)):
            part = part.strip(" .-•")
            key = norm(part)
            if not key or len(key) > 40 or len(key.split()) > 5 or key in seen:
                continue
            seen.add(key)
            out.append(part)
    return out


def jd_key(jd: dict) -> str:
    """Identity of a posting in the graph: its address, else title + company + requisition."""
    basis = (jd.get("source_url") or "").strip() or "|".join(str(jd.get(k) or "") for k in ("title", "company", "req_id"))
    return hashlib.sha1(basis.lower().encode()).hexdigest()[:16]


# --- the graph ---------------------------------------------------------------------------------------------------------------------

class Graph:
    """nodes {id: {id, kind, name, aliases, category, sources}}; edges {"src>dst": {src, dst, weight, postings}}; postings {key: role}."""

    def __init__(self, data: dict | None = None):
        data = data or {}
        self.version: int = data.get("version", 0)
        self.nodes: dict = data.get("nodes", {})
        self.edges: dict = data.get("edges", {})
        self.postings: dict = data.get("postings", {})

    def to_dict(self) -> dict:
        return {"version": self.version, "nodes": self.nodes, "edges": self.edges, "postings": self.postings}

    def content_hash(self) -> str:
        d = self.to_dict()
        d.pop("version")
        return hashlib.sha1(json.dumps(d, sort_keys=True).encode()).hexdigest()

    # lookup
    @staticmethod
    def node_id(kind: str, name: str) -> str:
        return f"{kind}:{norm(name)}"

    def find(self, kind: str, name: str) -> str | None:
        nid = self.node_id(kind, name)
        if nid in self.nodes:
            return nid
        key = norm(name)
        for n in self.nodes.values():                     # a recorded alias
            if n["kind"] == kind and key in {norm(a) for a in n["aliases"]}:
                return n["id"]
        return None

    def of_kind(self, kind: str) -> list[dict]:
        return [n for n in self.nodes.values() if n["kind"] == kind]

    # change
    def add(self, kind: str, name: str, category: str = "", source: str = "") -> str:
        nid = self.node_id(kind, name)
        n = self.nodes.setdefault(nid, {"id": nid, "kind": kind, "name": name.strip(), "aliases": [], "category": "", "sources": []})
        if category in CATEGORIES and not n["category"]:
            n["category"] = category
        if source and source not in n["sources"]:
            n["sources"].append(source)
        return nid

    def alias(self, nid: str, name: str) -> None:
        n = self.nodes[nid]
        if name.strip() and norm(name) != norm(n["name"]) and name.strip() not in n["aliases"]:
            n["aliases"].append(name.strip())

    def link(self, src: str, dst: str, posting: str) -> None:
        e = self.edges.setdefault(f"{src}>{dst}", {"src": src, "dst": dst, "weight": 0, "postings": []})
        if posting not in e["postings"]:
            e["postings"].append(posting)
            e["weight"] = len(e["postings"])

    def children(self, nid: str) -> list[tuple[str, int]]:
        return [(e["dst"], e["weight"]) for e in self.edges.values() if e["src"] == nid]

    def parents(self, nid: str) -> list[tuple[str, int]]:
        return [(e["src"], e["weight"]) for e in self.edges.values() if e["dst"] == nid]

    def merge(self, keep: str, drop: str) -> None:
        """Fold node `drop` into `keep`: its edges, aliases and sources move over; edge weights add up by posting."""
        if keep == drop or keep not in self.nodes or drop not in self.nodes:
            return
        d, k = self.nodes.pop(drop), self.nodes[keep]
        for a in [d["name"], *d["aliases"]]:
            self.alias(keep, a)
        k["sources"] = sorted(set(k["sources"]) | set(d["sources"]))
        k["category"] = k["category"] or d["category"]
        for key in [key for key, e in self.edges.items() if drop in (e["src"], e["dst"])]:
            e = self.edges.pop(key)
            src, dst = (keep if e["src"] == drop else e["src"]), (keep if e["dst"] == drop else e["dst"])
            if src != dst:
                for p in e["postings"]:
                    self.link(src, dst, p)


# --- persistence -------------------------------------------------------------------------------------------------------------------

def skills_dir(settings=None) -> Path:
    from jobagent.config import get_settings
    return (settings or get_settings()).state_dir / "skills"


def load(settings=None) -> Graph:
    p = skills_dir(settings) / "graph.json"
    try:
        return Graph(json.loads(p.read_text())) if p.exists() else Graph()
    except (OSError, json.JSONDecodeError):
        return Graph()


def save(g: Graph, why: str, before: str | None = None, settings=None) -> bool:
    """Write the graph when its content changed since `before` (a `content_hash`): the version goes up and one history line records why."""
    if before is not None and g.content_hash() == before:
        return False
    d = skills_dir(settings)
    d.mkdir(parents=True, exist_ok=True)
    g.version += 1
    tmp = d / "graph.json.tmp"
    tmp.write_text(json.dumps(g.to_dict(), indent=1, ensure_ascii=False))
    tmp.replace(d / "graph.json")
    with open(d / "history.jsonl", "a", encoding="utf-8") as h:
        h.write(json.dumps({"ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "version": g.version, "why": why,
                            "nodes": len(g.nodes), "edges": len(g.edges)}) + "\n")
    return True


# --- similarity --------------------------------------------------------------------------------------------------------------------

def cosine(a: list[float], b: list[float]) -> float:
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b, strict=False)) / (na * nb) if na and nb else 0.0


def default_embed(settings=None) -> Callable[[list[str]], list[list[float]] | None]:
    """Embeddings cached in state/skills/vectors.json by embedding model and text; vectors of a fallback model are used once, never cached
    (the same rule as tools/learned.py). None when the model cannot be reached: callers then match by name only."""
    from jobagent import llm
    from jobagent.config import get_settings
    s = settings or get_settings()
    path = skills_dir(s) / "vectors.json"

    def embed(texts: list[str]):
        try:
            cache = json.loads(path.read_text()) if path.exists() else {}
        except (OSError, json.JSONDecodeError):
            cache = {}
        key = lambda t: hashlib.sha1(f"{s.model_embed}|{t}".encode()).hexdigest()  # noqa: E731
        missing = [t for t in dict.fromkeys(texts) if key(t) not in cache]
        if missing:
            try:
                emb = llm.embeddings()
                vecs = emb.embed_documents(missing)
            except Exception:
                return None
            if emb.used_fallback:
                got = dict(zip(missing, vecs, strict=False))
                return [got.get(t) or cache.get(key(t)) for t in texts]
            cache.update({key(t): v for t, v in zip(missing, vecs, strict=False)})
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(cache))
        return [cache[key(t)] for t in texts]
    return embed


def resolve(g: Graph, kind: str, name: str, embed, threshold: float) -> str | None:
    """The existing node `name` means: by name or recorded alias, else the most similar node of the same kind at or above `threshold`
    (the spelling is then kept as an alias). None when it is new."""
    if (nid := g.find(kind, name)):
        return nid
    others = g.of_kind(kind)
    if not others or embed is None:
        return None
    vecs = embed([name] + [n["name"] for n in others])
    if not vecs or vecs[0] is None:
        return None
    best, score = None, 0.0
    for n, v in zip(others, vecs[1:], strict=False):
        if v is not None and (c := cosine(vecs[0], v)) > score:
            best, score = n["id"], c
    if best and score >= threshold:
        g.alias(best, name)
        return best
    return None


# --- ingesting postings ------------------------------------------------------------------------------------------------------------

class SkillOut(BaseModel):
    name: str = Field(max_length=60, description="Exactly one of the posting's technologies, spelled as the posting spells it")
    category: str = Field(default="tool", description="language, framework, database, cloud, tool or method")


class DutyOut(BaseModel):
    duty: str = Field(max_length=60, description="A kind of work in a few words, lower case, e.g. 'model training', 'model deployment "
                                                  "and hosting', 'databases', 'data pipelines', 'frontend development'")
    skills: list[SkillOut] = Field(max_length=25)


class IngestOut(BaseModel):
    duties: list[DutyOut] = Field(max_length=8)


class MergeGroup(BaseModel):
    keep: str
    merge: list[str]


class MergeOut(BaseModel):
    groups: list[MergeGroup] = Field(max_length=30)


INGEST_PROMPT = (
    "You organise the technologies of ONE job posting by the kind of work each is used for.\n"
    "Given the posting's responsibilities and its list of technologies, return the duties the posting asks for (at most 8) and, under each, "
    "the technologies from the LIST that serve it. A technology may serve several duties. Use only technologies from the LIST, spelled as "
    "listed; do not add any. Name duties as kinds of work, not as technologies. REUSE one of the KNOWN DUTIES (exact text) whenever it "
    "means the same work; make a new duty only for work none of them covers. Give each technology a category.")


def default_ask(jd: dict, known_duties: list[str], skills: list[str]) -> list[DutyOut]:
    """One structured planner-model call for one posting."""
    from langchain_core.messages import HumanMessage, SystemMessage

    from jobagent import llm
    body = {"title": jd.get("title"), "responsibilities": (jd.get("responsibilities") or [])[:25],
            "preferred_qualifications": (jd.get("preferred_qualifications") or [])[:10]}
    text = (f"KNOWN DUTIES: {json.dumps(known_duties)}\n\nPOSTING:\n{json.dumps(body, ensure_ascii=False)}\n\nLIST: {json.dumps(skills)}")
    out = llm.structured_invoke(llm.chat("planner"), IngestOut, [SystemMessage(INGEST_PROMPT), HumanMessage(text)])
    return out.duties


def default_merge(duties: list[str]) -> list[MergeGroup]:
    from langchain_core.messages import HumanMessage, SystemMessage

    from jobagent import llm
    prompt = ("These are the duties collected from many job postings. Group the ones that mean the same kind of work (e.g. 'model training' "
              "and 'training machine learning models'); for each group give the duty to keep (one of the group, exact text) and the others to "
              "merge into it. Leave distinct duties out. Use only the given names.")
    return llm.structured_invoke(llm.chat("planner"), MergeOut, [SystemMessage(prompt), HumanMessage(json.dumps(duties))]).groups


def ensure_role(g: Graph, role_id: str, title: str = "") -> str:
    nid = f"role:{role_id}"
    n = g.nodes.setdefault(nid, {"id": nid, "kind": "role", "name": title or role_id, "aliases": [], "category": "", "sources": []})
    if title:
        n["name"] = title
    return nid


def ingest(g: Graph, jd: dict, role_id: str, *, ask=default_ask, embed=None, duty_threshold: float = 0.85, skill_threshold: float = 0.9,
           role_title: str = "") -> dict:
    """Add one posting. Idempotent per posting: a second call with the same posting changes nothing. Returns what changed."""
    key = jd_key(jd)
    if key in g.postings:
        return {"added_skills": [], "added_duties": [], "skipped": True}
    skills = clean_skills(jd.get("technical_skills") or [])
    role = ensure_role(g, role_id, role_title)
    g.postings[key] = role_id
    if not skills:
        return {"added_skills": [], "added_duties": [], "skipped": False}
    by_norm = {norm(s): s for s in skills}
    known = [n["name"] for n in g.of_kind("duty")]
    new_skills: list[str] = []
    new_duties: list[str] = []
    placed: set[str] = set()
    for d in ask(jd, known, skills):
        duty_name = d.duty.strip().lower()
        if not duty_name:
            continue
        duty = resolve(g, "duty", duty_name, embed, duty_threshold)
        if duty is None:
            duty = g.add("duty", duty_name, source=key)
            new_duties.append(duty_name)
        g.link(role, duty, key)
        for s in d.skills:
            if norm(s.name) not in by_norm:                        # only technologies the posting names
                continue
            before = g.find("skill", s.name)
            sid = resolve(g, "skill", s.name, embed, skill_threshold)
            if sid is None:
                sid = g.add("skill", by_norm[norm(s.name)], s.category, key)
            elif before is None:
                g.alias(sid, s.name)
            g.add("skill", g.nodes[sid]["name"], s.category, key)
            if before is None and sid not in {x for x in new_skills}:
                new_skills.append(sid)
            g.link(duty, sid, key)
            placed.add(norm(s.name))
    leftover = [by_norm[k] for k in by_norm if k not in placed]
    if leftover:
        duty = g.find("duty", GENERAL_DUTY) or g.add("duty", GENERAL_DUTY, source=key)
        g.link(role, duty, key)
        for name in leftover:
            sid = resolve(g, "skill", name, embed, skill_threshold) or g.add("skill", name, "tool", key)
            if sid not in new_skills and g.find("skill", name) is None:
                new_skills.append(sid)
            g.link(duty, sid, key)
    return {"added_skills": new_skills, "added_duties": new_duties, "skipped": False}


def consolidate(g: Graph, merge=default_merge) -> int:
    """One pass folding duties that mean the same work into one; returns how many were merged. Unknown names from the model are ignored."""
    names = {n["name"].lower(): n["id"] for n in g.of_kind("duty")}
    done = 0
    for grp in merge(sorted(names)):
        keep = names.get(grp.keep.strip().lower())
        for other in grp.merge:
            drop = names.get(other.strip().lower())
            if keep and drop and drop in g.nodes and keep != drop:
                g.merge(keep, drop)
                done += 1
    return done


def build(g: Graph, roles: list[dict], postings: list[tuple[dict, str]], *, ask=default_ask, embed=None, merge=default_merge,
          log=print, thresholds: tuple[float, float] = (0.85, 0.9)) -> dict:
    """Add every role of the roles file (even with no postings yet) and every saved posting, then consolidate the duties."""
    titles = {r["id"]: r.get("title", r["id"]) for r in roles if r.get("id")}
    for rid, title in titles.items():
        ensure_role(g, rid, title)
    stats = {"postings": 0, "skipped": 0, "failed": 0, "roles_without_postings": []}
    for jd, rid in postings:
        try:
            res = ingest(g, jd, rid, ask=ask, embed=embed, duty_threshold=thresholds[0], skill_threshold=thresholds[1],
                         role_title=titles.get(rid, ""))
        except Exception as exc:                                 # one posting that fails never stops the others; a rerun retries it
            g.postings.pop(jd_key(jd), None)
            stats["failed"] += 1
            log(f"skills: {jd.get('company', '')} - {jd.get('title', '')}: {type(exc).__name__}: {str(exc)[:100]}")
            continue
        stats["skipped" if res["skipped"] else "postings"] += 1
    if stats["postings"] and merge is not None:
        try:
            stats["merged_duties"] = consolidate(g, merge)
        except Exception as exc:
            log(f"skills: duty consolidation skipped: {type(exc).__name__}: {str(exc)[:100]}")
    have = set(g.postings.values())
    stats["roles_without_postings"] = [rid for rid in titles if rid not in have]
    return stats


# --- suggestions -------------------------------------------------------------------------------------------------------------------

def suggest(g: Graph, jd_skills: list[str], role_id: str, owned: set[str], on_resume: set[str], limit: int = 6) -> dict:
    """Neighbours of the posting's skills: skills under the same duties, scored by posting support. {"owned": [...], "review": [...]},
    each {skill, via (the duty), score}; `owned` and `on_resume` are `norm` keys. Skills the posting names or the resume has are left out."""
    mine = {sid for s in jd_skills if (sid := g.find("skill", s))}
    if not mine:
        return {"owned": [], "review": []}
    duty_score: dict[str, float] = {}
    for sid in mine:
        for duty, w in g.parents(sid):
            duty_score[duty] = duty_score.get(duty, 0.0) + w
    role = f"role:{role_id}"
    for duty, w in g.children(role) if role in g.nodes else []:
        if duty in duty_score:
            duty_score[duty] += 0.5 * w                           # the role's own duties count a little more
    total: dict[str, float] = {}
    best: dict[str, tuple[float, str]] = {}                       # skill -> (strongest single duty contribution, that duty)
    for duty, ds in duty_score.items():
        top = max((w for _, w in g.children(duty)), default=1)
        for sid, w in g.children(duty):
            if sid in mine or norm(g.nodes[sid]["name"]) in on_resume:
                continue
            part = ds * w / top
            total[sid] = total.get(sid, 0.0) + part
            if part > best.get(sid, (0.0, ""))[0]:
                best[sid] = (part, duty)
    scores = {sid: (total[sid], best[sid][1]) for sid in total}
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1][0], g.nodes[kv[0]]["name"]))
    out = {"owned": [], "review": []}
    for sid, (score, duty) in ranked:
        n = g.nodes[sid]
        names = {norm(n["name"])} | {norm(a) for a in n["aliases"]}
        item = {"skill": n["name"], "via": g.nodes[duty]["name"], "score": round(score, 2)}
        bucket = "owned" if names & owned else "review"
        if len(out[bucket]) < limit:
            out[bucket].append(item)
    return out


def owned_skills(resume_terms: list[str] | None = None, settings=None) -> set[str]:
    """`norm` keys of every skill the candidate's own files show: the template's Technical Skills (passed in), master_profile.yaml,
    the tech stacks of their GitHub repos, and skills they approved with `jobagent skills approve`."""
    from jobagent.config import get_settings
    s = settings or get_settings()
    out = {norm(t) for t in resume_terms or [] if t}
    try:
        import yaml
        prof = yaml.safe_load((s.data_dir / "master_profile.yaml").read_text()) or {}
        for items in (prof.get("skills") or {}).values():
            for it in items or []:
                out.add(norm(it.get("name", "") if isinstance(it, dict) else str(it)))
    except Exception:
        pass
    for p in (s.state_dir / "github" / "cards").glob("*.json"):
        try:
            out |= {norm(t) for t in json.loads(p.read_text()).get("tech_stack", []) or []}
        except (OSError, json.JSONDecodeError):
            continue
    try:
        out |= {norm(t) for t in json.loads((skills_dir(s) / "approved.json").read_text())}
    except (OSError, json.JSONDecodeError):
        pass
    out.discard("")
    return out


def approve(skill: str, settings=None) -> bool:
    """Record a skill the candidate confirms they have; it counts as owned from then on. (master_profile.yaml is the candidate's own
    file and is never rewritten by the agent, so approvals live in state/skills/approved.json.)"""
    d = skills_dir(settings)
    d.mkdir(parents=True, exist_ok=True)
    p = d / "approved.json"
    try:
        have = json.loads(p.read_text()) if p.exists() else []
    except json.JSONDecodeError:
        have = []
    if norm(skill) in {norm(x) for x in have}:
        return False
    p.write_text(json.dumps(sorted([*have, skill.strip()], key=str.lower), indent=1))
    return True


def mermaid(g: Graph, role: str | None = None) -> str:
    """The graph in Mermaid `graph` form (Role --> Duty --> Skill), optionally for one role id."""
    keep_duties = {c for c, _ in g.children(f"role:{role}")} if role else None
    ids = {}

    def ident(nid: str) -> str:
        return ids.setdefault(nid, f"n{len(ids)}")

    lines = ["graph LR"]
    shown: set[str] = set()
    for e in g.edges.values():
        src, dst = g.nodes[e["src"]], g.nodes[e["dst"]]
        if role and not ((src["kind"] == "role" and src["id"] == f"role:{role}") or (src["kind"] == "duty" and src["id"] in keep_duties)):
            continue
        for n in (src, dst):
            if n["id"] not in shown:
                shown.add(n["id"])
                label = n["name"].replace('"', "'")
                lines.append(f'    {ident(n["id"])}["{label}"]')
        lines.append(f"    {ident(src['id'])} --> {ident(dst['id'])}")
    return "\n".join(lines) + "\n"


# --- tailoring hooks ---------------------------------------------------------------------------------------------------------------

def job_suggestions(job_dir: Path | None, jd: dict, role_id: str, resume_terms: list[str], settings=None) -> dict:
    """Suggestions for one job; also written to <job_dir>/skill_suggestions.json for the review screen. {} when switched off, when the
    graph has nothing for this posting, or on any error (the graph never stops tailoring)."""
    try:
        from jobagent.config import get_settings
        s = settings or get_settings()
        if not s.skills_use_graph:
            return {}
        g = load(s)
        res = suggest(g, clean_skills(jd.get("technical_skills") or []), role_id, owned_skills(resume_terms, s),
                      {norm(t) for t in resume_terms}, s.skills_max_neighbours)
        if not (res["owned"] or res["review"]):
            return {}
        if job_dir is not None:
            (job_dir / "skill_suggestions.json").write_text(json.dumps(res, indent=1))
        return res
    except Exception:
        return {}


def for_tailor(job_dir: Path | None, jd, resume, settings=None) -> dict:
    """Suggestions for the posting being tailored (`jd` a JobDescription, `resume` the parsed template). The role comes from the queue's
    match for this posting's address; {} when there is none or on any error."""
    try:
        from jobagent import db
        from jobagent.tools import tailor
        terms = [t.strip() for _, items in resume.skills for t in tailor.to_plain(items).split(",") if t.strip()]
        row = db.connect().execute("SELECT role_id FROM jobs WHERE url = ?", (getattr(jd, "source_url", "") or "",)).fetchone()
        return job_suggestions(job_dir, jd.model_dump(), (row["role_id"] if row and row["role_id"] else ""), terms, settings)
    except Exception:
        return {}


def hint_text(res: dict) -> str:
    """The block for the writer's prompt: owned neighbours may fill spare room; the others must not be added."""
    if not res or not (res.get("owned") or res.get("review")):
        return ""
    lines = ["\n\nRELATED SKILLS (from the skill graph; advisory):"]
    if res.get("owned"):
        lines.append("- The candidate HAS these, closely related to the posting's skills; use them ONLY to fill spare room on a skills line: "
                     + "; ".join(f"{x['skill']} (via {x['via']})" for x in res["owned"]))
    if res.get("review"):
        lines.append("- Related but NOT in the candidate's files: do NOT add these: " + "; ".join(x["skill"] for x in res["review"]))
    return "\n".join(lines)


def drop_forbidden(skills: list, res: dict) -> tuple[list, list[str]]:
    """Remove from generated skill lines any related skill that was listed for review (not owned). A line is never emptied. Returns
    (lines, dropped names). `skills` are tailor.SkillLine objects."""
    banned = {norm(x["skill"]) for x in (res or {}).get("review", [])}
    if not banned:
        return skills, []
    dropped: list[str] = []
    for line in skills:
        keep = [t for t in line.terms if norm(t) not in banned]
        if keep and len(keep) < len(line.terms):
            dropped += [t for t in line.terms if norm(t) in banned]
            line.terms = keep
    return skills, dropped


def review_lines(job_dir: Path) -> list[str]:
    """Markdown for the review packet: related skills the candidate may want to confirm (empty list when none)."""
    try:
        res = json.loads((job_dir / "skill_suggestions.json").read_text())
    except (OSError, json.JSONDecodeError):
        return []
    if not res.get("review"):
        return []
    out = ["## Suggested skills (not added)", "",
           "Related to this posting's skills but not found in your files. Add one only if you really have it: "
           "`jobagent skills approve \"<skill>\"` (then the next tailoring may use it).", ""]
    out += [f"- {x['skill']} (via {x['via']})" for x in res["review"]]
    return out + [""]
