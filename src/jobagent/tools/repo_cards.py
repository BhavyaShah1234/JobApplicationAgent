"""Step 9: one saved analysis ("card") per GitHub repo, built by reading the repo's code.

Many repos have no README, so the agent reads the code itself: the source archive (codeload.github.com, not
counted against the API limit) → file tree + dependency manifests + original README + the main source files and
notebooks → the model writes a card: a résumé-ready summary (problem, solution, system), a README for the agent's
own understanding, the tech stack, technical fields, results the evidence states, and how substantial the project is.

Cards live in state/github/cards/<owner>__<repo>.json (+ .README.md) and record the default branch's commit SHA they
were written from; a card is rebuilt only when that SHA changed (checked only when `pushed_at` moved), so a run only
analyses new or changed repos. The source archive is a nameless temp file, gone once the repo has been read. Each card also stores its embedding, so
ranking repos against a job costs one embedding call for the job.
"""

import json
import math
import re
import tarfile
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Literal

import httpx
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from jobagent.clock import with_today
from jobagent import llm
from jobagent.config import get_settings
from jobagent.tools import github

MAX_ARCHIVE = 80 * 2**20          # larger archives (datasets, weights) are read from README + tree only
MAX_FILE = 200 * 2**10
CODE_BUDGET, FILE_HEAD = 32_000, 4_000
SKIP_DIRS = {"node_modules", ".git", "venv", ".venv", "env", "__pycache__", "dist", "build", "site-packages",
             ".idea", ".vscode", ".next", "vendor", "third_party", "weights", "checkpoints", "runs", "wandb",
             "data", "datasets", ".ipynb_checkpoints", "migrations", "static", "assets", "public", "devel",
             "install", "log"}
SOURCE_EXT = {".py", ".ipynb", ".js", ".jsx", ".ts", ".tsx", ".java", ".c", ".cc", ".cpp", ".h", ".hpp", ".cs",
              ".go", ".rs", ".rb", ".php", ".kt", ".swift", ".m", ".r", ".jl", ".scala", ".sql", ".sh", ".launch",
              ".tf", ".dart", ".vue", ".svelte", ".lua", ".ino", ".sv", ".v", ".vhd"}
MANIFESTS = re.compile(r"(?i)^(requirements[\w.-]*\.txt|pyproject\.toml|setup\.(py|cfg)|environment\.ya?ml|"
                       r"package\.json|cargo\.toml|go\.mod|pom\.xml|build\.gradle(\.kts)?|cmakelists\.txt|"
                       r"package\.xml|dockerfile|docker-compose[\w.-]*\.ya?ml|makefile|.*\.tf|pubspec\.yaml|"
                       r"\.github/workflows/.*\.ya?ml)$")
ENTRY = re.compile(r"(?i)(^|/)(main|app|train|run|server|index|api|agent|graph|pipeline|model|inference|node|"
                   r"__init__|cli|manage|launch)\.\w+$")


def card_dir() -> Path:
    d = get_settings().state_dir / "github" / "cards"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _key(full_name: str) -> str:
    return full_name.replace("/", "__")


# --- reading a repo ----------------------------------------------------------------------------------------

@dataclass
class Snapshot:
    tree: list[str] = field(default_factory=list)
    manifests: dict[str, str] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)   # path -> text (whole file, capped)
    truncated: bool = False                                  # archive too big: tree + README only
    sha: str = ""                                            # commit the archive was made from


def _notebook_text(raw: str) -> str:
    try:
        nb = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    parts = []
    for cell in nb.get("cells", []):
        src = "".join(cell.get("source", []))
        parts.append(("# [markdown] " + src.replace("\n", "\n# ")) if cell.get("cell_type") == "markdown" else src)
    return "\n\n".join(parts)


def snapshot(full_name: str) -> Snapshot:
    """Stream the source archive and keep the tree, manifests and source text."""
    snap = Snapshot()
    with tempfile.TemporaryFile() as tmp:  # nameless; deleted as soon as it's closed
        with httpx.stream("GET", f"https://codeload.github.com/{full_name}/tar.gz/HEAD", timeout=120,
                          follow_redirects=True, headers={"User-Agent": "jobagent"}) as r:
            r.raise_for_status()
            size = 0
            for chunk in r.iter_bytes():
                size += len(chunk)
                if size > MAX_ARCHIVE:
                    snap.truncated = True
                    break
                tmp.write(chunk)
        tmp.seek(0)
        try:
            tar = tarfile.open(fileobj=tmp, mode="r|gz")
            for m in tar:
                if not snap.sha:  # git archive stores the commit id in the pax global header
                    snap.sha = tar.pax_headers.get("comment", "")
                parts = PurePosixPath(m.name).parts[1:]  # drop "<repo>-HEAD/"
                if not parts or any(p in SKIP_DIRS or p.startswith(".") and p != ".github" for p in parts[:-1]):
                    continue
                path = "/".join(parts)
                if m.isdir():
                    continue
                snap.tree.append(path)
                ext = PurePosixPath(path).suffix.lower()
                if not m.isfile() or m.size > MAX_FILE or not (MANIFESTS.match(path) or ext in SOURCE_EXT):
                    continue
                f = tar.extractfile(m)
                text = f.read().decode("utf-8", "replace") if f else ""
                if MANIFESTS.match(path) or MANIFESTS.match(parts[-1]):
                    snap.manifests[path] = text[:3000]
                elif ext == ".ipynb":
                    snap.sources[path] = _notebook_text(text)
                else:
                    snap.sources[path] = text
        except (tarfile.TarError, EOFError, OSError):
            snap.truncated = True  # cut-off archive: keep whatever was read
    return snap


