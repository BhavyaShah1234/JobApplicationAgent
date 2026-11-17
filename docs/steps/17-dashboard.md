# Step 17: the dashboard

`jobagent dashboard` serves a one-page web UI on this machine (default `http://127.0.0.1:8765/?t=<token>`; the address is printed, the token is
kept in `state/dashboard/token`, readable by you only). Standard library only: no new dependency, no image change.

| Tab | Shows | Source |
|---|---|---|
| Overview | counts: questions waiting, applications to review (and how many are ready), runs active, queue / application / outcome totals; link to the noVNC browser view | job database, `state/hitl`, run logs |
| Questions | what the agent is waiting on; a text box to answer (not for secrets) | `state/hitl/pending` |
| Review | applications filled to their review screen: ready or not, blockers, hours waiting, and the exact command that opens each review | `tools/review_queue.py` |
| Queue | discovered postings and their status, role, match score, note | `jobs` |
| Runs | each run (step, job, start, lines, errors, last line, running) and its full log | `state/logs/*.jsonl` (step 22) |
| Outcomes | counts by stage and the latest outcome mails per application | `outcome_events` |
| Start a run | `jobagent run INPUTS --through scrape\|tailor\|apply --limit N` in the background | the `run` command (step 16) |

**What it can change, and what it cannot.** Only two things: answering a question the agent is waiting on (a secret question is refused with the
terminal command instead) and starting a run that stops at or before the review screen (`through` cannot be `submit`; inputs cannot start with `-`
or contain a newline; arguments are passed as a list, never through a shell). It has no route that approves or sends an application: that stays a
typed `approve` in a terminal (`jobagent submit ...`); the Review tab only shows the command.

**Guards.** Every request needs the token (the first visit with `?t=` sets an HttpOnly, SameSite=Strict cookie) and a `Host` header that names this
machine (DNS-rebinding defence). POSTs also need the cookie, `Content-Type: application/json`, the `X-Dashboard: 1` header and a same-site `Origin`.
The database is opened read-only. Agent data reaches the page only through `textContent`, never as HTML (a scraped job title cannot inject markup;
a test forbids `innerHTML`). Log names are checked against a strict pattern, so no path can leave `state/logs`.

**Running it.** In the container: `docker exec -d jobagent jobagent dashboard` (the container uses host networking, so the page is at
`http://127.0.0.1:8765/`; read the token with `docker exec jobagent cat /app/state/dashboard/token`). Reaching it from a phone needs a decision on
exposure and is step 19, not this one. Development without touching real data: `jobagent.dashboard.demo.seed(root)` writes invented companies, a
queue, two applications at review, two questions, runs and outcomes.

**Not in this step:** approving or submitting, editing data files, secrets, authentication beyond the local token, live push updates (the page polls
every 6 s).
