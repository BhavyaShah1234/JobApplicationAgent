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
import os
import re
import tempfile
import time
from contextlib import contextmanager
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal
from pathlib import Path

from pydantic import BaseModel, Field

CATEGORIES = ("language", "framework", "database", "cloud", "tool", "method")
GENERAL_DUTY = "general tooling"          # skills a posting names without tying them to a duty
# the kinds of work offered to the model first, so duties stay short and shared across postings (it may add a new one when none fits)
DEFAULT_DUTIES = (
    "model training", "model deployment and hosting", "data pipelines", "data analysis", "databases", "backend services", "api development",
    "frontend development", "cloud infrastructure", "devops and ci/cd", "testing and quality", "observability and monitoring",
    "distributed systems", "systems and embedded programming", "networking", "security", "computer vision",
    "natural language processing", "robotics and controls", "mobile development", "performance optimization")
MAX_DUTY_WORDS = 5
# nouns that make a phrase a kind of knowledge, not a technology ("backend languages", "optimization techniques", "predictive models")
GENERIC_SKILL = re.compile(r"(?i)\b(languages?|techniques|concepts|principles|fundamentals|experience|knowledge|skills?|various|different|"
                           r"other|related|similar|models|algorithms|methods|approaches|practices|tools|technologies|frameworks|platforms|"
                           r"systems|engineering|development|programming|solutions|applications|environments?|databases)\b")

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
            if not key or len(key) > 40 or len(key.split()) > 4 or key in seen or (len(key.split()) > 1 and GENERIC_SKILL.search(key)):
                continue
            seen.add(key)
            out.append(part)
    return out


NOT_A_DUTY = re.compile(r"(?i)^(software (development|engineering)|engineering|development|programming|coding|technical work|innovation)$|"
                        r"collaborat|communicat|mentor|leadership|teamwork|team work|stakeholder|agile|scrum|documentation|problem[- ]solving|"
                        r"innovation|troubleshoot|debugging")


def valid_duty(name: str) -> bool:
    """A duty is a short, specific kind of technical work ("model training"): not a sentence copied from a responsibility, not a catch-all
    ("software development") and not a soft skill ("team collaboration")."""
    words = name.strip().split()
    return 0 < len(words) <= MAX_DUTY_WORDS and len(name.strip()) <= 40 and not NOT_A_DUTY.search(name.strip())


def role_for(jd: dict, roles: list[dict], embed=None, threshold: float = 0.55) -> str:
    """The role a posting belongs to when the queue recorded none: the first role whose title pattern matches, else the role whose
    title + description is most similar to the posting's title and summary (at or above `threshold`); '' when none fits."""
    import re as _re
    title = jd.get("title") or ""
    for r in roles:
        for pat in r.get("patterns") or []:
            try:
                if _re.search(pat, title):
                    return r["id"]
            except _re.error:
                continue
    if embed is None or not roles:
        return ""
    texts = [f"{title}. {(jd.get('role_summary') or '')[:300]}"] + [f"{r.get('title', '')}. {r.get('description', '')}" for r in roles]
    vecs = embed(texts)
    if not vecs or vecs[0] is None:
        return ""
    scored = [(cosine(vecs[0], v), r["id"]) for r, v in zip(roles, vecs[1:], strict=False) if v is not None]
    best = max(scored, default=(0.0, ""))
    return best[1] if best[0] >= threshold else ""


def jd_key(jd: dict) -> str:
    """Identity of a posting in the graph: its address, else title + company + requisition."""
    basis = (jd.get("source_url") or "").strip() or "|".join(str(jd.get(k) or "") for k in ("title", "company", "req_id"))
    return hashlib.sha1(basis.lower().encode()).hexdigest()[:16]


def jd_fp(jd: dict, role_id: str = "") -> str:
    """What of a posting matters to the graph: its skills, responsibilities and the role it was placed in. A posting read again
    with a different fingerprint is re-ingested; with the same one it is skipped."""
    basis = {"skills": sorted(norm(s) for s in clean_skills(jd.get("technical_skills") or [])),
             "resp": [str(x) for x in (jd.get("responsibilities") or [])[:25]],
             "pref": [str(x) for x in (jd.get("preferred_qualifications") or [])[:10]], "role": role_id}
    return hashlib.sha1(json.dumps(basis, sort_keys=True).encode()).hexdigest()[:16]


# --- the graph ---------------------------------------------------------------------------------------------------------------------