def code_context(snap: Snapshot) -> str:
    """Entry points and shallow files first, each file's head, within CODE_BUDGET characters."""
    def priority(path: str) -> tuple:
        return (not ENTRY.search(path), path.count("/"), -min(len(snap.sources[path]), 20_000))
    out, used = [], 0
    for path in sorted(snap.sources, key=priority):
        text = snap.sources[path].strip()
        if not text:
            continue
        chunk = f"### {path}\n{text[:FILE_HEAD]}" + ("\n... (truncated)" if len(text) > FILE_HEAD else "")
        if used + len(chunk) > CODE_BUDGET:
            out.append(f"(+ {len(snap.sources) - len(out)} more source files not shown)")
            break
        out.append(chunk)
        used += len(chunk)
    return "\n\n".join(out)


# --- the card ------------------------------------------------------------------------------------------------

class CardOut(BaseModel):
    title: str = Field(max_length=200, description="The project's name as a person would present it")
    summary: str = Field(max_length=2500, description="Resume-ready, 3 sentences, <= 550 characters: the problem, "
                         "the solution, the system built. No caveats about the repository here")
    readme: str = Field(max_length=9000, description="Markdown README written from the code (<= 5000 characters): "
                        "overview, architecture/components, how it works, data, how to run, results if evidenced")
    tech_stack: list[str] = Field(max_length=15, description="Languages, frameworks, libraries, services, tools "
                                  "actually used in the code (most important first), short names")
    fields: list[str] = Field(max_length=6, description="Technical fields, e.g. robotics, computer vision, "
                              "data engineering, LLM agents, web development")
    results: list[str] = Field(default_factory=list, max_length=5,
                               description="Concrete results the code/README/logs state (metrics, wins); none invented")
    evidence_notes: str = Field(default="", max_length=600, description="Caveats: e.g. README only, no code, "
                                "incomplete, claims not visible in code")
    kind: Literal["project", "hackathon", "research", "coursework", "tutorial", "library", "config", "trivial"]
    substance: int = Field(ge=1, le=5, description="1 = toy/empty/script, 5 = complete multi-component system")


PROMPT = """You read a GitHub repository (file tree, dependency manifests, original README if any, main source
files) and write a card about it for the owner's resume agent. Base everything on the evidence shown: never invent
features, datasets, metrics or results. If the README claims something the code doesn't show, you may still
mention it as the README's claim. Be specific (models, algorithms, APIs, data flow), not generic.
The summary must read as résumé material: the problem the project addresses, the approach/solution, and the
system that was built (components and how they connect)."""


def build_card(repo: github.Repo, model) -> dict:
    snap = snapshot(repo.full_name)
    tree = snap.tree[:300] + ([f"... {len(snap.tree) - 300} more files"] if len(snap.tree) > 300 else [])
    text = (f"REPOSITORY: {repo.full_name} ({'owner' if repo.owned else 'contributor'})\n"
            f"Description: {repo.description}\nLanguages: {', '.join(repo.languages) or repo.language}\n"
            f"Topics: {', '.join(repo.topics)}\n\nFILE TREE:\n" + "\n".join(tree) +
            "\n\nMANIFESTS:\n" + "\n\n".join(f"### {p}\n{t}" for p, t in list(snap.manifests.items())[:8]) +
            f"\n\nORIGINAL README:\n{repo.readme[:5000] or '(none)'}\n\nSOURCE FILES:\n{code_context(snap)}")
    out: CardOut = model.with_structured_output(CardOut, method="json_schema").invoke(
        [SystemMessage(with_today(PROMPT)), HumanMessage(text)])
    return {"full_name": repo.full_name, "url": repo.url, "owned": repo.owned, "pushed_at": repo.pushed_at,
            "head_sha": snap.sha or head_sha(repo.full_name),
            "analyzed_at": datetime.now(UTC).isoformat(timespec="seconds"), "files": len(snap.tree),
            "source_files": len(snap.sources), "archive_truncated": snap.truncated,
            "had_readme": bool(repo.readme.strip()), **out.model_dump()}


