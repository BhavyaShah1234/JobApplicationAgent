# Step 4c: the skill graph

A persistent, versioned graph in `state/skills/` (`graph.json`, `history.jsonl`, `vectors.json`, `approved.json`, `graph.mmd`):

```
Role (data/target_roles.yaml)  -->  Duty (a kind of work)  -->  Skill (a technology)       many to many at both levels
e.g.  ML Engineer --> model training --> PyTorch, TensorFlow, HuggingFace
                  --> model deployment and hosting --> Triton Inference Server, vLLM
      Software Engineer --> databases --> PostgreSQL, MongoDB
```

Two skills are neighbours when they share a duty; edge weights count the postings that support a link.

**Growing it.** `jobagent skills build` reads the roles file and every saved posting (`outputs/*/*/jd.json`; the role is the queue's match,
else the first role whose title pattern fits), asks the fast model once per posting to group the posting's technologies by duty, merges
near-identical duty and skill names by embedding similarity (thresholds in `config/skills.yaml`), then folds duplicate duties with one extra
call. Rerunning skips postings already in the graph. Every posting the pipeline scrapes is added automatically, and a role added to
`data/target_roles.yaml` appears with no duties until `jobagent skills sync` collects postings for it (scrape only, never apply; it first
looks at the queue, then re-runs discovery over the companies already known) and their postings give it duties and skills.

**Using it.** For a posting, the skills it names lead to their duties and from there to the neighbours. A neighbour the candidate already
has (template Technical Skills, `master_profile.yaml`, GitHub repo stacks, `jobagent skills approve`) may fill spare room on a skills line
(the one-page and layout checks still decide); any other neighbour is only written to `skill_suggestions.json` and listed on the review
screen as "Suggested skills (not added)", and generated lines have such skills removed. Nothing the candidate lacks is added by the graph,
and `master_profile.yaml` is never edited (approvals live in `state/skills/approved.json`).

Commands: `skills build|sync|show [--role]|explain SKILL|suggest JOB_DIR|approve SKILL|export [--role]`. Switch and limits: `config/skills.yaml`
(`skills_use_graph`, `skills_max_neighbours`, `skills_duty_merge`, `skills_skill_merge`). A graph failure is one log line; it never stops a
run. The tests keep the graph off (`SKILLS_USE_GRAPH=false` in `tests/conftest.py`) unless a test is about it.
