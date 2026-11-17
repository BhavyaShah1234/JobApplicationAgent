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
| Queue | discovered postings and their status, role, match score, note | `jobs` |
| Runs | each run (step, job, start, lines, errors, last line, running) and its full log | `state/logs/*.jsonl` (step 22) |
| Outcomes | counts by stage and the latest outcome mails per application | `outcome_events` |
| Start a run | `jobagent run INPUTS --through scrape\|tailor\|apply --limit N` in the background | the `run` command (step 16) |

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
single writer "Resume draft round 3 of 5"; for the form "Form page 3 of 15". The agent writes these as `progress` records
(`{stage, status, label, i, n, detail, plan, job, url, title, company}`) to the run's `.jsonl` log (`jobagent.log.progress`); every command that touches a job
contributes to the same card, linked by the job folder name or posting address. A stage that says "running" while nothing has written for 15 minutes shows as
stalled (the run was most likely killed).

**What it can change, and what it cannot.** Only two things: answering a question the agent is waiting on (a secret question is refused with the
terminal command instead) and starting a run that stops at or before the review screen (`through` cannot be `submit`; inputs cannot start with `-`
or contain a newline; arguments are passed as a list, never through a shell). It has no route that approves or sends an application: that stays a
typed `approve` in a terminal (`jobagent submit ...`); the Review tab only shows the command.

**Guards.** Every request needs the token (the first visit with `?t=` sets an HttpOnly, SameSite=Strict cookie) and a `Host` header that names this
machine (DNS-rebinding defence). POSTs also need the cookie, `Content-Type: application/json`, the `X-Dashboard: 1` header and a same-site `Origin`.
The database is opened read-only. Agent data reaches the page only as React text, never as HTML (a scraped job title cannot inject markup; tests render a hostile title and a Python test forbids
`dangerouslySetInnerHTML`/`innerHTML`). The content policy allows only scripts and styles from the dashboard itself (no inline or eval). Log names are checked against a strict pattern, so no path can leave `state/logs`.

**Running it.** The dashboard is a separate process from the agent; it must be started, and it must be able to see the agent's `data/`, `state/` and
`outputs/` folders. Three ways:

1. `scripts/dashboard.sh start` (recommended): builds an image from this checkout the first time, then starts a container `jobagent-dashboard` on
   the host network, so `http://127.0.0.1:8765/` works like noVNC does. By default it mounts your folders **read-only** (a view that cannot change
   anything); `DASHBOARD_MODE=interactive scripts/dashboard.sh start` mounts `state/` and `outputs/` writable so questions can be answered and
   runs started from the page. `scripts/dashboard.sh url` prints the address with its access token; `stop` removes the container.
   `JOBAGENT_HOME=/path/to/checkout` points it at another folder holding `data/ state/ outputs/`.
2. Inside the agent container, once its image contains this code: `docker exec -d jobagent jobagent dashboard`, then
   `docker exec jobagent jobagent dashboard --print-url`; or set `DASHBOARD_PORT=8765` in `.env` and the entrypoint starts it with the harness.
3. On the host: `jobagent dashboard` (needs the package installed there).

If the page shows nothing: (a) nothing is listening (`ss -ltn | grep 8765`; the container must exist and use `--network host`, a bridge network
makes 127.0.0.1 unreachable), (b) you opened the address without `?t=<token>` (you get a page saying so), (c) another program holds the port (the
dashboard says so; use `--port`). Reaching it from a phone needs a decision on exposure and is step 19, not this one.
Development without touching real data: `jobagent.dashboard.demo.seed(root)` writes invented companies, a queue, two applications at review, two
questions, runs and outcomes.

**Not in this step:** approving or submitting, editing data files, secrets, authentication beyond the local token, live push updates (the page polls
every 6 s).