def head_sha(full_name: str) -> str:
    """The default branch's commit SHA, from the git smart-HTTP ref advertisement (what `git ls-remote` reads;
    not counted against the API rate limit). '' if unavailable."""
    try:
        r = httpx.get(f"https://github.com/{full_name}.git/info/refs", params={"service": "git-upload-pack"},
                      headers={"User-Agent": "git/2.45"}, timeout=30, follow_redirects=True)
    except httpx.HTTPError:
        return ""
    m = re.search(rb"([0-9a-f]{40}) HEAD\x00", r.content) if r.status_code == 200 else None
    return m.group(1).decode() if m else ""


def needs_analysis(repo: github.Repo, card: dict | None) -> bool:
    """New repo, or new commits on the default branch since the card was written. pushed_at is the free first
    check (unchanged → nothing to do); when it moved, only a different head commit SHA means re-analysis — a push
    to another branch just updates the stored pushed_at."""
    if card is None:
        return True
    if card.get("pushed_at") == repo.pushed_at and card.get("head_sha"):
        return False
    sha = head_sha(repo.full_name)
    if sha and sha == card.get("head_sha"):
        card["pushed_at"] = repo.pushed_at
        save(card)
        return False
    if sha and not card.get("head_sha") and card.get("pushed_at") == repo.pushed_at:
        card["head_sha"] = sha  # card from before SHAs were stored: record it, nothing changed
        save(card)
        return False
    return True


def load(full_name: str) -> dict | None:
    p = card_dir() / f"{_key(full_name)}.json"
    return json.loads(p.read_text()) if p.exists() else None


def save(card: dict) -> None:
    d = card_dir()
    (d / f"{_key(card['full_name'])}.json").write_text(json.dumps(card, indent=1, ensure_ascii=False))
    (d / f"{_key(card['full_name'])}.README.md").write_text(card["readme"])


def _embed_text(card: dict) -> str:
    return (f"{card['title']}: {card['summary']} Stack: {', '.join(card['tech_stack'])}. "
            f"Fields: {', '.join(card['fields'])}. {card['readme'][:2500]}")


def refresh(user: str, token: str | None = None, workers: int = 6, force: bool = False, rediscover: bool = True,
            log=print) -> list[dict]:
    """Cards for all of `user`'s repos (own + contributed): new or changed repos are (re)analysed. With
    rediscover=False the repo list from the last day is reused (no API calls, safe for parallel runs)."""
    s = get_settings()
    repos = github.discover_repos(user, token, refresh=rediscover, log=log)
    with ThreadPoolExecutor(8) as pool:  # cheap checks, in parallel
        flags = list(pool.map(lambda r: force or needs_analysis(r, load(r.full_name)), repos))
    stale = [r for r, f in zip(repos, flags) if f]
    log(f"repo cards: {len(repos)} repos, {len(repos) - len(stale)} up to date, {len(stale)} to analyse")
    model = ChatOpenAI(model="glm-5-3", base_url=s.openai_base_url, api_key=s.openai_api_key, max_retries=1,
                       timeout=400, temperature=0, max_tokens=12000)

    def one(repo: github.Repo) -> str:
        t0 = datetime.now(UTC)
        try:
            save(build_card(repo, model))
            return f"  {repo.full_name}: ok ({(datetime.now(UTC) - t0).seconds}s)"
        except Exception as e:  # keep going; the repo is retried on the next run
            return f"  {repo.full_name}: FAILED {type(e).__name__}: {str(e)[:120]}"

    with ThreadPoolExecutor(workers) as pool:
        for line in pool.map(one, stale):
            log(line)
    cards = []
    for r in repos:
        if c := load(r.full_name):
            if c.get("created_at") != r.created_at or c.get("owned") != r.owned:  # metadata only, no re-analysis
                c["created_at"], c["owned"] = r.created_at, r.owned
                save(c)
            cards.append(c)
    missing = [c for c in cards if "embedding" not in c]
    if missing:
        vecs = llm.embeddings().embed_documents([_embed_text(c) for c in missing])
        for c, v in zip(missing, vecs):
            c["embedding"] = v
            save(c)
    return cards


def rank(cards: list[dict], job_text: str, k: int = 8, min_substance: int = 2) -> list[tuple[float, dict]]:
    pool = [c for c in cards if c["substance"] >= min_substance and c["kind"] not in ("config", "trivial")
            and not c["full_name"].lower().endswith(".github.io")]
    jv = llm.embeddings().embed_query(job_text)

    def cos(a, b):
        return sum(x * y for x, y in zip(a, b)) / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))
    return sorted(((cos(jv, c["embedding"]), c) for c in pool), key=lambda x: -x[0])[:k]


if __name__ == "__main__":
    import sys

    from jobagent.tools.vault import open_vault

    tex = (get_settings().data_dir / "templates" / "resume.tex").read_text()
    refresh(github.profile_user(tex), (open_vault().get("github") or {}).get("token"),
            workers=int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 6, force="--force" in sys.argv)
