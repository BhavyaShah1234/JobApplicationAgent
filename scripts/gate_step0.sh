#!/usr/bin/env bash
# Step 0 stability gate: N clean rebuild → recreate → verify rounds in a row.
# Needs OPENAI_API_KEY (Voyager) in the environment and host Ollama running.
set -euo pipefail
cd "$(dirname "$0")/.."
rounds="${1:-3}"

for round in $(seq "$rounds"); do
    echo "=== round $round/$rounds: rebuild ==="
    scripts/build.sh --quiet >/dev/null
    docker compose down --timeout 5 >/dev/null 2>&1 || true
    docker compose up -d >/dev/null
    for _ in $(seq 60); do curl -fs -o /dev/null http://127.0.0.1:6080/vnc.html && break; sleep 0.5; done
    curl -fs -o /dev/null http://127.0.0.1:6080/vnc.html && echo "noVNC: ok" || { echo "noVNC: FAIL"; exit 1; }
    docker compose exec -T agent pytest -q tests/unit | tail -1
    docker compose exec -T -e COLUMNS=200 agent jobagent step harness | grep -E "│ |passed|failed"
    docker compose exec -T agent pytest -q -m e2e tests/e2e/test_harness.py | tail -1
done
echo "=== gate passed: $rounds rounds ==="
