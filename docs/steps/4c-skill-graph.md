# Step 4c: the skill graph

A persistent, versioned graph in `state/skills/` (`graph.json`, `history.jsonl`, `vectors.json`, `approved.json`, `graph.mmd`):

```
Role (data/target_roles.yaml)  -->  Duty (a kind of work)  -->  Skill (a technology)       many to many at both levels
e.g.  ML Engineer --> model training --> PyTorch, TensorFlow, HuggingFace
                  --> model deployment and hosting --> Triton Inference Server, vLLM
      Software Engineer --> databases --> PostgreSQL, MongoDB
```

Two skills are neighbours when they share a duty; edge weights count the postings that support a link.

Model categories must be one of `language`, `framework`, `database`, `cloud`, `tool`, or `method`. A build migrates blank
legacy categories to the generic `tool` default and records that fallback explicitly. A later classified observation can replace
the default. This migration preserves skill identity, ownership and edges.

**Growing it.** `jobagent skills build` reads the roles file and every saved posting (`outputs/*/*/jd.json`; the role is the queue's match,
else the first role whose title pattern fits), asks the fast model once per posting to group the posting's technologies by duty, merges
near-identical DUTY names by embedding similarity (`skills_duty_merge`), then folds duplicate duties with one extra call. Skills are
identified by name and recorded alias only: a similar technology is a different skill, because ownership follows identity. Rerunning skips postings already in the graph. Every posting the pipeline scrapes is added automatically, and a role added to
`data/target_roles.yaml` appears with no duties until `jobagent skills sync` collects postings for it (scrape only, never apply; it first
looks at the queue, then re-runs discovery over the companies already known) and their postings give it duties and skills.

**Using it.** For a posting, the skills it names lead to their duties and from there to the neighbours. A neighbour the candidate already
has (template Technical Skills, `master_profile.yaml`, GitHub repo stacks, `jobagent skills approve`) may fill spare room on a skills line
(the one-page and layout checks still decide); any other neighbour is only written to `skill_suggestions.json` and listed on the review
screen as "Suggested skills (not added)", and generated lines have such skills removed. Nothing the candidate lacks is added by the graph,
and `master_profile.yaml` is never edited (approvals live in `state/skills/approved.json`).

Commands: `skills build|sync|show [--role]|explain SKILL|suggest JOB_DIR|approve SKILL|export [--role]`. Switch and limits: `config/skills.yaml`
(`skills_use_graph`, `skills_max_neighbours`, `skills_duty_merge`, `skills_model`). A graph failure is one log line; it never stops a
run. The tests keep the graph off (`SKILLS_USE_GRAPH=false` in `tests/conftest.py`) unless a test is about it.

## Behaviour fixed after review (2026-11-16)
- **Ownership is enforced at the render**, not only in the prompt: every writer and refresh (single writer, multi-agent drafts, ATS refresh) renders
  through `tailor.apply_fill`, which removes any related skill listed for review (term, name in parentheses, `tools_for`, aliases) and drops
  a category that ends up empty; `documents` re-checks the finished resume and a violation blocks it.
- **Skills are identified by name and alias only.** Embedding similarity places duties (which carry no ownership) but never merges two
  technologies, so TensorFlow and PyTorch stay separate and owning one never makes the other "owned".
- **Concurrent runs:** the graph is read-modified-written under a file lock (`state/skills/.lock`) and saved atomically with a version taken
  from committed state; the model call happens outside the lock. A pipeline that cannot get the lock within 5 s logs it and moves on.
- **A posting read again with changed content** replaces its old support (a content fingerprint of skills, responsibilities and role).
- **The "general tooling" bucket** (skills a posting never tied to a duty) creates no neighbours.
- **`skill_suggestions.json`** carries fingerprints of the posting, graph and your skills and is rewritten every time (empty too); a stale one
  is ignored by the review screen and the guard.
- **The resume stage's cache inputs** include the graph's effect (`jobagent tailor` rebuilds after you approve a skill, the graph grows or
  you switch it off) without `--force`.
- `jobagent skills sync` exits non-zero when any scrape failed and says which roles found nothing.

## Place names are not skills (2026-11-19)
Postings often list places beside their technologies (pay-transparency sections: "NY, WA, CA"), and the first live runs showed "NY", "WA" and "Seattle" among the
review suggestions. They never reached a resume (only owned skills can), but they were noise. Now US states (names and capitalised codes), work-mode words
(remote, hybrid) and the posting's own locations are dropped when a posting is read, and `jobagent skills prune` removes the ones older builds stored.
