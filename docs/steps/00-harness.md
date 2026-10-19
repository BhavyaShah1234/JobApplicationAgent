# Step 0 — Harness

## Run it

```bash
scripts/setup_env.sh            # once: creates .env with a fresh VAULT_KEY (back it up)
scripts/build.sh                # builds jobagent-harness:latest on the host network
docker compose up -d            # needs OPENAI_API_KEY (Voyager) in your shell
docker compose exec agent jobagent step harness
```

Watch the browser at <http://localhost:6080/vnc.html> (localhost only).

During development, `docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d` mounts
`src/` and `tests/` read-only so code changes don't need a rebuild.

Stability gate: `scripts/gate_step0.sh 3` (rebuild → recreate → unit tests, harness check, e2e test, noVNC).

## What the check covers

| Check | Passes when |
|---|---|
| volumes | `data/`, `state/`, `outputs/` are writable |
| binaries | Chrome, latexmk, pdflatex, poppler, tesseract, x11vnc are on PATH |
| latex | a probe document compiles and `pdftotext` reads it back |
| chrome | real Chrome launches headful on Xvfb with the persistent profile and takes a screenshot |
| vault | an entry round-trips and the previous run's entry is still there (persistence across recreate) |
| voyager planner / vision / embeddings | Voyager itself answers (called without fallback); vision reads the Chrome screenshot |
| ollama fallback | with Voyager pointed at a dead port, chat and embeddings are served by Ollama |

## Learned while building

- **Host DNS behind the ASU VPN (Cisco AnyConnect).** Containers on Docker's bridge network can't resolve
  names, so the image is built with `docker build --network host` (`scripts/build.sh`). Compose v5 always
  uses bake, which refuses `build.network: host` without `--allow`, so compose only references the image.
- **Ollama binds 127.0.0.1.** The container uses `network_mode: host` instead of changing the systemd unit;
  VNC and noVNC bind to 127.0.0.1 so nothing is exposed to the LAN.
- **GPU is a 4 GB GTX 1650**, so fallbacks are small: `qwen3:4b`, `qwen2.5vl:3b`, `nomic-embed-text`.
- **Stale key in inherited environments.** A process started before `~/.bashrc` changed can carry an old
  `OPENAI_API_KEY`; Voyager answers 401 "Invalid proxy server token". Open a new shell before `compose up`.
- **Chrome under Xvfb + fluxbox:** `--start-maximized` and the initial blank tab of a persistent context
  break `Page.captureScreenshot`; the window is sized explicitly, the first tab is replaced, occlusion
  backgrounding is disabled and screenshots retry briefly.
- **uv's distroless image** can't run `uv lock` (no shell to detect libc); lock inside the Python base image.
- `exclude-newer` in `pyproject.toml` keeps resolution to releases at least ~2 weeks old.
