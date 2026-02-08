# Pyldon Migration Plan

Rewrite of [NanoClaw](https://github.com/arpagon/nanoclaw) (TypeScript) to Python, using [Nanobot](https://github.com/HKUDS/nanobot) as a reference for Python patterns and idioms.

## Source Projects

| Project | Repo | Language | What We Take |
|---------|------|----------|--------------|
| **NanoClaw** | [arpagon/nanoclaw](https://github.com/arpagon/nanoclaw) | TypeScript | Architecture, security model, container isolation, per-room memory, IPC, mount system |
| **Nanobot** | [HKUDS/nanobot](https://github.com/HKUDS/nanobot) | Python | Python patterns, asyncio structure, Pydantic models, loguru logging, project layout |

Local paths for reference:
- NanoClaw: `../nanoclaw/`
- Nanobot: `../../thirdparty/HKUDS/nanobot/`

---

## Dependency Mapping

| NanoClaw (JS) | Pyldon (Python) | Notes |
|---------------|-----------------|-------|
| `matrix-bot-sdk` + `matrix-sdk-crypto-nodejs` | [`matrix-nio[e2e]`](https://github.com/matrix-nio/matrix-nio) | Different API surface; E2EE uses libolm instead of Rust SDK |
| `better-sqlite3` | `aiosqlite` / `sqlite3` (stdlib) | Near-identical SQL; async wrapper available |
| `cron-parser` | [`croniter`](https://github.com/kiorky/croniter) | Iterator-based API |
| `pino` / `pino-pretty` | [`loguru`](https://github.com/Delgan/loguru) | See [nanobot usage](https://github.com/HKUDS/nanobot/blob/main/nanobot/agent/loop.py) |
| `zod` | [`pydantic`](https://docs.pydantic.dev/) | See [nanobot config](https://github.com/HKUDS/nanobot/blob/main/nanobot/config/schema.py) |
| `@anthropic-ai/claude-agent-sdk` (JS v0.2.29) | [`claude-agent-sdk`](https://pypi.org/project/claude-agent-sdk/) (Python v0.1.33) | Official Python SDK; `query()`, `create_sdk_mcp_server()`, `tool()`, hooks |
| `child_process.spawn` | `asyncio.create_subprocess_exec` | See [nanobot shell tool](https://github.com/HKUDS/nanobot/blob/main/nanobot/agent/tools/shell.py) |
| `tsx` (dev) | Not needed | Python doesn't need a transpiler |
| `typescript` (dev) | `mypy` or `pyright` | Optional type checking |

---

## File-by-File Migration Map

### Host Process (NanoClaw `src/` --> Pyldon `pyldon/`)

| NanoClaw Source | Lines | Pyldon Target | Effort | Nanobot Reference |
|-----------------|-------|---------------|--------|-------------------|
| `src/types.ts` | 80 | `pyldon/models.py` | Easy | [nanobot/config/schema.py](https://github.com/HKUDS/nanobot/blob/main/nanobot/config/schema.py) |
| `src/config.ts` | 32 | `pyldon/config.py` | Easy | [nanobot/config/loader.py](https://github.com/HKUDS/nanobot/blob/main/nanobot/config/loader.py) |
| `src/utils.ts` | 19 | `pyldon/utils.py` | Easy | -- |
| `src/db.ts` | 278 | `pyldon/db.py` | Easy | [nanobot/session/manager.py](https://github.com/HKUDS/nanobot/blob/main/nanobot/session/manager.py) (different approach but useful pattern) |
| `src/pairing.ts` | 168 | `pyldon/pairing.py` | Easy | -- |
| `src/pair.ts` | 96 | `pyldon/cli.py` | Easy | [nanobot/cli/commands.py](https://github.com/HKUDS/nanobot/blob/main/nanobot/cli/commands.py) (typer-based CLI) |
| `src/matrix-types.ts` | 38 | `pyldon/matrix_types.py` | Easy | -- |
| `src/matrix-client.ts` | 150 | `pyldon/matrix_client.py` | Medium | -- (no Matrix in nanobot) |
| `src/matrix-monitor.ts` | 116 | `pyldon/matrix_monitor.py` | Medium | [nanobot/channels/base.py](https://github.com/HKUDS/nanobot/blob/main/nanobot/channels/base.py) (channel pattern) |
| `src/mount-security.ts` | 385 | `pyldon/mount_security.py` | Medium | -- |
| `src/task-scheduler.ts` | 139 | `pyldon/task_scheduler.py` | Medium | [nanobot/cron/service.py](https://github.com/HKUDS/nanobot/blob/main/nanobot/cron/service.py) |
| `src/container-runner.ts` | 450 | `pyldon/container_runner.py` | Hard | [nanobot/agent/tools/shell.py](https://github.com/HKUDS/nanobot/blob/main/nanobot/agent/tools/shell.py) (subprocess patterns) |
| `src/index.ts` | 591 | `pyldon/main.py` | Hard | [nanobot/agent/loop.py](https://github.com/HKUDS/nanobot/blob/main/nanobot/agent/loop.py) (async orchestration) |

### Container Agent (NanoClaw `container/agent-runner/src/` --> Pyldon `container/agent_runner/`)

| NanoClaw Source | Lines | Pyldon Target | Effort | Nanobot Reference |
|-----------------|-------|---------------|--------|-------------------|
| `container/agent-runner/src/index.ts` | 316 | `container/agent_runner/main.py` | Hard | [nanobot/agent/loop.py](https://github.com/HKUDS/nanobot/blob/main/nanobot/agent/loop.py) (SDK usage patterns) |
| `container/agent-runner/src/ipc-mcp.ts` | 322 | `container/agent_runner/ipc_mcp.py` | Medium-Hard | [nanobot/agent/tools/registry.py](https://github.com/HKUDS/nanobot/blob/main/nanobot/agent/tools/registry.py) (tool definition patterns) |

### Infrastructure

| NanoClaw Source | Pyldon Target | Effort | Notes |
|-----------------|---------------|--------|-------|
| `container/Dockerfile` | `container/Dockerfile` | Medium | `node:22-slim` --> `python:3.14-slim`; keep Chromium + agent-browser |
| `container/build.sh` | `container/build.sh` | Easy | Minimal changes |
| `package.json` | `pyproject.toml` | Easy | [nanobot pyproject.toml](https://github.com/HKUDS/nanobot/blob/main/pyproject.toml) |
| `tsconfig.json` | `pyproject.toml [tool.mypy]` | Easy | Optional |
| `.claude/skills/*` | `.claude/skills/*` | Medium | Rewrite skill instructions for Python codebase |

---

## Implementation Phases

### Phase 1: Foundation (Days 1-3)

Objective: Data layer and shared types working.

| Task | NanoClaw Source | Nanobot Reference |
|------|-----------------|-------------------|
| Project scaffold (`pyproject.toml`, `uv`) | `package.json` | [nanobot/pyproject.toml](https://github.com/HKUDS/nanobot/blob/main/pyproject.toml) |
| Pydantic models | `src/types.ts` (80 lines) | [nanobot/config/schema.py](https://github.com/HKUDS/nanobot/blob/main/nanobot/config/schema.py) |
| Config module | `src/config.ts` (32 lines) | [nanobot/config/loader.py](https://github.com/HKUDS/nanobot/blob/main/nanobot/config/loader.py) |
| Utils | `src/utils.ts` (19 lines) | -- |
| SQLite module | `src/db.ts` (278 lines) | -- |
| Pairing system | `src/pairing.ts` + `src/pair.ts` (264 lines) | [nanobot/cli/commands.py](https://github.com/HKUDS/nanobot/blob/main/nanobot/cli/commands.py) |
| Matrix types | `src/matrix-types.ts` (38 lines) | -- |

**Deliverable**: `pyldon/` package with models, config, db, pairing. All unit-testable without Docker or Matrix.

### Phase 2: Container Agent (Days 4-8)

Objective: Agent runs inside Docker and executes Claude SDK queries.

| Task | NanoClaw Source | Nanobot Reference |
|------|-----------------|-------------------|
| Python Claude Agent SDK spike | -- | [claude-agent-sdk on PyPI](https://pypi.org/project/claude-agent-sdk/) |
| Agent runner (stdin JSON -> SDK query -> stdout JSON) | `container/agent-runner/src/index.ts` (316 lines) | [nanobot/agent/loop.py](https://github.com/HKUDS/nanobot/blob/main/nanobot/agent/loop.py) |
| IPC MCP server (7 tools) | `container/agent-runner/src/ipc-mcp.ts` (322 lines) | [nanobot/agent/tools/registry.py](https://github.com/HKUDS/nanobot/blob/main/nanobot/agent/tools/registry.py) |
| Dockerfile (Python base) | `container/Dockerfile` | [nanobot/Dockerfile](https://github.com/HKUDS/nanobot/blob/main/Dockerfile) |
| Build script | `container/build.sh` | -- |

**Deliverable**: `echo '{"prompt":"hello",...}' | docker run -i pyldon-agent` works end-to-end.

**Critical validation**: Verify these Claude Agent SDK Python features work:
- `query()` async generator
- `create_sdk_mcp_server()` with `tool()` decorator
- `resume` session option
- `permissionMode: 'bypassPermissions'`
- `PreCompact` hook
- `settingSources`

### Phase 3: Host Process (Days 9-14)

Objective: Full host process running with Matrix and container orchestration.

| Task | NanoClaw Source | Nanobot Reference |
|------|-----------------|-------------------|
| Matrix client (matrix-nio + E2EE) | `src/matrix-client.ts` (150 lines) | -- |
| Matrix monitor (events, triggers, DMs) | `src/matrix-monitor.ts` (116 lines) | [nanobot/channels/base.py](https://github.com/HKUDS/nanobot/blob/main/nanobot/channels/base.py) |
| Mount security | `src/mount-security.ts` (385 lines) | -- |
| Container runner (asyncio subprocess) | `src/container-runner.ts` (450 lines) | [nanobot/agent/tools/shell.py](https://github.com/HKUDS/nanobot/blob/main/nanobot/agent/tools/shell.py) |
| Task scheduler | `src/task-scheduler.ts` (139 lines) | [nanobot/cron/service.py](https://github.com/HKUDS/nanobot/blob/main/nanobot/cron/service.py) |
| Main orchestrator (IPC, routing, lifecycle) | `src/index.ts` (591 lines) | [nanobot/agent/loop.py](https://github.com/HKUDS/nanobot/blob/main/nanobot/agent/loop.py) |

**Deliverable**: `uv run pyldon` connects to Matrix, responds to messages, runs scheduled tasks.

### Phase 4: Integration & Polish (Days 15-20)

| Task | Notes |
|------|-------|
| E2EE testing | matrix-nio uses libolm (different from NanoClaw's Rust SDK crypto) |
| Scheduled task E2E | Cron, interval, one-shot -- verify all work |
| IPC flow testing | Container -> host message delivery, task creation |
| Edge cases | Container timeouts, malformed output, concurrent messages, large outputs |
| Data migration | Script to migrate NanoClaw SQLite data + sessions to Pyldon format |
| Skills rewrite | Update `.claude/skills/` for Python codebase |
| ~~Systemd service~~ | ~~Replace launchd plist with systemd unit~~ (done: `systemd/pyldon.service`) |

**Deliverable**: Production-ready Pyldon replacing NanoClaw.

---

## Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Claude Agent SDK Python is alpha (v0.1.33 vs JS v0.2.29) | High | Phase 2 spike validates all required features early |
| matrix-nio E2EE uses libolm (different crypto backend) | Medium | Test encrypted rooms thoroughly in Phase 4 |
| `agent-browser` requires Node.js in Python container | Low | Keep Node.js in container for agent-browser, or replace with Playwright |
| Nanobot patterns may not fit NanoClaw's architecture | Low | Nanobot is reference only; architecture follows NanoClaw strictly |

---

## Nanobot Reference Index

Key files in [HKUDS/nanobot](https://github.com/HKUDS/nanobot) useful during migration:

| Pattern | Nanobot File | Why It's Useful |
|---------|--------------|-----------------|
| Async agent loop | [`nanobot/agent/loop.py`](https://github.com/HKUDS/nanobot/blob/main/nanobot/agent/loop.py) | asyncio orchestration, LLM call loop |
| Tool registration | [`nanobot/agent/tools/registry.py`](https://github.com/HKUDS/nanobot/blob/main/nanobot/agent/tools/registry.py) | Plugin-style tool system |
| Tool definitions | [`nanobot/agent/tools/shell.py`](https://github.com/HKUDS/nanobot/blob/main/nanobot/agent/tools/shell.py) | Subprocess execution in Python |
| File tools | [`nanobot/agent/tools/filesystem.py`](https://github.com/HKUDS/nanobot/blob/main/nanobot/agent/tools/filesystem.py) | File I/O patterns |
| Channel abstraction | [`nanobot/channels/base.py`](https://github.com/HKUDS/nanobot/blob/main/nanobot/channels/base.py) | Base channel pattern |
| Message bus | [`nanobot/bus/queue.py`](https://github.com/HKUDS/nanobot/blob/main/nanobot/bus/queue.py) | Async queue pattern |
| Cron scheduling | [`nanobot/cron/service.py`](https://github.com/HKUDS/nanobot/blob/main/nanobot/cron/service.py) | croniter usage, job persistence |
| Pydantic config | [`nanobot/config/schema.py`](https://github.com/HKUDS/nanobot/blob/main/nanobot/config/schema.py) | Config models with camelCase conversion |
| Session persistence | [`nanobot/session/manager.py`](https://github.com/HKUDS/nanobot/blob/main/nanobot/session/manager.py) | JSONL session storage |
| Memory system | [`nanobot/agent/memory.py`](https://github.com/HKUDS/nanobot/blob/main/nanobot/agent/memory.py) | Markdown-based memory |
| CLI | [`nanobot/cli/commands.py`](https://github.com/HKUDS/nanobot/blob/main/nanobot/cli/commands.py) | Typer-based CLI |
| Dockerfile | [`Dockerfile`](https://github.com/HKUDS/nanobot/blob/main/Dockerfile) | Python container with uv |
| pyproject.toml | [`pyproject.toml`](https://github.com/HKUDS/nanobot/blob/main/pyproject.toml) | Hatchling build, dependency list |
| Subagents | [`nanobot/agent/subagent.py`](https://github.com/HKUDS/nanobot/blob/main/nanobot/agent/subagent.py) | Background task spawning |
| Context building | [`nanobot/agent/context.py`](https://github.com/HKUDS/nanobot/blob/main/nanobot/agent/context.py) | System prompt assembly from files |
| Skill system | [`nanobot/agent/skills.py`](https://github.com/HKUDS/nanobot/blob/main/nanobot/agent/skills.py) | Markdown skills with YAML frontmatter |
