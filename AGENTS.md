# Pyldon

Python rewrite of [NanoClaw](https://github.com/arpagon/nanoclaw). Personal assistant with container-isolated agents.

## Quick Context

Single Python process (asyncio) that connects to Matrix via `matrix-nio`, routes messages to pi.dev agents running in Docker containers. Each room has isolated filesystem and memory.

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
| `container/agent_runner/main.py` | Runs inside Docker — spawns pi via RPC, collects response |
| `container/pi-extensions/pyldon-ipc.ts` | pi extension: IPC tools (send_message, schedule_task, etc.) |
| `groups/{name}/AGENTS.md` | Per-room memory (isolated) |

## Reference Projects

| Project | Path | Role |
|---------|------|------|
| NanoClaw (TypeScript) | `../nanoclaw/` | Direct ancestor -- same architecture, being rewritten |
| pi-mono (source) | `../../thirdparty/badlogic/pi-mono/` | pi.dev source code — SDK, RPC, extensions, providers |
| Nanobot (Python) | `../../thirdparty/HKUDS/nanobot/` | Python reference for patterns and idioms |

## Tech Stack

- **Python 3.14+** with asyncio (always use `uv` for package management and running)
- **matrix-nio[e2e]** for Matrix + E2EE
- **pi.dev** (`@mariozechner/pi-coding-agent`) for agent execution inside containers via RPC mode
- **SQLite** via `aiosqlite` for persistence
- **Pydantic** for data models and validation
- **loguru** for logging
- **croniter** for cron expression parsing
- **Docker** for agent container isolation

## Development

Always use `uv` for all Python operations. Never use `pip`, `pip3`, or raw `python` commands.

```bash
uv run pyldon            # Run the app
uv run pytest            # Run tests
uv add <package>         # Add a dependency
uv sync                  # Sync dependencies
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
