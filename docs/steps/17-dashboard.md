# Step 17: the dashboard

`jobagent dashboard` serves a one-page web UI on this machine (default `http://127.0.0.1:8765/?t=<token>`; the address is printed, the token is
kept in `state/dashboard/token`, readable by you only).

**Built with.** Front end: **React 18** (Vite, plain JSX, no TypeScript), in `frontend/` (components in `src/components`, one file per tab in `src/views`, polling in
`src/hooks.js`, the only network code in `src/api.js`). Server: Python standard library (`http.server`), no web framework and no new Python dependency. The React app is
built into `src/jobagent/dashboard/static/` and **committed**, so the machine that runs the agent needs no Node and the agent image is unchanged. Rebuild after
changing `frontend/`: `scripts/build_dashboard.sh build` (runs npm in a throwaway `node:22-slim` container; exact pinned versions, `package-lock.json`, `npm ci
--ignore-scripts`); `scripts/build_dashboard.sh test` runs the component tests (Vitest + Testing Library). A Python test checks that the committed build matches the
sources (`static/source.sha256`).

| Tab | Shows | Source |
|---|---|---|
| Overview | one page with everything: headline numbers (questions waiting, applications to review, jobs working, postings found, submitted, reply rate, wait for your approval); **Working now** (a card per job with its stages, progress bar and the current step and iteration); recently finished jobs; then the analytics: the funnel from postings found to interviews, daily activity, **Discovery** (one bar for where every posting went, then places outside the US, top companies, roles, match scores), outcomes (labels, time to a reply, reply rate by portal / role / resume score with 95% intervals) and resume-score distributions | job database, `state/hitl`, run logs, `outcome_events`, `ats_runs`, via `tools/outcome_stats.py` |
| Jobs | the same job cards with filters (working now, needs attention, finished) and a history of what each job did | `progress` records in the run logs |
| Questions | what the agent is waiting on; a text box to answer (not for secrets) | `state/hitl/pending` |
| Review | applications filled to their review screen: ready or not, blockers, hours waiting, and the exact command that opens each review | `tools/review_queue.py` |
| Queue | discovered postings and their status, role, match score, note; **a start form at the top** (add job links or companies and start `jobagent run INPUTS --through scrape\|tailor\|apply --limit N` in the background: found postings appear in the table); filter by status; select postings and **delete** the waiting ones (queued, needs review, skipped, failed, blocked) after a confirmation | `jobs`, `queue_removed`, the `run` command (step 16) |
| Runs | each run (step, job, start, lines, errors, last line, running) and its full log | `state/logs/*.jsonl` (step 22) |
| Outcomes | counts by stage and the latest outcome mails per application | `outcome_events` |

**Analytics honesty (on the Overview).** Silence is never counted as a rejection: the outcome labels are the agent's own (pending inside the cutoff, ghosted after it as a weak negative), and a reply rate is shown as "k of n" with a Wilson 95% interval. A group with fewer than 15 resolved applications is drawn grey and a banner says the figures describe what happened, not what to expect, until there are enough applications. Charts are small hand-written SVG components (`frontend/src/charts`), no charting library.

**Interaction.** Every chart and widget on the Overview answers to the pointer, the keyboard and a finger, through one tooltip layer (`frontend/src/tooltip.jsx`):
hover (or focus with Tab, or tap) a funnel step for its share of all postings, what it kept from the step above and how many dropped out; a bar for its count and
share of the list; a company for found, passed and the share that passed; a histogram bar or a stacked-bar part (which lights up with its legend row, and the
others dim); a day on the activity charts for that day's numbers (the chart also takes the arrow keys, and shows a guide column); a reply rate for what it is made
of (k of n replied, the 95% interval, and a warning when it is too thin); a stage box for its status, step and how long ago; the job bar for the whole pipeline; a headline
number for what it counts. Escape, a tap elsewhere, or scrolling hides it. Everything is also named for screen readers (`aria-label`, and a live region for the timeline).

**Responsive.** Checked in a real browser at 320, 360, 390, 600, 768, 820, 1024, 1280 and 1600 px: the page never scrolls sideways. Below 760 px the tabs become one
swipeable row, the headline numbers two columns, the panels one column, tables scroll inside their own box, SVG charts redraw at their real width (so text stays crisp), and
touch screens get larger buttons and 16 px inputs (no zoom-on-focus). The funnel, legends and Discovery panels reflow (4, 2, then 1 column).

**Pipeline progress.** Each job card shows its plan (scrape, research, documents, apply or submit, depending on how far the run goes), the state of every
stage (done, running, failed, blocked, stalled, pending), a bar for the whole job, and inside the running stage the step and iteration: for the
resume and cover letter "First draft", "Layout and quality check 2 of 4", "Fixing findings", "ATS baseline / ATS round 2 of 3", "Cover letter"; for the
single writer "Resume draft round 3 of 5"; for the form "Form page 3" and current-field counts. The agent writes these as `progress` records
(`{stage, status, label, i, n, detail, plan, job, url, title, company}`) to the run's `.jsonl` log (`jobagent.log.progress`); commands within one execution
contribute to the same card, linked by canonical job directory and execution ID. A stage that says "running" while nothing has written for 15 minutes shows as
stalled (the run was most likely killed).

