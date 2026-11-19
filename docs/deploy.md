# Installing, running and updating the agent (step 18)

Everything below is done from the repository folder with one script, `scripts/agentctl`; run it without arguments to see the commands. Nothing it does
submits an application.

## What runs where
- **One container**, `jobagent` (image `jobagent-harness`), started by `docker compose` on the **host network**. It holds the code, Chrome, LaTeX and the
  dashboard; your files stay on the host and are mounted in: `data/` (your personal information, templates, answers), `state/` (the encrypted vault, the job
  database, the browser profile, logs), `outputs/` (resumes, cover letters, application results) and `config/` (read-only settings).
- **Ports, all on 127.0.0.1 only:** 6080 the agent's browser (noVNC), 8765 the dashboard (`DASHBOARD_PORT` in `.env`; empty turns it off).
- **Nothing is exposed to your network.** To reach it from a phone see `docs/steps/19-phone.md`.

## First install
```
git clone <this repository> && cd JobApplicationAgent
scripts/agentctl setup          # creates .env with a NEW random VAULT_KEY, and data/ state/ outputs/
cp data/applicant.example.yaml data/applicant.yaml        # then fill in the files named in README.md
export OPENAI_API_KEY=...       # the model endpoint's key; compose refuses to start without it
scripts/agentctl build          # first build takes several minutes (host network, so a VPN's DNS works)
scripts/agentctl up             # starts the container and prints the dashboard address
scripts/agentctl doctor         # checks everything: docker, keys, folders, ports, image vs this checkout
```

## Every day
`scripts/agentctl status` (what is running, which commit the image was built from, mounts, the dashboard address), `url`, `logs`, `shell`, `down`.

## Updating
```
git pull
scripts/agentctl update
```
`update` does, in order: a health check, **a backup**, keeps the running image as `jobagent-harness:previous`, builds the new image (labelled with the commit it
came from), recreates the container, and smoke-tests the new one (the command line starts, `config show` works, the dashboard and skill graph import).
If the smoke test fails it **goes back to the previous image by itself** and exits 1. It refuses to run while a job is in progress inside the container
(recreating it would cut the run off); wait, or use `--force`. `scripts/agentctl rollback` does the same return by hand. Old images are never deleted.

## Backup and restore
`scripts/agentctl backup` writes `backups/agent-<time>.tar.gz` (mode 600, with a checksum) holding `data/` and `state/`; `--with-outputs` adds `outputs/`.
It leaves out `.env`, the browser profile and the logs. **The VAULT_KEY is not in the archive on purpose**: an archive next to its own key would make the
encryption pointless. Store the key somewhere safe (a password manager); without it `state/vault.enc` cannot be read.
`scripts/agentctl restore FILE --yes` puts an archive back, but only while the container is stopped, after checking its checksum, and the folders it replaces
are moved to `data.before-restore-<time>` / `state.before-restore-<time>`, not deleted.

## Secrets: where each one lives
| Secret | Where | Never in |
|---|---|---|
| `VAULT_KEY` (decrypts the vault) | `.env` (mode 600), and your own safe copy | git, images, backups |
| `OPENAI_API_KEY` | your shell when you run compose | files in the repository |
| portal passwords, GitHub token, app passwords | `state/vault.enc` (encrypted) | `.env`, logs, the dashboard |
| your personal information | `data/` | git (ignored), images (`.dockerignore`) |

## When something is wrong
- `scripts/agentctl doctor` names the failing check. Typical: not in the `docker` group; `OPENAI_API_KEY` not exported; `.env` without a `VAULT_KEY`.
- A vault that will not open after restoring `.env` means the key is not the one that encrypted it.
- Builds failing to download packages behind a VPN: `agentctl build` already uses the host network; check the VPN is up.
- Port 8765 or 6080 busy: another dashboard or noVNC is running; stop it or change `DASHBOARD_PORT` in `.env`.
