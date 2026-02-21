# Pyldon Agent Container
# Runs pi.dev agent in isolated container with browser automation

FROM python:3.12-slim

# System dependencies for Chromium
RUN apt-get update && apt-get install -y \
    chromium \
    fonts-liberation \
    fonts-noto-color-emoji \
    libgbm1 \
    libnss3 \
    libatk-bridge2.0-0 \
    libgtk-3-0 \
    libx11-xcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libasound2 \
    libpangocairo-1.0-0 \
    libcups2 \
    libdrm2 \
    libxshmfence1 \
    curl \
    git \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

ENV AGENT_BROWSER_EXECUTABLE_PATH=/usr/bin/chromium
ENV PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium

# pi.dev agent (CLI + SDK + coding tools)
RUN npm install -g @mariozechner/pi-coding-agent

WORKDIR /app

# Python runner (reads stdin JSON, spawns pi RPC, writes stdout JSON)
COPY agent_runner/ ./agent_runner/

# pi extension with IPC tools (send_message, schedule_task, etc.)
COPY pi-extensions/ ./pi-extensions/

# Workspace dirs
RUN mkdir -p /workspace/group /workspace/global /workspace/extra /workspace/ipc/messages /workspace/ipc/tasks

# Entrypoint: load env, pipe stdin to runner
RUN printf '#!/bin/bash\nset -e\n[ -f /workspace/env-dir/env ] && export $(cat /workspace/env-dir/env | xargs)\ncat > /tmp/input.json\ncd /app && python3 -m agent_runner.main < /tmp/input.json\n' > /app/entrypoint.sh && chmod +x /app/entrypoint.sh

# Non-root user
ARG USER_UID=1001
ARG USER_GID=1001
RUN groupadd --gid $USER_GID pyldon || true && \
    useradd --uid $USER_UID --gid $USER_GID -m pyldon || true && \
    chown -R $USER_UID:$USER_GID /workspace /app
RUN mkdir -p /home/pyldon/.pi/agent && \
    chown -R $USER_UID:$USER_GID /home/pyldon/.pi

USER pyldon
WORKDIR /workspace/group
ENTRYPOINT ["/app/entrypoint.sh"]
