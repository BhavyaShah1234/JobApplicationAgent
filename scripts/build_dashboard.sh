#!/usr/bin/env bash
# The dashboard front end is a React app (frontend/, Vite + React 18). It is built into src/jobagent/dashboard/static/, which is committed, so
# the machine that runs the agent needs no Node. This script runs npm in a throwaway Node container (no Node needed on the host either).
#
#   scripts/build_dashboard.sh install   resolve the pinned dependencies and write frontend/package-lock.json
#   scripts/build_dashboard.sh test      run the component tests (Vitest)
#   scripts/build_dashboard.sh build     test-free production build into src/jobagent/dashboard/static/   (default)
#
# Dependencies are pinned to exact versions, installed from the lockfile (`npm ci`) with install scripts disabled.
set -euo pipefail
cd "$(dirname "$0")/.."
IMG="${NODE_IMAGE:-node:22-slim}"
run() {
  docker run --rm --network host -u "$(id -u):$(id -g)" -e HOME=/tmp -e npm_config_cache=/tmp/.npm -e CI=1 \
    -v "$PWD/frontend:/work/frontend" -v "$PWD/src/jobagent/dashboard:/work/src/jobagent/dashboard" -w /work/frontend "$IMG" "$@"
}
case "${1:-build}" in
  install) run npm install --ignore-scripts --no-audit --no-fund ;;
  test)    run sh -c "npm ci --ignore-scripts --no-audit --no-fund && npm test" ;;
  build)   run sh -c "npm ci --ignore-scripts --no-audit --no-fund && npm run build"
           python3 scripts/dashboard_source_hash.py > src/jobagent/dashboard/static/source.sha256 ;;
  *) sed -n 2,11p "$0"; exit 2 ;;
esac