class Graph:
    """nodes {id: {id, kind, name, aliases, category, sources}}; edges {"src>dst": {src, dst, weight, postings}}; postings {key: role}."""

    def __init__(self, data: dict | None = None):
        data = data or {}
        self.version: int = data.get("version", 0)
        self.nodes: dict = data.get("nodes", {})
        self.edges: dict = data.get("edges", {})
        # posting key -> {"role": role id, "fp": fingerprint of the content ingested}; older files stored just the role id
        self.postings: dict = {k: (v if isinstance(v, dict) else {"role": v, "fp": None}) for k, v in (data.get("postings") or {}).items()}

    def to_dict(self) -> dict:
        """The graph as plain data (version, nodes, edges, postings) ready for JSON."""
        return {"version": self.version, "nodes": self.nodes, "edges": self.edges, "postings": self.postings}

    def content_hash(self) -> str:
        """A hash of everything except the version, so a save can tell whether anything changed."""
        d = self.to_dict()
        d.pop("version")
        return hashlib.sha1(json.dumps(d, sort_keys=True).encode()).hexdigest()

    # lookup
    @staticmethod
    def node_id(kind: str, name: str) -> str:
        """The id a node of this kind and name gets: `<kind>:<normalised name>`."""
        return f"{kind}:{norm(name)}"

    def find(self, kind: str, name: str) -> str | None:
        """Look a node up by name or by a recorded alias.

        Args:
            kind: `role`, `duty` or `skill`.
            name: The name or an alias, in any spelling `norm` unifies.

        Returns:
            The node id, or None when the graph does not know it.
        """
        nid = self.node_id(kind, name)
        if nid in self.nodes:
            return nid
        key = norm(name)
        for n in self.nodes.values():                     # a recorded alias
            if n["kind"] == kind and key in {norm(a) for a in n["aliases"]}:
                return n["id"]
        return None

    def of_kind(self, kind: str) -> list[dict]:
        """All nodes of one kind (`role`, `duty` or `skill`)."""
        return [n for n in self.nodes.values() if n["kind"] == kind]

    # change
    def add(self, kind: str, name: str, category: str = "", source: str = "") -> str:
        """Add a node, or update it when it exists.

        Args:
            kind: `role`, `duty` or `skill`.
            name: Display name.
            category: For a skill, one of `CATEGORIES`; kept only if the node has none yet.
            source: Key of the posting that mentioned it, recorded as provenance.

        Returns:
            The node id.
        """
        nid = self.node_id(kind, name)
        n = self.nodes.setdefault(nid, {"id": nid, "kind": kind, "name": name.strip(), "aliases": [], "category": "", "sources": []})
        if category in CATEGORIES and (not n["category"] or n.get('category_defaulted')):
            n["category"] = category
            n.pop('category_defaulted',None)
        if source and source not in n["sources"]:
            n["sources"].append(source)
        return nid

    def alias(self, nid: str, name: str) -> None:
        """Record another spelling of a node's name (ignored when it is the same name after normalisation)."""
        n = self.nodes[nid]
        if name.strip() and norm(name) != norm(n["name"]) and name.strip() not in n["aliases"]:
            n["aliases"].append(name.strip())

    def link(self, src: str, dst: str, posting: str) -> None:
        """Support an edge with one posting.

        Args:
            src: Source node id (a role or a duty).
            dst: Target node id (a duty or a skill).
            posting: The posting's key; an edge's weight is the number of distinct postings supporting it.
        """
        e = self.edges.setdefault(f"{src}>{dst}", {"src": src, "dst": dst, "weight": 0, "postings": []})
        if posting not in e["postings"]:
            e["postings"].append(posting)
            e["weight"] = len(e["postings"])

    def children(self, nid: str) -> list[tuple[str, int]]:
        """The nodes an edge leads to from `nid`, as (id, weight) pairs."""
        return [(e["dst"], e["weight"]) for e in self.edges.values() if e["src"] == nid]

    def parents(self, nid: str) -> list[tuple[str, int]]:
        """The nodes with an edge into `nid`, as (id, weight) pairs."""
        return [(e["src"], e["weight"]) for e in self.edges.values() if e["dst"] == nid]

    def retract(self, posting: str) -> None:
        """Take one posting's support out of the graph (before it is read again with changed content): edges lose it, edges and
        duty/skill nodes nothing supports any more disappear. Roles stay."""
        for key in list(self.edges):
            e = self.edges[key]
            if posting in e["postings"]:
                e["postings"].remove(posting)
                e["weight"] = len(e["postings"])
                if not e["postings"]:
                    del self.edges[key]
        linked = {e["src"] for e in self.edges.values()} | {e["dst"] for e in self.edges.values()}
        for nid in [n for n, v in self.nodes.items() if v["kind"] != "role" and n not in linked]:
            del self.nodes[nid]

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
    """The folder holding the skill graph's files (`graph.json`, `history.jsonl`, `vectors.json`, `approved.json`).

    Args:
        settings: Settings to read `state_dir` from; the current settings when omitted.

    Returns:
        `<state_dir>/skills`.
    """
    from jobagent.config import get_settings
    return (settings or get_settings()).state_dir / "skills"


