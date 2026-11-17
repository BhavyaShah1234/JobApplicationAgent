"""What the candidate taught the agent on the spot: `data/learned.yaml`, kept apart from `applicant.yaml` (which only
the candidate edits). The planner never sees this file as a whole: when a form asks something applicant.yaml doesn't
answer, the few entries closest to that question are looked up (`relevant`) and only those are shown.

Entries: {question, seen, answer, instructions, company, ...}. A rule (`instructions`, `company: any`) says how to
answer questions like this one; a plain answer belongs to the company it was given for. Writes are locked, backed up
first (state/backups/learned-*.yaml, last 20 kept) and checked to still parse.
"""

import fcntl
import hashlib
import json
import math
import re
import shutil
from contextlib import contextmanager
from pathlib import Path

import yaml
from rapidfuzz import fuzz

from jobagent.clock import now
from jobagent.config import get_settings

HEAD = """# What you taught the agent while it filled forms (`jobagent hitl`). The agent appends here; applicant.yaml is
# never written by code. It looks here only for questions applicant.yaml doesn't answer, and then only at the few
# entries closest to that question.
# Reply "rule: <how to answer questions like this>" to save an instruction for every company (company: any);
# a plain reply is saved as that company's answer. Edit or delete entries freely (`jobagent learned` lists them).
# A literal confirmation of the posting's qualifications is application-specific, never reusable across roles.
"""


def path() -> Path:
    return get_settings().data_dir / "learned.yaml"


def _backups() -> Path:
    return get_settings().state_dir / "backups"


def load() -> list[dict]:
    p = path()
    data = yaml.safe_load(p.read_text()) if p.exists() else None
    return [e for e in (data or []) if isinstance(e, dict) and e.get("question")]


@contextmanager
def _locked():
    lock = path().with_suffix(".lock")
    with open(lock, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _write(entries: list[dict]) -> None:
    """Back up, write, and check the result still parses (else put the backup back)."""
    p = path()
    backup = None
    if p.exists():
        _backups().mkdir(parents=True, exist_ok=True)
        backup = _backups() / f"learned-{now():%Y%m%d-%H%M%S-%f}.yaml"
        shutil.copy2(p, backup)
        for old in sorted(_backups().glob("learned-*.yaml"))[:-20]:
            old.unlink(missing_ok=True)
    p.write_text(HEAD + "\n" + yaml.safe_dump(entries, sort_keys=False, allow_unicode=True, width=120))
    try:
        yaml.safe_load(p.read_text())
    except yaml.YAMLError:
        if backup:
            shutil.copy2(backup, p)
        raise


def application_only(question: str) -> bool:
    """A yes/no attestation about unstated requirements depends on the current JD."""
    return bool(re.search(r'(?i)\b(?:meet|meets|satisfy|satisfies|fulfill|fulfil)\b', question) and
                re.search(r'(?i)\b(?:qualifications?|requirements?|criteria)\b', question))


def reusable(entry: dict, question: str = '') -> bool:
    """Explicit instructions can evaluate each JD; an old aggregate literal cannot."""
    return bool(entry.get('instructions')) or not any(application_only(q) for q in
        [question, entry['question'], *(entry.get('seen') or [])])


def add(question: str, options: list[str], answer: str, company: str, portal: str, rule: str = "") -> dict:
    """Keep the candidate's reply. A rule replaces an earlier rule for the same question; a plain answer replaces the
    same company's earlier answer. Aggregate qualification literals remain current-application replies and are not stored."""
    if rule:
        item = {"question": question, "seen": [question], "answer": "", "instructions": rule, "company": "any",
                "learned_from": f"{company} ({portal}), {now():%Y-%m-%d}"}
    else:
        item = {"question": question, "seen": [question], "answer": answer, "options": options[:30] or None,
                "company": company, "portal": portal, "date": now().strftime("%Y-%m-%d")}
        if application_only(question):
            return {**item, 'scope': 'current_application', 'stored': False}
    with _locked():
        entries = [e for e in load() if not (e["question"] == question and e.get("company") == item["company"])]
        entries.append(item)
        _write(entries)
    return item


# --- retrieval -------------------------------------------------------------------------------------------------

def _clean(label: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[*✱]", "", label)).strip(" :?").lower()


def _vectors(texts: list[str]) -> list[list[float]] | None:
    """Embeddings of the texts, cached in state/learned_vectors.json (by the embedding model and text)."""
    from jobagent import llm
    s = get_settings()
    cache_p = s.state_dir / "learned_vectors.json"
    try:
        cache = json.loads(cache_p.read_text()) if cache_p.exists() else {}
    except json.JSONDecodeError:
        cache = {}
    key = lambda t: hashlib.sha1(f"{s.model_embed}|{t}".encode()).hexdigest()  # noqa: E731
    missing = [t for t in dict.fromkeys(texts) if key(t) not in cache]
    if missing:
        emb = llm.embeddings()
        try:
            vecs = emb.embed_documents(missing)
        except Exception:
            return None
        if emb.used_fallback:  # another model's vectors: use them this once, don't mix them into the cache
            got = dict(zip(missing, vecs))
            return [got.get(t) or cache.get(key(t)) for t in texts]
        cache.update({key(t): v for t, v in zip(missing, vecs)})
        cache_p.write_text(json.dumps(cache))
    return [cache[key(t)] for t in texts]


def _cos(a: list[float], b: list[float]) -> float:
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0


def relevant(label: str, company: str, k: int = 3, entries: list[dict] | None = None, vector_bank: dict | None = None) -> list[dict]:
    """The learned entries closest to a form question: wording match and meaning (embeddings), at most k, only those
    reasonably close. A plain answer from another company is kept only when it is a near-exact wording match (the
    planner is told it was given elsewhere)."""
    entries = [e for e in (load() if entries is None else entries) if reusable(e, label)]
    if not entries:
        return []
    q = _clean(label)
    texts = [[_clean(t) for t in [e["question"], *(e.get("seen") or [])]] for e in entries]
    flat = [t for ts in texts for t in ts]
    vecs = ([vector_bank.get(t) for t in [q,*flat]] if vector_bank and all(t in vector_bank for t in [q,*flat]) else None) if vector_bank is not None else _vectors([q,*flat])
    qv, lookup = (vecs[0], dict(zip(flat, vecs[1:]))) if vecs else (None, {})
    scored = []
    for e, ts in zip(entries, texts):
        # the same words in another order, or the learned wording inside a longer label ("... (Select both if appropriate)")
        word = max(max(fuzz.token_sort_ratio(q, t),
                       fuzz.partial_ratio(q, t) if len(t.split()) >= 4 and len(q) > len(t) else 0) for t in ts) / 100
        meaning = max(_cos(qv, lookup[t]) for t in ts) if qv is not None else 0.0
        score = max(word, meaning)
        if e.get("company") not in ("any", company.lower()) and not e.get("instructions") and word < 0.9:
            continue  # another company's literal answer to a different-sounding question
        if score >= 0.75:
            scored.append((score, e))
    return [e for _, e in sorted(scored, key=lambda x: -x[0])[:k]]
