# Step 4b — GitHub (optional)

Added 2026-10-23. The resume template's **Projects** section is filled from GitHub (step 9), so the agent needs
the user's repositories, including other people's repos the user contributed to.

## Run it

```bash
docker compose exec agent jobagent step github          # log in with the agent's Google session
docker compose exec agent jobagent step github-check    # session + repo discovery for the resume's account(s)
```

## How it works

- **Login:** `github.com/login` → "Continue with Google", using the Google session from step 2 (Google's account
  chooser/consent are clicked through). If GitHub asks to verify the device, the code is read from the agent's
  inbox (`github.com` is recorded as an agent domain first) and the mail is trashed after the login succeeds.
  Anything else (linking, 2FA) is handed to the user in noVNC; if Google refuses, plain Chrome is opened for a
  manual login. The username is read from `meta[name=user-login]` / `octolytics-actor-login`, which only fill in
  once the dashboard has loaded.
- **Accounts:** the agent's Gmail logs in as `Bhavya2409`; the resume header links `BhavyaShah1234`. Repo
  discovery covers both (the resume's account first).
- **Repos** (`tools/github.discover_repos`, cached a day in `state/github/<user>.json`): own non-fork repos from
  `/users/<u>/repos` (description, language, topics, last push), repos contributed to via PR and commit search
  (`-user:<u>`), READMEs from `raw.githubusercontent.com`. Without a token GitHub allows 60 API calls/hour: one per
  account plus one per contributed repo. `jobagent vault set github token` (read-only fine-grained token) raises
  that to 5,000/hour and adds languages and GraphQL `repositoriesContributedTo`.
- Bootstrap gained a `github` node after `overleaf`; failure is a warning, never a blocker.

## Learned while building

- The first login succeeded but was reported as stuck: the user metas are empty right after the OAuth redirect.
  The check now reloads the dashboard and polls for up to 3 s before handing off.
- Fetching languages + README through the API cost 2 extra calls per repo and exhausted the unauthenticated
  limit on the first run; READMEs now come from raw.githubusercontent.com and languages only with a token.
