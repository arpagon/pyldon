"""Container runner for Pyldon.

Migrated from NanoClaw src/container-runner.ts.
Spawns agent execution in Docker containers and handles IPC.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from pyldon.config import (
    CONTAINER_IMAGE,
    CONTAINER_MAX_OUTPUT_SIZE,
    CONTAINER_TIMEOUT,
    DATA_DIR,
    GROUPS_DIR,
)
from pyldon.models import (
    AdditionalMount,
    ContainerInput,
    ContainerOutput,
    RegisteredGroup,
)
from pyldon.mount_security import validate_additional_mounts

# Sentinel markers for robust output parsing (must match agent-runner)
OUTPUT_START_MARKER = "---PYLDON_OUTPUT_START---"
OUTPUT_END_MARKER = "---PYLDON_OUTPUT_END---"


class VolumeMount:
    """A Docker volume mount."""

    def __init__(self, host_path: str, container_path: str, readonly: bool = False):
        self.host_path = host_path
        self.container_path = container_path
        self.readonly = readonly

    def __repr__(self) -> str:
        ro = " (ro)" if self.readonly else ""
        return f"{self.host_path} -> {self.container_path}{ro}"


def _build_volume_mounts(group: RegisteredGroup, is_main: bool) -> list[VolumeMount]:
    """Build the list of volume mounts for a container."""
    mounts: list[VolumeMount] = []
    project_root = str(Path.cwd())

    if is_main:
        # Main gets the entire project root mounted
        mounts.append(VolumeMount(project_root, "/workspace/project", readonly=False))

        # Main also gets its group folder as the working directory
        mounts.append(VolumeMount(
            str(GROUPS_DIR / group.folder),
            "/workspace/group",
            readonly=False,
        ))
    else:
        # Other groups only get their own folder
        mounts.append(VolumeMount(
            str(GROUPS_DIR / group.folder),
            "/workspace/group",
            readonly=False,
        ))

        # Global memory directory (read-only for non-main)
        global_dir = GROUPS_DIR / "global"
        if global_dir.exists():
            mounts.append(VolumeMount(
                str(global_dir), "/workspace/global", readonly=True
            ))

    # Identity file (personality, not in git) - mounted for ALL groups
    identity_file = DATA_DIR / "IDENTITY.md"
    if identity_file.exists():
        mounts.append(VolumeMount(
            str(identity_file),
            "/workspace/group/IDENTITY.md",
            readonly=True,
        ))

    # Per-group sessions directory (isolated from other groups)
    group_sessions_dir = DATA_DIR / "sessions" / group.folder / ".pi"
    group_sessions_dir.mkdir(parents=True, exist_ok=True)
    mounts.append(VolumeMount(
        str(group_sessions_dir),
        "/home/pyldon/.pi",
        readonly=False,
    ))

    # Per-group IPC namespace
    group_ipc_dir = DATA_DIR / "ipc" / group.folder
    (group_ipc_dir / "messages").mkdir(parents=True, exist_ok=True)
    (group_ipc_dir / "tasks").mkdir(parents=True, exist_ok=True)
    mounts.append(VolumeMount(
        str(group_ipc_dir),
        "/workspace/ipc",
        readonly=False,
    ))

    # Environment file directory (keeps credentials out of process listings)
    env_dir = DATA_DIR / "env"
    env_dir.mkdir(parents=True, exist_ok=True)
    env_file = Path.cwd() / ".env"
    if env_file.exists():
        env_content = env_file.read_text(encoding="utf-8")
        allowed_vars = [
            # pi.dev / Bedrock
            "AWS_BEARER_TOKEN_BEDROCK",
            "AWS_REGION",
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "GEMINI_API_KEY",
            "GROQ_API_KEY",
            "OPENROUTER_API_KEY",
            "MISTRAL_API_KEY",
            # pi config
            "PYLDON_PI_PROVIDER",
            "PYLDON_PI_MODEL",
        ]
        filtered_lines = [
            line
            for line in env_content.splitlines()
            if line.strip()
            and not line.strip().startswith("#")
            and any(line.strip().startswith(f"{v}=") for v in allowed_vars)
        ]
        if filtered_lines:
            (env_dir / "env").write_text("\n".join(filtered_lines) + "\n", encoding="utf-8")
            mounts.append(VolumeMount(
                str(env_dir), "/workspace/env-dir", readonly=True
            ))

    # Additional mounts validated against external allowlist
    if group.container_config and group.container_config.additional_mounts:
        validated = validate_additional_mounts(
            group.container_config.additional_mounts,
            group.name,
            is_main,
        )
        for m in validated:
            mounts.append(VolumeMount(
                str(m["host_path"]),
                str(m["container_path"]),
                readonly=bool(m["readonly"]),
            ))

    return mounts


def _build_docker_args(mounts: list[VolumeMount]) -> list[str]:
    """Build docker run arguments from mounts."""
    args = ["run", "-i", "--rm"]

    # Load environment variables from env file and pass via -e
    env_file = DATA_DIR / "env" / "env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").strip().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                args.extend(["-e", line])

    for mount in mounts:
        if mount.readonly:
            args.extend(["-v", f"{mount.host_path}:{mount.container_path}:ro"])
        else:
            args.extend(["-v", f"{mount.host_path}:{mount.container_path}"])

    args.append(CONTAINER_IMAGE)
    return args


async def run_container_agent(
    group: RegisteredGroup,
    input_data: ContainerInput,
) -> ContainerOutput:
    """Spawn a Docker container to run the agent.

    Returns the parsed output from the container.
    """
    start_time = time.monotonic()

    group_dir = GROUPS_DIR / group.folder
    group_dir.mkdir(parents=True, exist_ok=True)

    mounts = _build_volume_mounts(group, input_data.is_main)
    docker_args = _build_docker_args(mounts)

    logger.debug(
        "Container mount config: group={}, mounts={}",
        group.name,
        [str(m) for m in mounts],
    )
    logger.info(
        "Spawning container agent: group={}, mount_count={}, is_main={}",
        group.name, len(mounts), input_data.is_main,
    )

    logs_dir = GROUPS_DIR / group.folder / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    timeout = (
        group.container_config.timeout
        if group.container_config and group.container_config.timeout
        else CONTAINER_TIMEOUT
    )

    try:
        process = await asyncio.create_subprocess_exec(
            "docker",
            *docker_args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return ContainerOutput(
            status="error",
            result=None,
            error="Docker is not installed or not in PATH",
        )

    # Send input via stdin
    input_json = input_data.model_dump_json(by_alias=True)

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(input=input_json.encode()),
            timeout=timeout / 1000,  # Convert ms to seconds
        )
    except asyncio.TimeoutError:
        logger.error("Container timeout, killing: group={}", group.name)
        process.kill()
        await process.wait()
        return ContainerOutput(
            status="error",
            result=None,
            error=f"Container timed out after {timeout}ms",
        )

    duration_ms = int((time.monotonic() - start_time) * 1000)

    # Truncate output if too large
    stdout = stdout_bytes[:CONTAINER_MAX_OUTPUT_SIZE].decode(errors="replace")
    stderr = stderr_bytes[:CONTAINER_MAX_OUTPUT_SIZE].decode(errors="replace")

    # Log stderr lines
    for line in stderr.strip().splitlines():
        if line:
            logger.debug("[container:{}] {}", group.folder, line)

    # Write container log
    timestamp_str = datetime.now(timezone.utc).isoformat().replace(":", "-").replace(".", "-")
    log_file = logs_dir / f"container-{timestamp_str}.log"
    is_verbose = os.environ.get("LOG_LEVEL", "").lower() in ("debug", "trace")

    log_lines = [
        "=== Container Run Log ===",
        f"Timestamp: {datetime.now(timezone.utc).isoformat()}",
        f"Group: {group.name}",
        f"IsMain: {input_data.is_main}",
        f"Duration: {duration_ms}ms",
        f"Exit Code: {process.returncode}",
        "",
    ]

    if is_verbose:
        log_lines.extend([
            "=== Input ===",
            input_json,
            "",
            "=== Docker Args ===",
            " ".join(["docker"] + docker_args),
            "",
            "=== Mounts ===",
            "\n".join(str(m) for m in mounts),
            "",
            "=== Stderr ===",
            stderr,
            "",
            "=== Stdout ===",
            stdout,
        ])
    else:
        log_lines.extend([
            "=== Input Summary ===",
            f"Prompt length: {len(input_data.prompt)} chars",
            f"Session ID: {input_data.session_id or 'new'}",
            "",
            "=== Mounts ===",
            "\n".join(f"{m.container_path}{' (ro)' if m.readonly else ''}" for m in mounts),
            "",
        ])
        if process.returncode != 0:
            log_lines.extend([
                "=== Stderr (last 500 chars) ===",
                stderr[-500:],
                "",
            ])

    log_file.write_text("\n".join(log_lines), encoding="utf-8")
    logger.debug("Container log written: {}", log_file)

    if process.returncode != 0:
        logger.error(
            "Container exited with error: group={}, code={}, duration={}ms",
            group.name, process.returncode, duration_ms,
        )
        return ContainerOutput(
            status="error",
            result=None,
            error=f"Container exited with code {process.returncode}: {stderr[-200:]}",
        )

    # Extract JSON between sentinel markers
    try:
        start_idx = stdout.find(OUTPUT_START_MARKER)
        end_idx = stdout.find(OUTPUT_END_MARKER)

        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            json_line = stdout[start_idx + len(OUTPUT_START_MARKER):end_idx].strip()
        else:
            # Fallback: last non-empty line
            lines = stdout.strip().splitlines()
            json_line = lines[-1] if lines else ""

        output = ContainerOutput(**json.loads(json_line))

        # Extract extra fields for logging (tool_calls, stderr from pi)
        try:
            raw_output = json.loads(json_line)
            agent_tool_calls = raw_output.get("tool_calls", [])
            agent_stderr = raw_output.get("stderr", "")
        except Exception:
            agent_tool_calls = []
            agent_stderr = ""

        # Append tool calls and stderr to log
        if agent_tool_calls:
            extra_lines = ["", "=== Tool Calls ==="]
            for tc in agent_tool_calls:
                extra_lines.append(f"- [{tc.get('status', '?')}] {tc.get('tool', '?')}")
                args = tc.get("args", {})
                if args:
                    args_str = json.dumps(args) if isinstance(args, dict) else str(args)
                    extra_lines.append(f"  args: {args_str[:200]}")
                preview = tc.get("result_preview", "")
                if preview:
                    extra_lines.append(f"  result: {preview[:300]}")
            log_file = logs_dir / f"container-{timestamp_str}.log"
            existing = log_file.read_text(encoding="utf-8") if log_file.exists() else ""
            log_file.write_text(existing + "\n".join(extra_lines) + "\n", encoding="utf-8")

        if agent_stderr:
            log_file = logs_dir / f"container-{timestamp_str}.log"
            existing = log_file.read_text(encoding="utf-8") if log_file.exists() else ""
            log_file.write_text(existing + "\n=== Pi Stderr ===\n" + agent_stderr + "\n", encoding="utf-8")

        logger.info(
            "Container completed: group={}, duration={}ms, status={}, has_result={}, tools={}",
            group.name, duration_ms, output.status, bool(output.result), len(agent_tool_calls),
        )
        return output

    except Exception as e:
        logger.error(
            "Failed to parse container output: group={}, error={}, stdout_tail={}",
            group.name, e, stdout[-500:],
        )
        return ContainerOutput(
            status="error",
            result=None,
            error=f"Failed to parse container output: {e}",
        )


def write_tasks_snapshot(
    group_folder: str,
    is_main: bool,
    tasks: list[dict[str, Any]],
) -> None:
    """Write tasks snapshot for container consumption."""
    group_ipc_dir = DATA_DIR / "ipc" / group_folder
    group_ipc_dir.mkdir(parents=True, exist_ok=True)

    # Main sees all tasks, others only see their own
    filtered = tasks if is_main else [t for t in tasks if t.get("groupFolder") == group_folder]

    tasks_file = group_ipc_dir / "current_tasks.json"
    tasks_file.write_text(json.dumps(filtered, indent=2), encoding="utf-8")


def write_groups_snapshot(
    group_folder: str,
    is_main: bool,
    groups: list[dict[str, Any]],
) -> None:
    """Write available groups snapshot for the container to read.

    Only main group can see all available groups.
    """
    group_ipc_dir = DATA_DIR / "ipc" / group_folder
    group_ipc_dir.mkdir(parents=True, exist_ok=True)

    # Main sees all groups; others see nothing
    visible = groups if is_main else []

    groups_file = group_ipc_dir / "available_groups.json"
    groups_file.write_text(
        json.dumps({
            "groups": visible,
            "lastSync": datetime.now(timezone.utc).isoformat(),
        }, indent=2),
        encoding="utf-8",
    )
