# Step 21: configuration files

Behaviour you may want to change without touching code lives in `config/<concern>.yaml` (mounted read-only at `/app/config`):
`models`, `forms`, `ats`, `browser`, `queue`, `learning`, `logging`. Each file lists only its own settings (the example files are
commented out; uncomment a line to change it).

Precedence, highest first: **environment variable > `.env` > `config/*.yaml` > the default in `config.py`**.

- Paths, credentials (`OPENAI_API_KEY`, `VAULT_KEY`), `STATE_DIR` and the display are **environment-only**: a YAML file that names
  one (or a setting that belongs to another file) is ignored and `config show` reports it as `rejected`.
- Numbers are range-checked when settings load (lease >= 30 s, window >= 1 day, pages 1-60, ATS target 0-100, retries and call budgets
  bounded); a bad value stops the command with the field's name before any work starts. `pacing_max_per_company: 0` turns pacing off.

```
docker exec jobagent jobagent config show            # every setting: effective value and where it came from
docker exec jobagent jobagent config show queue      # one file's settings
```

Hard-coded values that are NOT yet configurable (candidates for a later pass): per-portal selectors and waits in `steps/apply.py` and
`filling/`, discovery thresholds in `tools/matching.py`, ATS site timeouts in `tools/ats_sites.py`, the challenge hand-off timings in
`tools/challenge.py`, the 15-minute heartbeat cadence in `tools/queue_runner.py`.
