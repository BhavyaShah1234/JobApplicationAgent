# JobApplicationAgent harness image.
# Only Claude Code edits this file; the agent requests additions via state/missing-agent-capabilities.txt.

FROM ghcr.io/astral-sh/uv:0.12.22 AS uv

FROM python:3.14-slim-trixie@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170

ARG UID=1000
ARG GID=1000

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    LANG=en_US.UTF-8 \
    LC_ALL=en_US.UTF-8 \
    DISPLAY=:99 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1

# Display stack (Xvfb + fluxbox + x11vnc + noVNC), LaTeX, PDF/OCR tools, fonts, locale.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg locales tzdata procps \
        xvfb fluxbox x11vnc novnc websockify x11-utils \
        latexmk texlive-latex-base texlive-latex-recommended texlive-latex-extra \
        texlive-fonts-recommended texlive-fonts-extra \
        poppler-utils tesseract-ocr \
        fonts-liberation fonts-noto-core fonts-noto-color-emoji \
    && sed -i 's/^# *en_US.UTF-8/en_US.UTF-8/' /etc/locale.gen && locale-gen \
    && mkdir -p /tmp/.X11-unix && chmod 1777 /tmp/.X11-unix \
    && rm -rf /var/lib/apt/lists/*

# Real Google Chrome Stable from Google's signed repo (amd64); Debian Chromium on other arches.
RUN if [ "$(dpkg --print-architecture)" = "amd64" ]; then \
        install -d -m 0755 /etc/apt/keyrings \
        && curl -fsSL https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /etc/apt/keyrings/google-chrome.gpg \
        && echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/google-chrome.gpg] https://dl.google.com/linux/chrome/deb/ stable main" \
            > /etc/apt/sources.list.d/google-chrome.list \
        && apt-get update && apt-get install -y --no-install-recommends google-chrome-stable; \
    else \
        apt-get update && apt-get install -y --no-install-recommends chromium \
        && ln -s /usr/bin/chromium /usr/local/bin/google-chrome; \
    fi \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /uvx /usr/local/bin/

WORKDIR /app

# Dependencies first (cached layer), then the project itself.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --extra dev
# System libraries Playwright expects for Chromium-family browsers (no browser download).
RUN playwright install-deps chromium && rm -rf /var/lib/apt/lists/*

COPY src ./src
COPY tests ./tests
RUN uv sync --frozen --extra dev

# Non-root user matching the host UID so bind-mounted data/state/outputs stay owned by the host user.
RUN groupadd -g ${GID} agent && useradd -m -u ${UID} -g ${GID} -s /bin/bash agent \
    && mkdir -p /app/data /app/state /app/outputs && chown -R agent:agent /app/data /app/state /app/outputs

COPY --chmod=0755 docker/entrypoint.sh /usr/local/bin/entrypoint.sh

USER agent
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["sleep", "infinity"]
