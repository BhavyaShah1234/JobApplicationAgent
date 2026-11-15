"""Does a listing match one of the candidate's target roles (data/target_roles.yaml)? Cheap to expensive:

1. **Regex.** The global and the role's `exclude_patterns` reject a title outright; a role's `patterns` hit is a strong
   prior (stage `regex`) and the listing goes to the judge with it.
2. **Semantic.** A listing no pattern hit is embedded (title + snippet) and compared with each role's `description`;
   at or above `semantic_threshold` it goes to the judge (stage `semantic`).
3. **Judge.** The fast model compares the listing with the role and preferences.yaml and returns
   {match, role_id, confidence, reason}. `confidence >= ACCEPT` is queued; `REVIEW <= confidence < ACCEPT` is
   `needs_review` for the candidate; anything lower is skipped.

Pure functions take the roles file as a dict, so everything but `judge` / `similarity` runs offline.
"""

import math
import re
from dataclasses import dataclass, field

import yaml

from jobagent.config import get_settings

ACCEPT, REVIEW = 0.7, 0.5
DEFAULT_THRESHOLD = 0.6


@dataclass
class Match:
    verdict: str                      # queued | needs_review | skipped
    stage: str                        # excluded | regex | semantic | judge | none
    role_id: str = ""
    score: float = 0.0                # the judge's confidence, else the semantic similarity, else 0
    reason: str = ""
    detail: dict = field(default_factory=dict)


def load_roles(path=None) -> dict:
    p = path or get_settings().data_dir / "target_roles.yaml"
    data = yaml.safe_load(p.read_text()) if p.exists() else None
    return data or {"roles": []}


def _hits(patterns, text: str) -> str | None:
    for pat in patterns or []:
        try:
            if re.search(pat, text):
                return pat
        except re.error:
            continue  # a bad pattern in the user's file never stops discovery
    return None


def regex_stage(title: str, config: dict) -> tuple[str, list[dict]]:
    """('excluded', []) when a global / role exclude pattern hits; else ('ok', roles whose patterns hit)."""
    if _hits(config.get("global_exclude_patterns"), title):
        return "excluded", []
    hit = []
    for role in config.get("roles", []):
        if _hits(role.get("exclude_patterns"), title):
            continue
        if _hits(role.get("patterns"), title):
            hit.append(role)
    hit.sort(key=lambda r: -(r.get("priority") or 0))
    return "ok", hit


def cosine(a: list[float], b: list[float]) -> float:
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0


def semantic_stage(text: str, config: dict, embed) -> tuple[dict | None, float]:
    """The role whose description is closest to `text` and its similarity. `embed(list[str]) -> list[vector]`."""
    roles = [r for r in config.get("roles", []) if r.get("description")]
    if not roles or not text.strip():
        return None, 0.0
    vecs = embed([text] + [f"{r.get('title', '')}: {r['description']}" for r in roles])
    scored = [(cosine(vecs[0], v), r) for v, r in zip(vecs[1:], roles)]
    best = max(scored, key=lambda x: x[0])
    return best[1], best[0]


def semantic_text(title: str, snippet: str) -> str:
    return f"{title}. {snippet}"[:1500]


def role_texts(config: dict) -> list[str]:
    return [f"{r.get('title', '')}: {r['description']}" for r in config.get("roles", []) if r.get("description")]


class BudgetExceeded(Exception):
    """An embedding was needed that the configured budget does not cover."""


def precomputed(texts: list[str], embed, batch: int = 64, cap: int | None = None, stats: dict | None = None):
    """An `embed(list[str])` that answers from one batched pass over `texts` (listings and role descriptions), so a
    company's hundreds of titles cost a few embedding calls instead of one per listing. The budget is enforced at execution
    time, not only when planning: `cap` is the most texts ever embedded, and a text outside the precomputed set is NEVER
    embedded on demand - asking for it raises BudgetExceeded. `stats` (if given) records the calls and texts actually used."""
    stats = stats if stats is not None else {}
    stats.update(calls=0, embedded=0, cap=cap)
    vectors: dict[str, list[float]] = {}
    todo = list(dict.fromkeys(t for t in texts if t))
    if cap is not None and len(todo) > cap:
        raise BudgetExceeded(f"{len(todo)} texts exceed the budget of {cap}")
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        stats["calls"] += 1
        stats["embedded"] += len(chunk)
        vectors.update(zip(chunk, embed(chunk)))

    def lookup(items: list[str]) -> list[list[float]]:
        missing = [t for t in items if t not in vectors]
        if missing:
            raise BudgetExceeded(f"{len(missing)} text(s) were not in the budgeted batch")
        return [vectors[t] for t in items]
    return lookup


