# Pyldon

Python rewrite of [NanoClaw](https://github.com/arpagon/nanoclaw). Personal Claude assistant with container-isolated agents.

## Quick Context

Single Python process (asyncio) that connects to Matrix via `matrix-nio`, routes messages to Claude Agent SDK running in Docker containers. Each room has isolated filesystem and memory.

## Key Modules

| Module | Purpose |
|--------|---------|
| `pyldon/main.py` | Main app: Matrix connection, message routing, IPC |
| `pyldon/matrix_client.py` | Matrix client wrapper (connect, send, typing, E2EE) |
| `pyldon/matrix_monitor.py` | Matrix event handler for incoming messages |
| `pyldon/container_runner.py` | Spawns Docker containers with volume mounts |
| `pyldon/task_scheduler.py` | Runs scheduled tasks as containerized agents |
| `pyldon/db.py` | SQLite operations (messages, tasks, chats) |
| `pyldon/config.py` | Trigger pattern, paths, intervals |
| `pyldon/mount_security.py` | Validates additional mounts against external allowlist |
| `pyldon/pairing.py` | Owner pairing system for Matrix setup |
| `pyldon/models.py` | Pydantic models for all data types |
| `container/agent_runner/main.py` | Runs inside Docker -- Claude Agent SDK + IPC MCP server |
| `container/agent_runner/ipc_mcp.py` | MCP tools: schedule_task, send_message, etc. |
| `groups/{name}/CLAUDE.md` | Per-room memory (isolated) |

## Reference Projects

| Project | Path | Role |
|---------|------|------|
| NanoClaw (TypeScript) | `../nanoclaw/` | Direct ancestor -- same architecture, being rewritten |
| Nanobot (Python) | `../../thirdparty/HKUDS/nanobot/` | Python reference for patterns and idioms |

## Tech Stack

- **Python 3.12+** with asyncio
- **matrix-nio[e2e]** for Matrix + E2EE
- **claude-agent-sdk** (Python) for agent execution inside containers
- **SQLite** via `aiosqlite` for persistence
- **Pydantic** for data models and validation
- **loguru** for logging
- **croniter** for cron expression parsing
- **Docker** for agent container isolation

## Development

```bash
uv run pyldon            # Run the app
uv run pytest            # Run tests
./container/build.sh     # Rebuild agent container
```

## Skills

| Skill | When to Use |
|-------|-------------|
| `/setup` | First-time installation, authentication, service configuration |
| `/customize` | Adding channels, integrations, changing behavior |
| `/debug` | Container issues, logs, troubleshooting |

## Migration Status

See [PLAN.md](PLAN.md) for the full migration plan from NanoClaw (TypeScript) to Pyldon (Python).