**What it can change, and what it cannot.** Only two things: answering an ordinary question the agent is waiting on (secrets and submission approval require a terminal) and starting a run that stops at or before the review screen (`through` cannot be `submit`; inputs cannot start with `-`
or contain a newline; arguments are passed as a list, never through a shell). It has no route that approves or sends an application: that stays a
typed `approve` in a terminal (`jobagent submit ...`); the Review tab only shows the command.

**Guards.** Every request needs the token (the first visit with `?t=` sets an HttpOnly, SameSite=Strict cookie) and a `Host` header that names this
machine (DNS-rebinding defence). POSTs also need the cookie, `Content-Type: application/json`, the `X-Dashboard: 1` header and a same-site `Origin`.
The database is opened read-only. Agent data reaches the page only as React text, never as HTML (a scraped job title cannot inject markup; tests render a hostile title and a Python test forbids
`dangerouslySetInnerHTML`/`innerHTML`). The content policy allows only scripts and styles from the dashboard itself (no inline or eval). Log names are checked against a strict pattern, so no path can leave `state/logs`.

**Running it.** The Compose harness starts the dashboard inside `jobagent` on `http://127.0.0.1:8765`. It shares the actual agent's display, Chrome profile, writable answers, vault, configuration and output folders. Starting a job from the UI therefore runs the same installed CLI as a terminal command, rather than a second agent in a server-only container.

```bash
scripts/dashboard.sh start
scripts/dashboard.sh url
scripts/dashboard.sh logs
```

`url` prints the private access link. Open it once; the cookie authenticates later visits. `start` reuses a healthy running service. To change its mode, stop it first with `scripts/dashboard.sh stop`, then use `DASHBOARD_MODE=readonly scripts/dashboard.sh start` for a read-only view. `JOBAGENT_CONTAINER` selects a different **existing harness** explicitly. Changed source must be rebuilt and the idle harness recreated before it runs; the launcher does not install uncommitted code. Compose uses `DASHBOARD_PORT=8765` by default; an explicitly empty value disables automatic startup. `DASHBOARD_READ_ONLY=0` is interactive and `1` is read-only.

The older separate `jobagent-dashboard` container must be stopped before moving to the integrated service on the same port. Its old access cookie is separate: obtain the integrated service's link with `scripts/dashboard.sh url`.

The start form on the Queue page (the old separate Start tab; `/start` now opens the Queue) accepts the normal inputs, a scrape/tailor/apply stop point, optional ATS-scan skipping, and explicit stage refresh. It uses automatic browser fallback and retains startup output and exit status in Runs. A second dashboard launch is refused while its existing child is active. CLI runs still use their normal workflow and browser locks. Nothing started here can pass the review screen.

**Deleting from the queue.** `POST /api/queue/delete {ids}` (`tools/queue_admin.py`) removes postings that are `queued`, `needs_review`, `skipped`, `failed` or `blocked` and have no application; a running, scraped, applied or closed posting, or one with an application on record, is refused with the reason and kept. The deleted address is stored in `queue_removed`, so the next discovery run does not add it back (`jobqueue.enqueue` skips it). The route has the same guards as every POST (token, host, same-site JSON header) and is absent in read-only mode, where the page shows no form or delete controls.

Progress records carry a canonical job directory and execution ID. Same-numbered requisitions at different companies remain separate, and a retry does not inherit a previous attempt's failure. Standalone tailor/apply/ATS commands report their actual final status. The form page number has no fabricated total; the configured maximum-page budget is not a count of discovered pages. Field progress counts the current inventory and cannot change whether a field is accepted. HITL requests identify their execution, show a waiting stage, and resume through the normal file queue. Read-only viewers do not create or prune requests, including requests from a different PID namespace.

The UI polls active progress every 3–4 seconds, questions every 5 seconds, and slower tables every 6–20 seconds. It needs no manual page refresh. Failed requests show a stale-data notice and retain the last successful update time; pending requests do not accumulate or overwrite another log's response. Ordinary answer drafts survive polling. Secrets and submission-approval requests are terminal-only, enforced by the API even for older approval requests. Human security challenges remain human-operated. The Browser tab explains when fallback is using host Chrome instead of the embedded Docker desktop.

The score chart uses the same exact PDF/JD identity and complete required-provider denominator as application review. Incomplete or unverifiable packets do not become averages. Milestone bars are recorded counts rather than conversion rates between different cohorts; direct URL inputs may bypass discovery filters, and replies do not require an earlier recorded receipt.

If nothing loads, first check the harness is running, obtain the correct access link, and inspect `scripts/dashboard.sh logs`. Development fixtures remain separate: `jobagent.dashboard.demo.seed(root)` creates invented records only in the explicitly supplied test root.

**Not in this step:** approving or submitting, editing data files, secrets, authentication beyond the local token, server-pushed updates (the page polls automatically).