def decide(confidence: float, match: bool) -> str:
    if match and confidence >= ACCEPT:
        return "queued"
    if confidence >= REVIEW and match:
        return "needs_review"
    return "skipped"


def match_listing(title: str, snippet: str, config: dict, embed=None, judge=None, degraded: str = "") -> Match:
    """The three stages for one listing. `judge(title, snippet, role, prior) -> (match, confidence, reason)` is the
    model call. With NO judge by the user's choice (`--no-judge`, `degraded` empty) a regex hit is queued at the prior's
    confidence 0.7 and a semantic one is reviewed. When the judge could not run - it failed, or its budget is spent
    (`degraded` says why) - nothing is auto-validated: a regex or semantic candidate goes to `needs_review`, marked as not
    validated, never to the queue."""
    status, hit = regex_stage(title, config)
    if status == "excluded":
        return Match("skipped", "excluded", reason="the title matches an exclude pattern")
    role, stage, sim = None, "", 0.0
    if hit:
        role, stage, sim = hit[0], "regex", 1.0
    elif embed is not None:
        try:
            cand, sim = semantic_stage(semantic_text(title, snippet), config, embed)
        except BudgetExceeded:
            return Match("skipped", "none", reason="not evaluated (the embedding budget was reached)",
                         detail={"degraded": "embedding budget"})
        if cand is not None and sim >= float(config.get("semantic_threshold", DEFAULT_THRESHOLD)):
            role, stage = cand, "semantic"
    if role is None:
        return Match("skipped", "none", score=round(sim, 3), reason="no target role fits this title")
    if judge is None:
        if degraded:
            return Match("needs_review", stage, role["id"], round(sim, 3) if stage == "semantic" else ACCEPT,
                         f"{stage} match to {role.get('title', role['id'])}; NOT validated by the judge ({degraded})",
                         {"degraded": degraded})
        verdict = "queued" if stage == "regex" else "needs_review"
        return Match(verdict, stage, role["id"], round(sim, 3) if stage == "semantic" else ACCEPT,
                     f"{stage} match to {role.get('title', role['id'])}")
    match, confidence, reason = judge(title, snippet, role, stage)
    return Match(decide(confidence, match), "judge", role["id"], round(confidence, 3), reason,
                 {"prior": stage, "similarity": round(sim, 3)})


def model_judge(preferences: dict | None = None):
    """The judge backed by the fast model, with preferences.yaml (seniority, locations, types) in its prompt."""
    from langchain_core.messages import HumanMessage, SystemMessage
    from pydantic import BaseModel, Field

    from jobagent import llm

    class Verdict(BaseModel):
        match: bool = Field(description="The listing is a role the candidate should apply to")
        confidence: float = Field(ge=0, le=1, description="0-1, how sure")
        reason: str = Field(max_length=200, description="One short sentence")

    prefs = yaml.safe_dump({k: v for k, v in (preferences or {}).items()
                            if k in ("seniority", "employment_types", "locations", "exclude")}, sort_keys=False)

    def judge(title: str, snippet: str, role: dict, prior: str):
        msgs = [SystemMessage("You decide whether a job listing is a role the candidate wants. Judge by the title and "
                              "snippet only; never invent details. A listing far above the candidate's seniority, "
                              "or in another field, is not a match."),
                HumanMessage(f"TARGET ROLE: {role.get('title')} - {role.get('description')}\n"
                             f"Accepted seniority: {role.get('seniority')}\nPREFERENCES:\n{prefs}\n"
                             f"Prior signal: {prior}\nLISTING TITLE: {title}\nSNIPPET: {snippet[:800]}")]
        out = llm.structured(Verdict, "fast", timeout=60).invoke(msgs)
        return out.match, float(out.confidence), out.reason
    return judge


def model_embed():
    from jobagent import llm

    emb = llm.embeddings()
    return lambda texts: emb.embed_documents(texts)
