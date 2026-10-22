# Step 5 — Bootstrap (global default action)

## Run it

```bash
docker compose exec agent jobagent bootstrap                # check/repair everything; resumes after a crash
docker compose exec agent jobagent step bootstrap-check     # idempotency + simulated crash/resume
```

Gate passed 2026-10-22: three container recreates, each `jobagent bootstrap` with every node ok and
`changed: nothing`. The check also passed: first run, idempotent second run, and a crash after `browser`
resumed with only `browser → linkedin → overleaf` re-run.

## How it works

`graph/bootstrap.py` is a LangGraph `StateGraph`: harness → gmail → browser → linkedin → overleaf → summarize.

| Node | Checks | Acts only if missing |
|---|---|---|
| harness | data/state/outputs writable, vault readable | creates `missing-agent-capabilities.txt` |
| gmail | vault `status=ready` + IMAP login | runs step 1 setup for the stored address |
| browser | stale profile lock, Gmail opens signed in | removes a stale lock; signed-out → warning (`browser google-login` by hand) |
| linkedin | vault entry | none — warning while step 3 is skipped |
| overleaf | logged in | automatic Google login; failure → warning |

`summarize` emits the `SystemMessage` ("Harness bootstrap …") that later graphs start from. State is checkpointed
with `SqliteSaver` in `state/checkpoints.sqlite`; the running thread id is kept in the `kv` table until the run
finishes, so the next `jobagent bootstrap` after a crash resumes it (LangGraph re-runs only the unfinished node).
`JOBAGENT_BOOTSTRAP_CRASH_AFTER=<node>` simulates a crash for testing.
