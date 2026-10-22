# Step 1 — Agent email (`bhavya24091952@gmail.com`)

## Run it

```bash
docker compose exec agent jobagent vault set gmail password       # hidden prompt; never edit vault.enc
docker compose exec agent jobagent vault set gmail app_password   # from myaccount.google.com/apppasswords
docker compose exec agent jobagent step gmail --existing bhavya24091952@gmail.com
docker compose exec agent jobagent step gmail-check               # the e2e check
```

Gate passed 2026-10-21: three container recreates, each with IMAP/SMTP round-trip and the shared-inbox guard passing.

## How it works now

- **Mail-only mode.** Google refuses sign-in from the automated browser ("This browser or app may not be
  secure"). The agent does not work around that: it records `browser_session=blocked` and does everything
  over IMAP/SMTP with an app password (created by the user; needs 2-Step Verification on the account).
- **Shared inbox guard.** The account is also the user's personal throwaway inbox. The agent only reads
  codes from, and only trashes mail from, sites it used itself (`agent_domains`), and the stale sweep
  filters by sender on the IMAP server so personal mail is never downloaded.
- **Vault safety.** Saves are atomic with a `.bak` of the last good version; an empty/unreadable vault stops
  with a pointer to `jobagent vault restore`. A plaintext value written into `vault.enc` can be recovered with
  `jobagent vault import-file gmail password /app/state/vault.enc` (encrypts it, shreds the plaintext).

## Learned while building

- Google blocked automated **account creation** twice ("Sorry, we could not create your Google Account")
  and automated **sign-in** ("This browser or app may not be secure"). Both are deliberate anti-automation
  checks; the design routes around needing them (existing account + app password), not through them.
- "The setting you are looking for is not available" on the app-passwords page means 2-Step Verification
  is off.
- Google sign-in pages render after a "Loading" phase, use a `type=text name=identifier` email field, and
  leave hidden duplicates (`id=identifierId`) on later pages: match `:visible` fields and wait for each
  field to disappear before the next action. Never retry a password (lockout risk).
- Playwright converts Python regexes to JavaScript: inline `(?i)` is invalid; use `re.I`.
- Signup URLs contain `continue=...ManageAccount`: decide "signed in" by host, never by URL substrings.
- HITL request/answer files must be written atomically; requests from killed runs are pruned by PID.
- `.claude/settings.json` with `"worktree": {"bgIsolation": "none"}` lets sessions edit the main checkout
  directly (takes effect from the next session).
