#!/usr/bin/env bash
# Build the harness image on the host network: when host DNS only works through a VPN tunnel
# (e.g. Cisco AnyConnect), the default bridge network can't resolve package mirrors.
# Compose's bake builder refuses `build.network: host`, so build with docker directly.
set -euo pipefail
cd "$(dirname "$0")/.."
docker build --network host \
    --build-arg UID="$(id -u)" --build-arg GID="$(id -g)" \
    -t jobagent-harness:latest "$@" .
