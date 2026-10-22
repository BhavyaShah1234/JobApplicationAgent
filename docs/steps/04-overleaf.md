# Step 4 — Overleaf (optional)

Step 3 (LinkedIn) was skipped by the user for now (2026-10-22).

## Run it

```bash
docker compose exec agent jobagent step overleaf          # log in (signs up with Google the first time)
docker compose exec agent jobagent step overleaf-check    # round-trip a real project, then delete it
```

Gate passed 2026-10-22: three container recreates, each still logged in and with a full round-trip — project
created from `data/templates/resume.tex`, compiled on Overleaf (2 pages, text identical to local latexmk),
read back as zip (identical after stripping Overleaf's banner), test project deleted.

## How it works

- **Login:** `/auth/google?intent=sign_in` with the Google session from step 2. "Not registered" → retried with
  `intent=sign_up` (creates the account). Post-sign-up upsell pages (`/registration/...`) are skipped; the account
  stays on the free plan. If Google refused the automated browser, the login is handed to the user in plain Chrome.
- **Backend:** account features show no Git integration (free plan), so the backend is `browser`: projects are
  created via the official "Open in Overleaf" endpoint (`POST /docs`, `encoded_snip[]`/`snip_name[]`), compiled via
  `POST /project/<id>/compile` (CSRF from `meta[name=ol-csrfToken]`), downloaded via the returned output URL and
  `/project/<id>/download/zip`, deleted via `DELETE /project/<id>` — all with the browser's own cookies.
- The MCP/Git backend (`tamirsida/overleaf_mcp`) would only apply on a plan with Git integration.

## Learned while building

- Projects created via `/docs` get a "Welcome to Overleaf" comment block prepended; `strip_overleaf_banner()` removes
  it on every read-back so it can never leak into a pulled resume.
- The `/docs` form is posted from an Overleaf page (same-site) so session cookies are sent.
