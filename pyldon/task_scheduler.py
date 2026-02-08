"""Task scheduler for Pyldon.

Migrated from NanoClaw src/task-scheduler.ts.
Runs scheduled tasks as containerized agents.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path

from croniter import croniter
from loguru import logger

from pyldon.config import (
    GROUPS_DIR,
    MAIN_GROUP_FOLDER,
    SCHEDULER_POLL_INTERVAL,
    TIMEZONE,
)
from pyldon.container_runner import run_container_agent, write_tasks_snapshot
from pyldon.db import (
    get_all_tasks,
    get_due_tasks,
    get_task_by_id,
    log_task_run,
    update_task_after_run,
)
from pyldon.models import (
    ContainerInput,
    RegisteredGroup,
    ScheduledTask,
    TaskRunLog,
)


class SchedulerDependencies:
    """Dependencies injected into the scheduler."""

    def __init__(
        self,
        send_message: Callable[[str, str], Awaitable[None]],
        registered_groups: Callable[[], dict[str, RegisteredGroup]],
        get_sessions: Callable[[], dict[str, str]],
    ):
        self.send_message = send_message
        self.registered_groups = registered_groups
        self.get_sessions = get_sessions


async def _run_task(task: ScheduledTask, deps: SchedulerDependencies) -> None:
    """Execute a single scheduled task."""
    start_time = time.monotonic()
    group_dir = GROUPS_DIR / task.group_folder
    group_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Running scheduled task: task_id={}, group={}", task.id, task.group_folder)

    groups = deps.registered_groups()
    group = next(
        (g for g in groups.values() if g.folder == task.group_folder),
        None,
    )

    if group is None:
        logger.error("Group not found for task: task_id={}, group={}", task.id, task.group_folder)
        await log_task_run(TaskRunLog(
            task_id=task.id,
            run_at=datetime.now(timezone.utc).isoformat(),
            duration_ms=int((time.monotonic() - start_time) * 1000),
            status="error",
            result=None,
            error=f"Group not found: {task.group_folder}",
        ))
        return

    # Update tasks snapshot for container to read
    is_main = task.group_folder == MAIN_GROUP_FOLDER
    all_tasks = await get_all_tasks()
    write_tasks_snapshot(
        task.group_folder,
        is_main,
        [
            {
                "id": t.id,
                "groupFolder": t.group_folder,
                "prompt": t.prompt,
                "schedule_type": t.schedule_type,
                "schedule_value": t.schedule_value,
                "status": t.status,
                "next_run": t.next_run,
            }
            for t in all_tasks
        ],
    )

    result: str | None = None
    error: str | None = None

    # For group context mode, use the group's current session
    sessions = deps.get_sessions()
    session_id = sessions.get(task.group_folder) if task.context_mode == "group" else None

    try:
        output = await run_container_agent(
            group,
            ContainerInput(
                prompt=task.prompt,
                session_id=session_id,
                group_folder=task.group_folder,
                chat_jid=task.chat_jid,
                is_main=is_main,
                is_scheduled_task=True,
            ),
        )

        if output.status == "error":
            error = output.error or "Unknown error"
        else:
            result = output.result

        logger.info("Task completed: task_id={}, duration={}ms", task.id, int((time.monotonic() - start_time) * 1000))
    except Exception as e:
        error = str(e)
        logger.error("Task failed: task_id={}, error={}", task.id, error)

    duration_ms = int((time.monotonic() - start_time) * 1000)

    await log_task_run(TaskRunLog(
        task_id=task.id,
        run_at=datetime.now(timezone.utc).isoformat(),
        duration_ms=duration_ms,
        status="error" if error else "success",
        result=result,
        error=error,
    ))

    # Compute next run
    next_run: str | None = None
    if task.schedule_type == "cron":
        cron = croniter(task.schedule_value, datetime.now(timezone.utc))
        next_run = cron.get_next(datetime).isoformat()
    elif task.schedule_type == "interval":
        ms = int(task.schedule_value)
        next_run = datetime.fromtimestamp(
            time.time() + ms / 1000, tz=timezone.utc
        ).isoformat()
    # 'once' tasks have no next run

    result_summary = (
        f"Error: {error}"
        if error
        else (result[:200] if result else "Completed")
    )
    await update_task_after_run(task.id, next_run, result_summary)


async def start_scheduler_loop(deps: SchedulerDependencies) -> None:
    """Start the scheduler loop as an async task.

    Polls for due tasks and runs them.
    """
    logger.info("Scheduler loop started")

    while True:
        try:
            due_tasks = await get_due_tasks()
            if due_tasks:
                logger.info("Found {} due task(s)", len(due_tasks))

            for task in due_tasks:
                # Re-check task status in case it was paused/cancelled
                current = await get_task_by_id(task.id)
                if not current or current.status != "active":
                    continue

                await _run_task(current, deps)
        except Exception as e:
            logger.error("Error in scheduler loop: {}", e)

        await asyncio.sleep(SCHEDULER_POLL_INTERVAL)
