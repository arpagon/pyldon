# Pyldon

Personal Claude assistant written in Python. Runs agents in isolated Docker containers with per-room filesystem separation. Rewrite of [NanoClaw](https://github.com/arpagon/nanoclaw) (TypeScript) taking architectural inspiration from [Nanobot](https://github.com/HKUDS/nanobot) (Python).

## Why

NanoClaw proved the architecture: container-isolated agents, per-room memory, scheduled tasks, Matrix as the communication channel. But it's written in TypeScript, and the Python ecosystem is where AI tooling lives -- LiteLLM, LangChain, transformers, and the community that builds with them.

Pyldon is NanoClaw's architecture rewritten in Python. Same security model (OS-level container isolation, not application-level permission checks). Same philosophy (small enough to understand). Better ecosystem.

## Philosophy

**Small enough to understand.** Single Python process. A handful of modules. No microservices, no message queues, no abstraction layers.

**Secure by isolation.** Agents run in Docker containers. They can only see explicitly mounted directories. Shell access is safe because commands execute inside the container.

**Built for one user.** Not a framework. Working software that fits your exact needs. Fork it and modify it.

**AI-native.** Setup, debugging, and customization happen through Claude Code. The codebase assumes an AI collaborator is always available.

**Skills over features.** Contributors add Claude Code skills (markdown instruction files), not feature PRs. Users run `/add-telegram` and get clean, purpose-built code.

## What It Does

- **Matrix I/O** - Message your assistant from any Matrix client (Element, FluffyChat, etc.)
- **E2EE** - End-to-end encryption support
- **Per-room isolation** - Each room gets its own filesystem, memory (`AGENTS.md`), and container sandbox
- **Main channel** - Admin room for cross-room management
- **Scheduled tasks** - Cron, interval, and one-shot tasks that run as full containerized agents
- **Web access** - Search and fetch content
- **Browser automation** - Chromium inside the sandbox for scraping, screenshots, PDFs
- **Configurable identity** - `IDENTITY.md` defines your assistant's personality

## Quick Start

```bash
git clone https://github.com/arpagon/pyldon.git
cd pyldon
claude
```

Then run `/setup`.

## Architecture

```
Matrix (matrix-nio) --> SQLite --> Event handler --> Docker Container (Claude Agent SDK) --> Response
```

Single Python process. Agents execute in isolated Docker containers with mounted directories. IPC via filesystem. No daemons, no queues.

## Lineage

| Project | Role |
|---------|------|
| [OpenClaw](https://github.com/openclaw/openclaw) | Original inspiration (430k+ lines) |
| [NanoClaw](https://github.com/arpagon/nanoclaw) | First minimalist rewrite (TypeScript) -- Pyldon's direct ancestor |
| [Nanobot](https://github.com/HKUDS/nanobot) | Python reference for patterns and structure |

Pyldon takes the **security model** from NanoClaw (container isolation, per-room filesystems, mount allowlists) and the **language and patterns** from Nanobot (Python, asyncio, Pydantic, loguru).

## Requirements

- Linux or macOS
- Python 3.14+
- [uv](https://docs.astral.sh/uv/) (package manager -- always use `uv` for dependency management and running)
- [Claude Code](https://claude.ai/download)
- [Docker](https://docker.com/products/docker-desktop)
- A Matrix account with access token

## License

MIT
