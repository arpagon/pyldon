# Pyldon

Personal AI assistant written in Python. Runs [pi.dev](https://github.com/badlogic/pi-mono) agents in isolated Docker containers with per-room filesystem separation. Rewrite of [NanoClaw](https://github.com/arpagon/nanoclaw) (TypeScript).

## Why

NanoClaw proved the architecture: container-isolated agents, per-room memory, scheduled tasks, Matrix as the communication channel. But it's written in TypeScript, and the Python ecosystem is where AI tooling lives.

Pyldon is NanoClaw's architecture rewritten in Python. Same security model (OS-level container isolation, not application-level permission checks). Same philosophy (small enough to understand). Better ecosystem.

## Philosophy

**Small enough to understand.** ~3,400 lines of code. Single Python process. A handful of modules. No microservices, no message queues, no abstraction layers.

**Secure by isolation.** Agents run in Docker containers. They can only see explicitly mounted directories. Shell access is safe because commands execute inside the container.

**Built for one user.** Not a framework. Working software that fits your exact needs. Fork it and modify it.

## What It Does

- **Matrix I/O** - Message your assistant from any Matrix client (Element, FluffyChat, etc.)
- **E2EE** - End-to-end encryption support
- **Per-room isolation** - Each room gets its own filesystem, memory (`AGENTS.md`), and container sandbox
- **Main channel** - Admin room for cross-room management
- **Scheduled tasks** - Cron, interval, and one-shot tasks that run as full containerized agents
- **Web access** - Search and fetch content
- **Browser automation** - Chromium inside the sandbox for scraping, screenshots, PDFs
- **Configurable identity** - `IDENTITY.md` defines your assistant's personality

## Architecture

```
Matrix (matrix-nio) → SQLite → Event handler → Docker Container (pi.dev RPC) → Response
```

Single Python process. Agents execute in isolated Docker containers via [pi.dev](https://github.com/badlogic/pi-mono) in RPC mode. IPC via filesystem JSON files. No daemons, no queues.

## Quick Start

```bash
git clone https://github.com/arpagon/pyldon.git
cd pyldon
uv sync
./container/build.sh
cp .env.example .env  # Configure your provider credentials
uv run pyldon
```

## Requirements

- Linux or macOS
- Python 3.14+ with [uv](https://docs.astral.sh/uv/)
- [Docker](https://docker.com/products/docker-desktop)
- A Matrix account with access token
- AI provider credentials (AWS Bedrock, Anthropic API, OpenAI, etc.)

## Lineage

| Project | Role |
|---------|------|
| [NanoClaw](https://github.com/arpagon/nanoclaw) | Direct ancestor (TypeScript) |
| [pi-mono](https://github.com/badlogic/pi-mono) | Agent engine (pi.dev) |

## License

MIT
