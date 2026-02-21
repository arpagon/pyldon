# Migration Plan: Claude Agent SDK -> pi.dev

## Document
- Date: `2026-02-21`
- Author: `Codex`
- Status: `Proposed`
- Scope: `Pyldon (Python host) + container runner`
- Primary goal: replace current coupling to `claude-code-sdk` with a `pi.dev`-based execution layer while preserving container isolation, file-based IPC, and current Matrix/scheduler behavior.

## Executive Summary
Pyldon already has a strong split between:
- host logic (`pyldon/*.py`) for Matrix, DB, scheduler, routing, and mount security
- agent runner in the container (`container/agent_runner/main.py`)

This allows a low-risk migration without rewriting the full system. The plan is to:
1. add an engine abstraction
2. implement a `pi.dev` backend
3. keep the same `stdin JSON -> stdout JSON` contract and IPC behavior
4. roll out with canary + instant fallback to Claude

Expected result: less vendor lock-in, multi-provider capability via pi.dev, and a clean base for additional engines.

---

## 1) Current State (Technical Baseline)

### Current Claude Coupling
- `container/agent_runner/main.py`: imports `claude_code_sdk` and uses `query()` + `ClaudeCodeOptions`.
- `container/Dockerfile`: installs `@anthropic-ai/claude-code` and Python `claude-code-sdk`.
- `pyldon/container_runner.py`: environment variable allowlist is Claude/Bedrock-centric.
- `pyldon/container_runner.py`: per-group sessions are persisted in `.claude`.

### Components Already Engine-Agnostic
- Host/runner JSON contract (`ContainerInput`/`ContainerOutput` in `pyldon/models.py`).
- Docker orchestration and process parsing (`pyldon/container_runner.py`).
- File-based IPC for `send_message`, task operations, and scheduler integration (`container/agent_runner/ipc_mcp.py` + watcher in `pyldon/main.py`).
- Scheduler and Matrix routing (`pyldon/task_scheduler.py`, `pyldon/main.py`).

### Important Finding
`create_ipc_mcp()` builds tool handlers but there is no explicit MCP server wiring visible in the current Python runner. This should be normalized during migration to avoid implicit provider-specific behavior.

---

## 2) Assumptions and Constraints

### Validated pi.dev Assumptions
- `pi.dev` is provider-agnostic (multi-provider model).
- Programmatic integration exists via JS SDK (`@pi-ai/coding`) and `AgentSession`.
- An RPC mode exists for non-TypeScript integration.
- MCP is not the primary native path today, so tools should be exposed through a provider-neutral wrapper.

### Pyldon Constraints
- Keep container isolation and existing mount security model.
- Keep file-based IPC to remain compatible with host processing.
- Preserve per-group long-lived context/session behavior.
- Avoid downtime and keep Claude fallback during rollout.

### Non-Goals
- Rewriting Matrix stack or scheduler.
- Unnecessary DB schema redesign.
- Removing Docker.
- Enabling every provider on day one.

---

## 3) Target Architecture

### Engine Pattern
Introduce a stable internal contract:
- `AgentEngine` (interface)
- `run(input: ContainerInput) -> ContainerOutput`
- Initial implementations:
  - `ClaudeEngine` (legacy fallback)
  - `PiEngine` (new default candidate)

Host API remains unchanged. Migration impact is mostly isolated to the container runner.

### Runner V2 (Container)
- `container/agent_runner/main.py` becomes an engine router:
  - read `engine` from input or env (`PYLDON_AGENT_ENGINE`)
  - dispatch to selected backend
  - preserve sentinels and output schema

### Tools / IPC Strategy
Because MCP is not the primary `pi.dev` path, replace MCP coupling with a neutral `ToolBridge`:
- one canonical tool contract (`send_message`, `schedule_task`, etc.)
- implementation writes files under `/workspace/ipc/messages|tasks/*.json`
- engine-specific adapters register those tools (Claude adapter, Pi adapter)

### Session Strategy
- Separate per-engine session storage:
  - Claude: `data/sessions/<group>/.claude`
  - Pi: `data/sessions/<group>/.pi`
- Store session IDs by engine to avoid collisions:
  - proposed structure: `sessions.json` -> `{"<group>": {"claude": "...", "pi": "..."}}`

### Configuration
Add explicit engine config:
- global default (`agent_engine_default`)
- per-group override (`container_config.engine`)
- fallback chain (`fallback_engine`)

---

## 4) Migration Phases

### Phase 0 - Preparation and ADRs (1-2 days)
Goal: lock architecture and contracts before code changes.