def load(settings=None) -> Graph:
    """Read the saved graph.

    Args:
        settings: Settings to locate the state folder; the current settings when omitted.

    Returns:
        The saved `Graph`, or an empty one when there is no file or it cannot be read.
    """
    p = skills_dir(settings) / "graph.json"
    try:
        return Graph(json.loads(p.read_text())) if p.exists() else Graph()
    except (OSError, json.JSONDecodeError):
        return Graph()


@contextmanager
def locked(settings=None, timeout: float = 60.0):
    """Exclusive lock on the skill graph for a read-modify-write. Job pipelines run at the same time; each one reloads the graph inside
    the lock, so no update is lost and versions come from committed state. TimeoutError when another run holds it too long."""
    import fcntl
    d = skills_dir(settings)
    d.mkdir(parents=True, exist_ok=True)
    f = open(d / ".lock", "a")
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise TimeoutError("the skill graph is being updated by another run") from None
                time.sleep(0.2)
        yield
    finally:
        try:
            fcntl.flock(f, fcntl.LOCK_UN)
        finally:
            f.close()


def _write_atomic(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as h:
            h.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def save(g: Graph, why: str, before: str | None = None, settings=None) -> bool:
    """Write the graph when its content changed since `before` (a `content_hash`). Call it inside `locked` with a graph loaded INSIDE the
    lock: the version is the committed one plus one, written atomically, with one history line saying why."""
    if before is not None and g.content_hash() == before:
        return False
    d = skills_dir(settings)
    d.mkdir(parents=True, exist_ok=True)
    try:
        committed = json.loads((d / "graph.json").read_text()).get("version", 0)
    except (OSError, json.JSONDecodeError):
        committed = 0
    g.version = committed + 1
    _write_atomic(d / "graph.json", json.dumps(g.to_dict(), indent=1, ensure_ascii=False))
    with open(d / "history.jsonl", "a", encoding="utf-8") as h:
        h.write(json.dumps({"ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "version": g.version, "why": why,
                            "nodes": len(g.nodes), "edges": len(g.edges)}) + "\n")
    return True


# --- similarity --------------------------------------------------------------------------------------------------------------------

def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two embedding vectors.

    Args:
        a: First vector.
        b: Second vector.

    Returns:
        A value in [-1, 1]; 0.0 when the vectors differ in length (they came from different models and are not comparable) or either is all zeros.
    """
    if len(a) != len(b):                                  # vectors of two models are not comparable
        return 0.0
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b, strict=False)) / (na * nb) if na and nb else 0.0


def default_embed(settings=None) -> Callable[[list[str]], list[list[float]] | None]:
    """Embeddings cached in state/skills/vectors.json by embedding model and text; vectors of a fallback model are used once, never cached
    (the same rule as tools/learned.py). None when the model cannot be reached: callers then match by name only."""
    from jobagent import llm
    from jobagent.config import get_settings
    s = settings or get_settings()
    path = skills_dir(s) / "vectors.json"

    mem: dict = {}                                       # loaded once: the file holds 4096-number vectors and grows to megabytes

    def embed(texts: list[str]):
        """Embed texts, using the on-disk cache for known ones.

        Args:
            texts: The strings to embed.

        Returns:
            One vector per text, all from the same model; None when the model cannot be reached (callers then match by name only).
        """
        if not mem:
            try:
                mem.update(json.loads(path.read_text()) if path.exists() else {})
            except (OSError, json.JSONDecodeError):
                pass
        key = lambda t: hashlib.sha1(f"{s.model_embed}|{t}".encode()).hexdigest()  # noqa: E731
        missing = [t for t in dict.fromkeys(texts) if key(t) not in mem]
        if missing:
            try:
                emb = llm.embeddings()
                vecs = emb.embed_documents(missing)
            except Exception:
                return None
            if emb.used_fallback:                      # another model's space: embed EVERYTHING compared with that one model, cache nothing
                try:
                    every = list(dict.fromkeys(texts))
                    got = dict(zip(every, emb.embed_documents(every), strict=False))
                except Exception:
                    return None
                return [got[t] for t in texts]
            new = {key(t): v for t, v in zip(missing, vecs, strict=False)}
            mem.update(new)
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                disk = json.loads(path.read_text()) if path.exists() else {}
            except (OSError, json.JSONDecodeError):
                disk = {}
            disk.update(new)                          # merge with what another run wrote meanwhile
            _write_atomic(path, json.dumps(disk))
        return [mem[key(t)] for t in texts]
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
    """One technology a posting names, as returned by the model: its name as the posting spells it and a category."""
    name: str = Field(max_length=60, description="Exactly one of the posting's technologies, spelled as the posting spells it")
    category: Literal['language','framework','database','cloud','tool','method'] = Field(
        default="tool", description="language, framework, database, cloud, tool or method")


class DutyOut(BaseModel):
    """One duty (a short kind of work, e.g. 'model training') with the posting's technologies that serve it."""
    duty: str = Field(max_length=200, description="A kind of work in at most 4 words, lower case, e.g. 'model training', 'model deployment "
                                                  "and hosting', 'databases', 'data pipelines', 'frontend development'")
    skills: list[SkillOut] = Field(max_length=25)


class IngestOut(BaseModel):
    """The model's answer for one posting: at most eight duties, each with its technologies."""
    duties: list[DutyOut] = Field(max_length=8)


class MergeGroup(BaseModel):
    """Duties that mean the same work: `keep` is the name to keep, `merge` the names to fold into it."""
    keep: str
    merge: list[str]


class MergeOut(BaseModel):
    """The model's answer when asked which duties are duplicates."""
    groups: list[MergeGroup] = Field(max_length=30)


INGEST_PROMPT = (
    "You organise the technologies of ONE job posting by the kind of work each is used for.\n"
    "Given the posting's responsibilities and its list of technologies, return the duties the posting asks for (at most 8) and, under each, "
    "the technologies from the LIST that serve it. A technology may serve several duties. Use only technologies from the LIST, spelled as "
    "listed; do not add any (skip anything that is not a named technology, library, language, service or tool). Name duties as short "
    "kinds of work of at most 4 words, never as a sentence and never as a technology; never a catch-all ('software development') or a soft skill ('team collaboration'). CHOOSE from the KNOWN DUTIES (exact text) whenever one "
    "fits the work; make a new duty (at most 4 words) only for work none of them covers. Give each technology a category.")


def _role() -> str:
    try:
        from jobagent.config import get_settings
        return get_settings().skills_model
    except Exception:
        return "fast"


def default_ask(jd: dict, known_duties: list[str], skills: list[str]) -> list[DutyOut]:
    """One structured call to the fast model per posting (the planner model took ~10 minutes a posting for this grouping)."""
    from langchain_core.messages import HumanMessage, SystemMessage

    from jobagent import llm
    body = {"title": jd.get("title"), "responsibilities": (jd.get("responsibilities") or [])[:25],
            "preferred_qualifications": (jd.get("preferred_qualifications") or [])[:10]}
    text = (f"KNOWN DUTIES: {json.dumps(known_duties)}\n\nPOSTING:\n{json.dumps(body, ensure_ascii=False)}\n\nLIST: {json.dumps(skills)}")
    out = llm.structured_invoke(llm.chat(_role()), IngestOut, [SystemMessage(INGEST_PROMPT), HumanMessage(text)])
    return out.duties


def default_merge(duties: list[str]) -> list[MergeGroup]:
    """Ask the model which duty names mean the same work.

    Args:
        duties: Every duty name in the graph.

    Returns:
        The groups to merge; names the model invents are ignored later by `consolidate`.
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    from jobagent import llm
    prompt = ("These are the duties collected from many job postings. Group the ones that mean the same kind of work (e.g. 'model training' "
              "and 'training machine learning models'); for each group give the duty to keep (one of the group, exact text) and the others to "
              "merge into it. Leave distinct duties out. Use only the given names.")
    return llm.structured_invoke(llm.chat(_role()), MergeOut, [SystemMessage(prompt), HumanMessage(json.dumps(duties))]).groups


def ensure_role(g: Graph, role_id: str, title: str = "") -> str:
    """Make sure a role node exists, and refresh its display name.

    Args:
        g: The graph.
        role_id: The role's id from `data/target_roles.yaml`.
        title: The role's title, when known.

    Returns:
        The node id (`role:<role_id>`).
    """
    nid = f"role:{role_id}"
    n = g.nodes.setdefault(nid, {"id": nid, "kind": "role", "name": title or role_id, "aliases": [], "category": "", "sources": []})
    if title:
        n["name"] = title
    return nid


def ingest(g: Graph, jd: dict, role_id: str, *, ask=default_ask, embed=None, duty_threshold: float = 0.92, role_title: str = "") -> dict:
    """Add one posting. A posting already read with the same content changes nothing; one read again with changed content replaces its
    old support. Skills are identified by name and recorded alias only (never by embedding similarity: close is not the same technology,
    and ownership follows identity); duties, which carry no ownership, may merge by similarity."""
    key, fp = jd_key(jd), jd_fp(jd, role_id)
    cur = g.postings.get(key)
    if cur and (cur.get("fp") is None or cur["fp"] == fp):
        return {"added_skills": [], "added_duties": [], "skipped": True}
    skills = clean_skills(jd.get("technical_skills") or [])
    duties = ask(jd, list(dict.fromkeys([*DEFAULT_DUTIES, *(n["name"] for n in g.of_kind("duty"))])), skills) if skills else []
    if cur:
        g.retract(key)
    role = ensure_role(g, role_id, role_title)
    g.postings[key] = {"role": role_id, "fp": fp}
    if not skills:
        return {"added_skills": [], "added_duties": [], "skipped": False}
    by_norm = {norm(s): s for s in skills}
    new_skills: list[str] = []
    new_duties: list[str] = []
    placed: set[str] = set()
    for d in duties:
        duty_name = d.duty.strip().lower()
        if not valid_duty(duty_name):                          # a sentence, not a duty: its skills fall to "general tooling"
            continue
        duty = resolve(g, "duty", duty_name, embed, duty_threshold)
        if duty is None:
            duty = g.add("duty", duty_name, source=key)
            new_duties.append(duty_name)
        g.link(role, duty, key)
        for s in d.skills:
            if norm(s.name) not in by_norm:                        # only technologies the posting names
                continue
            known = g.find("skill", s.name) is not None
            sid = g.add("skill", by_norm[norm(s.name)], s.category, key)
            if not known:
                new_skills.append(sid)
            g.link(duty, sid, key)
            placed.add(norm(s.name))
    leftover = [by_norm[k] for k in by_norm if k not in placed]
    if leftover:
        duty = g.find("duty", GENERAL_DUTY) or g.add("duty", GENERAL_DUTY, source=key)
        g.link(role, duty, key)
        for name in leftover:
            known = g.find("skill", name) is not None
            sid = g.add("skill", name, "tool", key)
            if not known:
                new_skills.append(sid)
            g.link(duty, sid, key)
    return {"added_skills": new_skills, "added_duties": new_duties, "skipped": False}


def add_posting(jd: dict, role_id: str, settings=None, *, ask=default_ask, embed=None, duty_threshold: float | None = None,
                role_title: str = "", timeout: float = 5.0) -> dict | None:
    """Add a posting to the SAVED graph while other runs may be doing the same: the slow model call happens outside the lock, then the
    graph is reloaded inside it and the posting applied to that. None when nothing needed doing."""
    from jobagent.config import get_settings
    s = settings or get_settings()
    snap = load(s)
    cur = snap.postings.get(jd_key(jd))
    if cur and (cur.get("fp") is None or cur["fp"] == jd_fp(jd, role_id)):
        return None
    skills = clean_skills(jd.get("technical_skills") or [])
    duties = ask(jd, list(dict.fromkeys([*DEFAULT_DUTIES, *(n["name"] for n in snap.of_kind("duty"))])), skills) if skills else []
    with locked(s, timeout):
        g = load(s)
        before = g.content_hash()
        res = ingest(g, jd, role_id, ask=lambda *_: duties, embed=embed, role_title=role_title,
                     duty_threshold=duty_threshold if duty_threshold is not None else s.skills_duty_merge)
        save(g, f"posting {jd.get('company', '')} - {jd.get('title', '')}", before, s)
    return res


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
          log=print, duty_threshold: float = 0.92) -> dict:
    """Add every role of the roles file (even with no postings yet) and every saved posting, then consolidate the duties."""
    titles = {r["id"]: r.get("title", r["id"]) for r in roles if r.get("id")}
    for rid, title in titles.items():
        ensure_role(g, rid, title)
    stats = {"postings": 0, "skipped": 0, "failed": 0, "roles_without_postings": [], "category_defaults": 0}
    # Older unrestricted model output could be silently discarded by Graph.add, leaving blank categories.
    # Preserve every identity/edge/ownership boundary; use the existing generic default when the old category is unknown.
    for node in g.of_kind('skill'):
        if node.get('category') not in CATEGORIES:
            node['category']='tool'
            node['category_defaulted']=True
            stats['category_defaults']+=1
    for i, (jd, rid) in enumerate(postings, 1):
        try:
            log(f"skills: {i}/{len(postings)} {jd.get('company', '')} - {jd.get('title', '')} ({rid})")
            res = ingest(g, jd, rid, ask=ask, embed=embed, duty_threshold=duty_threshold, role_title=titles.get(rid, ""))
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
    have = {p["role"] for p in g.postings.values()}
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
    steering = {s for s in mine if g.nodes[s]["category"] != "language"} or mine      # Python / Java sit under almost every duty: they steer nothing
    for sid in steering:
        for duty, w in g.parents(sid):
            if g.nodes[duty]["name"] != GENERAL_DUTY:                  # unplaced skills share a bucket, not a kind of work
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
            part = ds * (w / top) / math.log2(2 + len(g.children(duty)))           # a huge duty says little about two of its skills
            part /= math.sqrt(max(1, len(g.parents(sid))))                           # a skill under many duties (Python) is a weak neighbour
            total[sid] = total.get(sid, 0.0) + part
            if part > best.get(sid, (0.0, ""))[0]:
                best[sid] = (part, duty)
    scores = {sid: (total[sid], best[sid][1]) for sid in total}
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1][0], g.nodes[kv[0]]["name"]))
    out = {"owned": [], "review": []}
    for sid, (score, duty) in ranked:
        n = g.nodes[sid]
        names = {norm(n["name"])} | {norm(a) for a in n["aliases"]}
        item = {"skill": n["name"], "via": g.nodes[duty]["name"], "score": round(score, 2), "aliases": list(n["aliases"])}
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


def evidence_text(settings=None) -> str:
    """Everything the candidate wrote about their own work: the resume template (experience bullets, projects, skills), master_profile.yaml
    and the titles/summaries/stacks of their GitHub repo cards. A skill named there is one the candidate has used."""
    from jobagent.config import get_settings
    s = settings or get_settings()
    parts: list[str] = []
    for p in (s.data_dir / "templates" / "resume.tex", s.data_dir / "master_profile.yaml"):
        try:
            parts.append(p.read_text())
        except OSError:
            pass
    for p in (s.state_dir / "github" / "cards").glob("*.json"):
        try:
            c = json.loads(p.read_text())
            parts.append(" ".join([str(c.get("title", "")), str(c.get("summary", "")), " ".join(c.get("tech_stack", []) or [])]))
        except (OSError, json.JSONDecodeError):
            continue
    return "\n".join(parts)


def owned_with_evidence(g: Graph, resume_terms: list[str], settings=None) -> set[str]:
    """`owned_skills` plus every graph skill the candidate's own writing names. Names of one or two characters ("Go", "R") are not read
    out of prose (too easy to misread), only from the explicit skill lists."""
    owned = owned_skills(resume_terms, settings)
    text = evidence_text(settings)
    for n in g.of_kind("skill"):
        if len(n["name"]) > 2 and named_in(text, {"skill": n["name"], "aliases": n["aliases"]}):
            owned |= {norm(n["name"]), *(norm(a) for a in n["aliases"])}
    return owned


def approve(skill: str, settings=None) -> bool:
    """Record a skill the candidate confirms they have; it counts as owned from then on. (master_profile.yaml is the candidate's own
    file and is never rewritten by the agent, so approvals live in state/skills/approved.json.)"""
    d = skills_dir(settings)
    with locked(settings, 10):
        p = d / "approved.json"
        try:
            have = json.loads(p.read_text()) if p.exists() else []
        except json.JSONDecodeError:
            have = []
        if norm(skill) in {norm(x) for x in have}:
            return False
        _write_atomic(p, json.dumps(sorted([*have, skill.strip()], key=str.lower), indent=1))
    return True


def mermaid(g: Graph, role: str | None = None) -> str:
    """The graph in Mermaid `graph` form (Role --> Duty --> Skill), optionally for one role id."""
    keep_duties = {c for c, _ in g.children(f"role:{role}")} if role else None
    ids = {}

    def ident(nid: str) -> str:
        """A short, stable id for a node inside one diagram."""
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

ARTIFACT = "skill_suggestions.json"


def _fingerprint(jd: dict, g: Graph, owned: set[str]) -> dict:
    return {"jd": jd_fp(jd), "graph": g.version, "owned": hashlib.sha1(json.dumps(sorted(owned)).encode()).hexdigest()[:12]}


def named_in(text: str, item: dict) -> bool:
    """Whether the posting's own text names this skill (by name or recorded alias). A skill the posting names is the posting's, not a graph
    suggestion: the claims policy has always allowed what the posting asks for. Names of three letters or fewer ("Go", "R") must match
    case-sensitively as a whole word, so the verb "go" does not count."""
    for name in [item["skill"], *item.get("aliases", [])]:
        flags = 0 if len(name) <= 3 else re.IGNORECASE
        if re.search(rf"(?<![A-Za-z0-9+#]){re.escape(name)}(?![A-Za-z0-9+#])", text, flags):
            return True
    return False


def _posting_text(jd: dict, job_dir: Path | None) -> str:
    parts = [json.dumps(jd, ensure_ascii=False)]
    if job_dir is not None and (job_dir / "jd.txt").exists():
        try:
            parts.append((job_dir / "jd.txt").read_text())
        except OSError:
            pass
    return "\n".join(parts)


def job_suggestions(job_dir: Path | None, jd: dict, role_id: str, resume_terms: list[str], settings=None) -> dict:
    """Suggestions for one job, also written to <job_dir>/skill_suggestions.json with the fingerprints of the posting, the graph and the
    candidate's skills (an EMPTY result is written too, so an old one never lingers). {} when switched off, when the graph has nothing for
    this posting, or on any error: the artifact is then removed (fail closed) and tailoring goes on without the graph."""
    art = (job_dir / ARTIFACT) if job_dir is not None else None
    try:
        from jobagent.config import get_settings
        s = settings or get_settings()
        if not s.skills_use_graph:
            if art:
                art.unlink(missing_ok=True)
            return {}
        g = load(s)
        owned = owned_with_evidence(g, resume_terms, s)
        res = suggest(g, clean_skills(jd.get("technical_skills") or []), role_id, owned, {norm(t) for t in resume_terms}, s.skills_max_neighbours)
        text = _posting_text(jd, job_dir)
        for k in ("owned", "review"):                                   # skills the posting itself names are its own, not suggestions
            res[k] = [x for x in res[k] if not named_in(text, x)]
        res["fingerprint"] = _fingerprint(jd, g, owned)
        if art:
            _write_atomic(art, json.dumps(res, indent=1))
        return res if (res["owned"] or res["review"]) else {}
    except Exception:
        if art:
            art.unlink(missing_ok=True)
        return {}


def _resume_terms(resume) -> list[str]:
    """The template's skills as individual terms: "Term (a, b)" gives Term, a and b."""
    from jobagent.tools import tailor
    out: list[str] = []
    for _, items in resume.skills:
        for part in re.split(r",(?![^()]*\))", tailor.to_plain(items)):
            part = part.strip()
            if m := re.fullmatch(r"(.+?)\s*\((.+)\)", part):
                out += [m.group(1).strip(), *[x.strip() for x in m.group(2).split(",") if x.strip()]]
            elif part:
                out.append(part)
    return out


def for_tailor(job_dir: Path | None, jd, resume, settings=None) -> dict:
    """Suggestions for the posting being tailored (`jd` a JobDescription, `resume` the parsed template). The role comes from the queue's
    match for this posting's address; {} when there is none or on any error."""
    try:
        from jobagent import db
        row = db.connect().execute("SELECT role_id FROM jobs WHERE url = ?", (getattr(jd, "source_url", "") or "",)).fetchone()
        return job_suggestions(job_dir, jd.model_dump(), (row["role_id"] if row and row["role_id"] else ""), _resume_terms(resume), settings)
    except Exception:
        return {}


def input_fingerprint(jd, resume, settings=None) -> str:
    """What the resume stage must treat as an input: the graph switch, the graph's version and the neighbours it would suggest given what the
    candidate owns. Approving a skill, growing the graph or switching it off therefore rebuilds the resume without --force."""
    try:
        from jobagent.config import get_settings
        s = settings or get_settings()
        if not s.skills_use_graph:
            return "off"
        res = for_tailor(None, jd, resume, s)
        basis = {"graph": (g := load(s)).version, "owned": sorted(owned_with_evidence(g, _resume_terms(resume), s)), "n": s.skills_max_neighbours,
                 "res": {k: [(x["skill"], x["via"]) for x in res.get(k, [])] for k in ("owned", "review")}}
        return hashlib.sha1(json.dumps(basis, sort_keys=True).encode()).hexdigest()[:16]
    except Exception:
        return "error"


def _valid_artifact(job_dir: Path) -> dict | None:
    """This job's suggestions when they still describe its posting; None when missing, unreadable or stale."""
    try:
        res = json.loads((job_dir / ARTIFACT).read_text())
        jd = json.loads((job_dir / "jd.json").read_text())
        return res if (res.get("fingerprint") or {}).get("jd") == jd_fp(jd) else None
    except (OSError, json.JSONDecodeError):
        return None


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


# --- ownership enforcement: no related skill the candidate's files do not show reaches a resume, whoever wrote it -----------------------

def forbidden_set(res: dict | None) -> set[str]:
    """`norm` keys (names and aliases) of the related skills listed for review: not owned, so never to appear."""
    out: set[str] = set()
    for x in (res or {}).get("review", []):
        out |= {norm(x["skill"]), *(norm(a) for a in x.get("aliases", []))}
    out.discard("")
    return out


def _strip(raw: str, forbidden: set[str]) -> tuple[str, list[str]]:
    """One written term ("A, B (c, d)") without forbidden skills, as a term or inside the parentheses; (rest, what was removed)."""
    kept, gone = [], []
    for part in re.split(r",(?![^()]*\))", raw):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(.+?)\s*\((.+)\)", part)
        term, tools = (m.group(1).strip(), [x.strip() for x in m.group(2).split(",") if x.strip()]) if m else (part, [])
        if norm(term) in forbidden:
            gone.append(term)
            gone += [x for x in tools if norm(x) in forbidden]
            continue
        ok = [x for x in tools if norm(x) not in forbidden]
        gone += [x for x in tools if norm(x) in forbidden]
        kept.append(f"{term} ({', '.join(ok)})" if ok else term)
    return ", ".join(kept), gone


def enforce(skills: list, forbidden: set[str]) -> tuple[list, list[str]]:
    """The skill lines (tailor.SkillLine) without any forbidden skill, in terms, in parentheses and in `tools_for`. A line left with nothing
    is removed: it is never kept to satisfy a minimum. Returns (new lines, removed names)."""
    if not forbidden:
        return skills, []
    dropped: list[str] = []
    out = []
    for line in skills:
        terms = []
        for raw in line.terms:
            kept, gone = _strip(raw, forbidden)
            dropped += gone
            if kept:
                terms.append(kept)
        tools_for = []
        for t in line.tools_for:
            if norm(t.term) in forbidden:
                dropped.append(t.term)
                continue
            tools = [x for x in t.tools if norm(x) not in forbidden]
            dropped += [x for x in t.tools if norm(x) in forbidden]
            if tools:
                tools_for.append(t.model_copy(update={"tools": tools}))
        if terms:
            out.append(line.model_copy(update={"terms": terms, "tools_for": tools_for}))
    return out, dropped


def guard_resume(resume, job_dir: Path) -> None:
    """Attach this job's forbidden set to the parsed resume, from its (valid) suggestions artifact, so every render of it is checked."""
    try:
        resume.forbidden = forbidden_set(_valid_artifact(job_dir))
    except AttributeError:                                  # not a tailor.Resume (a test double): nothing to guard
        pass


def violations(job_dir: Path, f) -> list[str]:
    """Skills in a generated resume (`f`: tailor.Filled) that this job's suggestions list as not owned; empty when all is well."""
    forb = forbidden_set(_valid_artifact(job_dir))
    if not forb:
        return []
    _, dropped = enforce(f.skills, forb)
    return [f"skill not shown by your files: {x} (it was only suggested; `jobagent skills approve` it first)" for x in dict.fromkeys(dropped)]


def review_lines(job_dir: Path) -> list[str]:
    """Markdown for the review packet: related skills the candidate may want to confirm (empty list when none or when they are stale)."""
    res = _valid_artifact(job_dir)
    if not res or not res.get("review"):
        return []
    out = ["## Suggested skills (not added)", "",
           "Related to this posting's skills but not found in your files. Add one only if you really have it: "
           "`jobagent skills approve \"<skill>\"` (then the next tailoring may use it).", ""]
    out += [f"- {x['skill']} (via {x['via']})" for x in res["review"]]
    return out + [""]
