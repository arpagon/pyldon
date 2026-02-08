"""IPC-based MCP tools for Pyldon agent runner.

Migrated from NanoClaw container/agent-runner/src/ipc-mcp.ts.
Writes messages and tasks to files for the host process to pick up.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from croniter import croniter

IPC_DIR = Path("/workspace/ipc")
MESSAGES_DIR = IPC_DIR / "messages"
TASKS_DIR = IPC_DIR / "tasks"


def _write_ipc_file(directory: Path, data: dict) -> str:
    """Write a JSON file atomically to an IPC directory.

    Uses temp file + rename for atomic writes.
    Returns the filename.
    """
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"{int(time.time() * 1000)}-{os.urandom(3).hex()}.json"
    filepath = directory / filename

    # Atomic write: temp file then rename
    temp_path = filepath.with_suffix(".tmp")
    temp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temp_path.rename(filepath)

    return filename


def create_ipc_mcp(
    chat_jid: str,
    group_folder: str,
    is_main: bool,
) -> dict:
    """Create the IPC MCP tool definitions.

    Returns a dict mapping tool names to their handler functions,
    ready for registration with the Claude Agent SDK.

    Note: The actual MCP server creation depends on the claude-code-sdk
    Python API. This provides the tool definitions and handlers that
    will be wired up in main.py.
    """

    def send_message(text: str) -> str:
        """Send a message to the current Matrix room."""
        data = {
            "type": "message",
            "chatJid": chat_jid,
            "text": text,
            "groupFolder": group_folder,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        filename = _write_ipc_file(MESSAGES_DIR, data)
        return f"Message queued for delivery ({filename})"

    def schedule_task(
        prompt: str,
        schedule_type: str,
        schedule_value: str,
        context_mode: str = "group",
        target_group: str | None = None,
    ) -> str:
        """Schedule a recurring or one-time task."""
        # Validate schedule_value
        if schedule_type == "cron":
            try:
                croniter(schedule_value)
            except (ValueError, KeyError) as e:
                return f'Invalid cron: "{schedule_value}". Use format like "0 9 * * *" (daily 9am) or "*/5 * * * *" (every 5 min). Error: {e}'
        elif schedule_type == "interval":
            try:
                ms = int(schedule_value)
                if ms <= 0:
                    raise ValueError("must be positive")
            except (ValueError, TypeError):
                return f'Invalid interval: "{schedule_value}". Must be positive milliseconds (e.g., "300000" for 5 min).'
        elif schedule_type == "once":
            try:
                datetime.fromisoformat(schedule_value)
            except ValueError:
                return f'Invalid timestamp: "{schedule_value}". Use ISO 8601 format like "2026-02-01T15:30:00".'

        # Non-main groups can only schedule for themselves
        effective_target = (target_group if is_main and target_group else group_folder)

        data = {
            "type": "schedule_task",
            "prompt": prompt,
            "schedule_type": schedule_type,
            "schedule_value": schedule_value,
            "context_mode": context_mode or "group",
            "groupFolder": effective_target,
            "chatJid": chat_jid,
            "createdBy": group_folder,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        filename = _write_ipc_file(TASKS_DIR, data)
        return f"Task scheduled ({filename}): {schedule_type} - {schedule_value}"

    def list_tasks() -> str:
        """List all scheduled tasks visible to this group."""
        tasks_file = IPC_DIR / "current_tasks.json"

        if not tasks_file.exists():
            return "No scheduled tasks found."

        try:
            all_tasks = json.loads(tasks_file.read_text(encoding="utf-8"))
            tasks = (
                all_tasks
                if is_main
                else [t for t in all_tasks if t.get("groupFolder") == group_folder]
            )

            if not tasks:
                return "No scheduled tasks found."

            formatted = "\n".join(
                f"- [{t['id']}] {t['prompt'][:50]}... ({t['schedule_type']}: {t['schedule_value']}) - {t['status']}, next: {t.get('next_run', 'N/A')}"
                for t in tasks
            )
            return f"Scheduled tasks:\n{formatted}"
        except Exception as e:
            return f"Error reading tasks: {e}"

    def pause_task(task_id: str) -> str:
        """Pause a scheduled task."""
        data = {
            "type": "pause_task",
            "taskId": task_id,
            "groupFolder": group_folder,
            "isMain": is_main,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        _write_ipc_file(TASKS_DIR, data)
        return f"Task {task_id} pause requested."

    def resume_task(task_id: str) -> str:
        """Resume a paused task."""
        data = {
            "type": "resume_task",
            "taskId": task_id,
            "groupFolder": group_folder,
            "isMain": is_main,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        _write_ipc_file(TASKS_DIR, data)
        return f"Task {task_id} resume requested."

    def cancel_task(task_id: str) -> str:
        """Cancel and delete a scheduled task."""
        data = {
            "type": "cancel_task",
            "taskId": task_id,
            "groupFolder": group_folder,
            "isMain": is_main,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        _write_ipc_file(TASKS_DIR, data)
        return f"Task {task_id} cancellation requested."

    def register_group(
        jid: str, name: str, folder: str, trigger: str
    ) -> str:
        """Register a new Matrix room (main group only)."""
        if not is_main:
            return "Only the main group can register new groups."

        data = {
            "type": "register_group",
            "jid": jid,
            "name": name,
            "folder": folder,
            "trigger": trigger,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        _write_ipc_file(TASKS_DIR, data)
        return f'Group "{name}" registered. It will start receiving messages immediately.'

    # Return the tools dict - the caller (main.py) is responsible for
    # wiring these into the actual MCP server
    return {
        "send_message": send_message,
        "schedule_task": schedule_task,
        "list_tasks": list_tasks,
        "pause_task": pause_task,
        "resume_task": resume_task,
        "cancel_task": cancel_task,
        "register_group": register_group,
    }