Tasks:
- Create ADR: engine abstraction + adapter pattern.
- Define engine config schema.
- Define multi-engine session schema.
- Define compatibility requirements for `ContainerInput/Output`.

Deliverables:
- `docs/adr/adr-XXXX-engine-abstraction.md`
- signed compatibility checklist

Exit criteria:
- no unresolved architecture blockers.

Rollback:
- none (documentation-only phase).

### Phase 1 - Minimal Refactor, No Behavior Change (2-3 days)
Goal: modularize runner without changing production behavior.

Tasks:
- add `container/agent_runner/engines/base.py`
- move current logic to `container/agent_runner/engines/claude_engine.py`
- convert `main.py` into dispatcher with default `claude`
- add regression tests for output format, errors, and sentinel handling

Deliverables:
- modular runner still running on Claude
- green tests

Exit criteria:
- `uv run pytest` passes without regressions.
- baseline behavior unchanged.

Rollback:
- switch feature flag/branch back to legacy runner.

### Phase 2 - Neutral ToolBridge and MCP Decoupling (2-3 days)
Goal: make business tools independent of any provider API.

Tasks:
- extract IPC tool core from `ipc_mcp.py` into `ipc_tools.py`
- keep MCP adapter only for Claude (if needed)
- build Pi tool adapter using the same tool core
- add unit tests for all tools + permission checks (main vs non-main)

Deliverables:
- unified tool API
- coverage at least equal to current tool tests

Exit criteria:
- tools behave identically from Claude backend.
- host receives identical IPC files.

Rollback:
- keep legacy MCP adapter togglable by flag.

### Phase 3 - Implement PiEngine (RPC/Programmatic) (3-5 days)
Goal: run agent prompts through `pi.dev` while preserving Pyldon contracts.

Tasks:
- implement `container/agent_runner/engines/pi_engine.py` or `pi_engine.ts` (TS wrapper recommended for native SDK fit)
- integrate `AgentSession` and per-group session persistence
- map ToolBridge tools into pi tool registration flow
- capture final result + intermediate logs
- map all failures to `ContainerOutput(status="error", error=...)`

Technical options:
- Recommended: TS wrapper with `@pi-ai/coding` called by Python runner.
- Alternative: local RPC sidecar in container + Python client bridge.

Deliverables:
- `pi` backend handling normal chat and scheduled tasks
- smoke tests for both flows

Exit criteria:
- Matrix -> Pi engine -> Matrix works end-to-end.
- scheduler works in Pi mode.

Rollback:
- set `PYLDON_AGENT_ENGINE=claude` globally.

### Phase 4 - Configuration, Secrets, Security Hardening (1-2 days)
Goal: support multi-provider safely.

Tasks:
- extend env var allowlist in `pyldon/container_runner.py`:
  - include required Pi/provider credentials
  - keep strict allowlist model
- preserve mount policy unchanged
- enforce engine-specific session directories
- add secret redaction/sanitization in logs

Deliverables:
- documented secret policy
- env filtering tests

Exit criteria:
- no secret leakage in logs.
- container only gets allowed variables.

Rollback:
- revert allowlist extension to Claude-only set.

### Phase 5 - Data and Session Compatibility (1-2 days)
Goal: migrate state with backward compatibility.

Tasks:
- define multi-engine `sessions.json` shape with backward compatibility
- create idempotent migration script:
  - keep existing Claude session IDs
  - initialize Pi session slots empty
- define behavior for in-flight scheduled tasks during engine switch

Deliverables:
- `scripts/migrate_sessions_to_multi_engine.py`
- migration tests

Exit criteria:
- existing nodes boot without errors.
- backward compatibility verified.

Rollback:
- restore backup of `sessions.json`.

### Phase 6 - E2E Testing and Performance Baseline (2-3 days)
Goal: verify complete functionality and operational limits.

Tasks:
- full test matrix:
  - normal chat
  - scheduled `cron`, `interval`, `once`
  - task operations (`send_message`, `list/pause/resume/cancel`)
  - group registration from main room
  - timeout and malformed output handling
- concurrency tests (multiple groups/messages)
- compare latency/cost/error profile vs Claude baseline

Deliverables:
- pass/fail test report
- baseline comparison table

Exit criteria:
- error rate within agreed margin over baseline.
- no security regressions.

Rollback:
- canary rollback to Claude.

### Phase 7 - Progressive Rollout and Claude Deprecation (3 stages)
Goal: deploy safely with guardrails.

Stage A (canary):
- enable Pi only for `main` group
- monitor for 48-72h

Stage B (expansion):
- rollout 25% -> 50% -> 100% groups based on metrics

