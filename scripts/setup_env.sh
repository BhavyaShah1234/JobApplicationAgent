#!/usr/bin/env bash
# Create .env from .env.example with a fresh VAULT_KEY, and the host folders that get bind-mounted.
set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p data/templates state outputs

if [ -f .env ]; then
    echo ".env already exists; leaving it unchanged."
else
    # Fernet key = urlsafe base64 of 32 random bytes.
    key=$(head -c 32 /dev/urandom | base64 | tr '+/' '-_')
    sed "s|^VAULT_KEY=.*|VAULT_KEY=${key}|" .env.example > .env
    chmod 600 .env
    echo "Created .env with a new VAULT_KEY. Back it up: without it state/vault.enc can't be decrypted."
fi

[ -n "${OPENAI_API_KEY:-}" ] || echo "Warning: OPENAI_API_KEY isn't set in this shell; docker compose needs it."
