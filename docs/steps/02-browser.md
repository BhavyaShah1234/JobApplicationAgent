# Step 2 — Browser profile, cookies, challenges

## Run it

```bash
docker compose exec agent jobagent browser google-login     # once: sign in by hand in plain Chrome (noVNC)
docker compose exec agent jobagent browser status           # cookies, profile size, Gmail signed in?
docker compose exec agent jobagent browser warm-up --minutes 5   # optional ordinary browsing
docker compose exec agent jobagent step browser-check       # the e2e check
```

Gate passed 2026-10-22: five container recreates, each with profile persistence (probe cookie from the
previous container still present, 6 clean launches), challenge detection/hand-off, Gmail opening signed in,
and the step 1 mail checks.

## How it works

- **Google session by hand.** Google refuses automated sign-in, so `browser google-login` opens plain Chrome
  (no automation) on the agent's profile; the user signs in once; the agent's automated browser then reuses
  that session. If Google later signs it out, `browser-check` reports `signed-out` — repeat `google-login`.
- **Challenges are handed over, never solved.** `tools/challenge.detect()` recognises Cloudflare/Turnstile,
  reCAPTCHA, hCaptcha, Arkose, Google "unusual traffic", PerimeterX, DataDome and access-denied pages
  (invisible reCAPTCHA badges are ignored). `goto()` captures the page, asks the user to solve it in noVNC and
  resumes by itself when it's gone; it also backs off exponentially (with jitter, honouring Retry-After) on
  429/5xx.
- **Profile health.** Stale `SingletonLock` files from killed runs are removed; a lock held by a live Chrome
  raises `ProfileInUse`. The entrypoint clears stale locks and stale HITL requests at container start.

## Learned while building

- Chrome's sandbox needs user namespaces, which Docker blocks: plain Chrome needs `--no-sandbox` (Playwright
  adds it for the automated browser). Plain Chrome's output goes to `state/logs/plain-chrome.log` and a crash
  on start raises instead of leaving an empty noVNC screen.
- PIDs restart from 1 in each container, so PID-based HITL pruning can't detect requests from an earlier
  container; clear them at startup.
- `page.set_content()` keeps the JS realm: tests that inject top-level `const`s need a fresh tab per test.
