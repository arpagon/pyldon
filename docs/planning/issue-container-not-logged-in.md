# Issue: Container agent fails with "Not logged in" despite env vars being set

## Status
**Blocked** -- needs investigation of how claude-code-sdk resolves credentials inside container

## Symptom
Container agent starts, receives input, calls `claude-code-sdk query()`, and immediately gets:
```
Not logged in - Please run /login
```

## What works
- Manual test inside container with `--entrypoint bash` + explicit env export + direct Python call to `claude-code-sdk` works perfectly
- `CLAUDE_CODE_USE_BEDROCK=1`, `AWS_REGION`, `AWS_BEARER_TOKEN_BEDROCK` are confirmed set

## What fails
- When Pyldon spawns the container via `docker run -i --rm -e ... pyldon-agent:latest`, the entrypoint script runs `agent_runner.main` which calls `claude-code-sdk query()`, and the SDK's internal `claude` CLI subprocess reports "Not logged in"

## Hypothesis
The `claude-code-sdk` Python package calls the `claude` CLI (Node.js, installed globally via npm). The CLI may:
1. Not inherit env vars properly when spawned by the SDK from within the entrypoint
2. Look for auth config in `$HOME/.claude/` which is mounted from host but may be empty/wrong
3. Have a different behavior when stdin is piped (the entrypoint reads stdin into a file first)

## Key difference between working test and failing run
| | Manual test | Pyldon run |
|---|---|---|
| Entrypoint | `bash -c "..."` | `/app/entrypoint.sh` |
| Env loading | `export $(cat ... \| xargs)` | `-e` flags via docker |
| User | `pyldon` | `pyldon` |
| Stdin | interactive tty | piped JSON |
| Claude SDK call | direct Python | via `agent_runner.main` |

## Investigation needed
1. Add debug logging in `agent_runner/main.py` to print env vars before calling SDK
2. Check if the entrypoint.sh is consuming stdin before `agent_runner.main` can read it (the `cat > /tmp/input.json` step)
3. Check if claude CLI inside container sees the env vars when spawned by the SDK
4. Compare how NanoClaw's container runner passes credentials (check `../nanoclaw/src/container-runner.ts`)
5. Check if `$HOME/.claude/` mount interferes with Bedrock auth

## Files involved
- `pyldon/container_runner.py` -- builds docker args, passes `-e` env vars
- `container/Dockerfile` -- entrypoint.sh script
- `container/agent_runner/main.py` -- calls `claude-code-sdk query()`
- `data/env/env` -- environment variables file
- `../nanoclaw/src/container-runner.ts` -- reference implementation