Stage C (deprecation):
- keep Claude fallback for 2 weeks
- then disable Claude as default (do not remove code yet)

Promotion gates between stages:
- error budget healthy
- no security incidents
- acceptable response quality feedback

Rollback:
- live config switch back to `claude`.

### Phase 8 - Cleanup and Closure (1-2 days)
Goal: finish with maintainable code and docs.

Tasks:
- remove dead provider-specific code no longer needed
- update `README.md`, `PLAN.md`, `AGENTS.md`
- publish operational runbook for multi-engine mode

Deliverables:
- final documentation
- reduced technical debt

Exit criteria:
- DoD checklist completed.

---

## 5) Expected Code Changes (File-Level)

Files to add:
- `container/agent_runner/engines/base.py`
- `container/agent_runner/engines/claude_engine.py`
- `container/agent_runner/engines/pi_engine.py` (or TS wrapper equivalent)
- `container/agent_runner/ipc_tools.py`
- `docs/adr/adr-XXXX-engine-abstraction.md`
- `scripts/migrate_sessions_to_multi_engine.py`
- new tests for engines and migration

Files to modify:
- `container/agent_runner/main.py` (engine router/dispatcher)
- `container/agent_runner/ipc_mcp.py` (legacy adapter only, if retained)
- `container/Dockerfile` (Pi runtime deps, optionally TS runtime tools)
- `pyldon/container_runner.py` (env allowlist + per-engine session paths)
- `pyldon/models.py` (typed config/session structures if needed)
- `pyldon/main.py` and `pyldon/task_scheduler.py` (engine-aware session read/write)
- `README.md`, `PLAN.md`

---

## 6) Risks and Mitigations

1. `pi` API/version volatility
- Mitigation: pin versions, maintain stable internal wrapper, add release smoke tests.

2. Lack of native MCP parity
- Mitigation: keep ToolBridge IPC-first and provider-neutral.

3. Session/context regressions
- Mitigation: backward-compatible schema + idempotent migration + backups.

4. Python + TypeScript complexity increase
- Mitigation: constrain TS to `pi` adapter layer and keep strict JSON contract.

5. Secret exposure risk
- Mitigation: strict allowlist and log redaction tests.

6. Response quality degradation during rollout
- Mitigation: longer canary, metrics gate, immediate fallback.

---

## 7) Observability and Metrics

Minimum per-engine metrics:
- `agent_run_count`
- `agent_error_count`
- `agent_timeout_count`
- `agent_duration_ms` (p50/p95)
- `tool_call_count` by tool type
- `ipc_file_processing_errors`

Initial targets:
- error rate <= Claude baseline + 2%
- timeout rate <= Claude baseline + 1%
- p95 latency <= Claude baseline + 25% (initial migration window)

Logging requirements:
- include `engine`, `group`, `session_present`, `is_scheduled_task`
- avoid full prompt logging and never log secrets

---

## 8) Suggested Timeline

Estimated total duration: `15-22 days` (single primary engineer).

Week 1:
- Phase 0, 1, and part of 2

Week 2:
- finish Phase 2 + complete Phase 3

Week 3:
- Phase 4, 5, 6

Week 4:
- Phase 7 and 8 (depends on canary outcomes)

---

## 9) Definition of Done (DoD)

Final checklist:
- [ ] `pi` engine is production-capable with Claude fallback
- [ ] Host/runner contract remains unchanged (`ContainerInput/ContainerOutput`)
- [ ] IPC tools work without provider-specific MCP dependency
- [ ] Multi-engine sessions are migrated and backward compatible
- [ ] Unit + E2E tests are green
- [ ] Operational runbook and rollback procedures documented
- [ ] `README.md` and `PLAN.md` updated
- [ ] No secret leakage in logs

---

## 10) Open Decisions (Must Be Closed Before Phase 3)

1. Exact Pi integration model: direct TS wrapper vs RPC sidecar.
2. Cost model: default provider and per-group limits.
3. Context compaction/truncation policy for Pi sessions.
4. Final Claude retirement policy (date + quality gates).

Recommendation:
- close decision (1) via a focused 1-2 day spike before Phase 3 starts.

---

## 11) Technical References
- `pi.dev` docs: providers, RPC, programmatic use (`@pi-ai/coding`).
- Current Pyldon code:
  - `container/agent_runner/main.py`
  - `container/agent_runner/ipc_mcp.py`
  - `container/Dockerfile`
  - `pyldon/container_runner.py`
  - `pyldon/main.py`
  - `pyldon/task_scheduler.py`
  - `pyldon/models.py`
