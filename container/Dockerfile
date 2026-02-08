# Pyldon Agent Container
# Runs Claude Agent SDK in isolated container with browser automation

FROM python:3.12-slim

# Install system dependencies for Chromium
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

# Set Chromium path for agent-browser
ENV AGENT_BROWSER_EXECUTABLE_PATH=/usr/bin/chromium
ENV PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium

# Install agent-browser and claude-code globally (Node.js tools)
RUN npm install -g agent-browser @anthropic-ai/claude-code

# Install uv for Python package management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Create app directory
WORKDIR /app

# Copy agent runner code
COPY agent_runner/ ./agent_runner/

# Create a minimal pyproject.toml for the container agent
RUN printf '[project]\nname = "pyldon-agent"\nversion = "0.1.0"\nrequires-python = ">=3.12"\ndependencies = [\n    "claude-code-sdk>=0.0.25",\n    "croniter>=6.0.0",\n]\n\n[tool.hatch.build.targets.wheel]\npackages = ["agent_runner"]\n\n[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n' > pyproject.toml

# Install Python dependencies
RUN uv sync

# Create workspace directories
RUN mkdir -p /workspace/group /workspace/global /workspace/extra /workspace/ipc/messages /workspace/ipc/tasks

# Create entrypoint script
RUN printf '#!/bin/bash\nset -e\n[ -f /workspace/env-dir/env ] && export $(cat /workspace/env-dir/env | xargs)\ncat > /tmp/input.json\ncd /app && uv run python -m agent_runner.main < /tmp/input.json\n' > /app/entrypoint.sh && chmod +x /app/entrypoint.sh

# Create user with same UID/GID as host user for seamless file permissions
ARG USER_UID=1001
ARG USER_GID=1001
RUN groupadd --gid $USER_GID pyldon || true && \
    useradd --uid $USER_UID --gid $USER_GID -m pyldon || true && \
    chown -R $USER_UID:$USER_GID /workspace /app

# Switch to non-root user
USER pyldon

# Set working directory to group workspace
WORKDIR /workspace/group

# Entry point reads JSON from stdin, outputs JSON to stdout
ENTRYPOINT ["/app/entrypoint.sh"]
