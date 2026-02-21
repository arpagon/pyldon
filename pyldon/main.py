"""Main entry point for Pyldon.

Migrated from NanoClaw src/index.ts.
Connects to Matrix, routes messages to Claude Agent SDK running in Docker containers.
Each room has isolated filesystem and memory.
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from croniter import croniter
from loguru import logger

from nio import (
    JoinedRoomsResponse,
    RoomGetStateEventResponse,
)

from pyldon.config import (
    ASSISTANT_NAME,
    DATA_DIR,
    GROUPS_DIR,
    IPC_POLL_INTERVAL,
    MAIN_GROUP_FOLDER,
    ROOM_SYNC_INTERVAL_MS,
    TIMEZONE,
    TRIGGER_PATTERN,
)
from pyldon.container_runner import (
    run_container_agent,
    write_groups_snapshot,
    write_tasks_snapshot,
)
from pyldon.db import (
    close_database,
    create_task,
    delete_task,
    get_all_chats,
    get_all_tasks,
    get_last_group_sync,
    get_messages_since,
    get_recent_messages,
    get_task_by_id,
    init_database,
    set_last_group_sync,
    store_chat_metadata,
    store_message,
    update_chat_name,
    update_task,
)
from pyldon.matrix_client import (
    get_matrix_client,
    get_matrix_config,
    init_matrix_client,
    send_matrix_message,
    set_matrix_typing,
    stop_matrix_client,
)
from pyldon.matrix_monitor import start_matrix_monitor
from pyldon.models import (
    AvailableGroup,
    ContainerInput,
    MatrixMessage,
    MatrixRoomConfig,
    RegisteredGroup,
    ScheduledTask,
)
from pyldon.pairing import (
    build_pairing_message,
    create_pairing_request,
    get_owner,
    get_pending_pairing,
    is_main_room,
    is_paired,
)
from pyldon.task_scheduler import SchedulerDependencies, start_scheduler_loop
from pyldon.utils import load_json, save_json

# --- State ---

_last_timestamp: str = ""
_sessions: dict[str, str] = {}
_registered_groups: dict[str, RegisteredGroup] = {}
_last_agent_timestamp: dict[str, str] = {}


def _load_state() -> None:
    """Load persisted state from disk."""
    global _last_timestamp, _sessions, _registered_groups, _last_agent_timestamp

    state_path = DATA_DIR / "router_state.json"
    state = load_json(state_path, {})
    _last_timestamp = state.get("last_timestamp", "")
    _last_agent_timestamp = state.get("last_agent_timestamp", {})

    _sessions = load_json(DATA_DIR / "sessions.json", {})

    groups_data = load_json(DATA_DIR / "registered_groups.json", {})
    _registered_groups = {
        k: RegisteredGroup(**v) if isinstance(v, dict) else v
        for k, v in groups_data.items()
    }

    logger.info("State loaded: {} registered group(s)", len(_registered_groups))


def _save_state() -> None:
    """Persist state to disk."""
    save_json(
        DATA_DIR / "router_state.json",
        {
            "last_timestamp": _last_timestamp,
            "last_agent_timestamp": _last_agent_timestamp,
        },
    )
    save_json(DATA_DIR / "sessions.json", _sessions)


def _register_group(room_id: str, group: RegisteredGroup) -> None:
    """Register a group and persist to disk."""
    _registered_groups[room_id] = group
    save_json(
        DATA_DIR / "registered_groups.json",
        {k: v.model_dump() for k, v in _registered_groups.items()},
    )

    # Create group folder
    group_dir = GROUPS_DIR / group.folder
    (group_dir / "logs").mkdir(parents=True, exist_ok=True)

    logger.info("Group registered: room={}, name={}, folder={}", room_id, group.name, group.folder)


# --- Room Metadata ---


async def _sync_room_metadata(force: bool = False) -> None:
    """Sync room metadata from Matrix (respects 24h cache)."""
    if not force:
        last_sync = await get_last_group_sync()
        if last_sync:
            last_sync_time = datetime.fromisoformat(last_sync)
            elapsed_ms = (datetime.now(timezone.utc) - last_sync_time).total_seconds() * 1000
            if elapsed_ms < ROOM_SYNC_INTERVAL_MS:
                logger.debug("Skipping room sync - synced recently: {}", last_sync)
                return

    try:
        logger.info("Syncing room metadata from Matrix...")
        client = get_matrix_client()
        resp = await client.joined_rooms()

        count = 0
        if isinstance(resp, JoinedRoomsResponse):
            for room_id in resp.rooms:
                try:
                    # Try to get room name from state
                    state_resp = await client.room_get_state_event(room_id, "m.room.name")
                    name = None
                    if isinstance(state_resp, RoomGetStateEventResponse):
                        name = state_resp.content.get("name")
                    if name:
                        await update_chat_name(room_id, name)
                    else:
                        await update_chat_name(room_id, room_id)
                    count += 1
                except Exception:
                    await update_chat_name(room_id, room_id)
                    count += 1

        await set_last_group_sync()
        logger.info("Room metadata synced: {} room(s)", count)
    except Exception as e:
        logger.error("Failed to sync room metadata: {}", e)


def _get_available_groups() -> list[dict[str, Any]]:
    """Get available rooms for the agent."""
    # This is sync but we'll call from async context with cached data
    # We need to use a blocking approach since get_all_chats is async
    # This will be called from async contexts, so we'll refactor the call site
    return []  # Placeholder; actual implementation is async


async def _get_available_groups_async() -> list[dict[str, Any]]:
    """Get available rooms for the agent (async version)."""
    chats = await get_all_chats()
    registered_ids = set(_registered_groups.keys())

    return [
        {
            "jid": c.jid,
            "name": c.name,
            "lastActivity": c.last_message_time,
            "isRegistered": c.jid in registered_ids,
        }
        for c in chats
        if c.jid != "__group_sync__" and c.jid.startswith("!")
    ]


# --- Message Processing ---


async def _process_matrix_message(
    message: MatrixMessage,
    room_config: MatrixRoomConfig | None,
    is_main: bool,
) -> None:
    """Process an incoming Matrix message."""
    global _last_agent_timestamp
    # Handle pairing flow if not paired yet
    if not is_paired():
        pending = get_pending_pairing()
        if pending:
            logger.info("Pairing already pending, sending reminder: sender={}", message.sender)
            await send_matrix_message(
                message.room_id,
                build_pairing_message(pending.code),
                message.thread_id,
            )
        else:
            code = create_pairing_request(message.sender, message.room_id, message.sender_name)
            logger.info("Created pairing request: sender={}, room={}, code={}", message.sender, message.room_id, code)
            await send_matrix_message(
                message.room_id,
                build_pairing_message(code),
                message.thread_id,
            )
        return

    # Determine folder for this room
    if room_config and room_config.folder:
        folder = room_config.folder
    elif is_main:
        folder = MAIN_GROUP_FOLDER
    else:
        folder = f"matrix-{re.sub(r'[^a-zA-Z0-9]', '_', message.room_id)}"

    # Build group object for container runner
    group = _registered_groups.get(message.room_id)
    if group is None:
        group = RegisteredGroup(
            name=folder,
            folder=folder,
            trigger=f"@{ASSISTANT_NAME}",
            added_at=datetime.now(timezone.utc).isoformat(),
        )
        _register_group(message.room_id, group)

    # Store the message
    await store_message(
        id=message.event_id,
        chat_id=message.room_id,
        sender=message.sender,
        sender_name=message.sender_name,
        content=message.content,
        timestamp=message.timestamp,
        is_from_me=False,
    )

    # Store chat metadata
    await store_chat_metadata(message.room_id, message.timestamp, message.sender_name)

    # Get recent conversation for context (last 20 messages)
    # Plus any new messages since last agent interaction
    since_timestamp = _last_agent_timestamp.get(message.room_id, "")
    recent_messages = await get_recent_messages(message.room_id, limit=50)
    new_messages = await get_messages_since(message.room_id, since_timestamp, ASSISTANT_NAME)

    # Merge: use recent for context, ensure new messages are included
    seen_ids = set()
    all_messages = []
    for m in recent_messages:
        if m.id not in seen_ids:
            seen_ids.add(m.id)
            all_messages.append(m)
    for m in new_messages:
        if m.id not in seen_ids:
            seen_ids.add(m.id)
            all_messages.append(m)
    all_messages.sort(key=lambda m: m.timestamp)

    # Build XML-formatted prompt
    def _escape_xml(s: str) -> str:
        return (
            s.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    lines = [
        f'<message sender="{_escape_xml(m.sender_name)}" time="{m.timestamp}">{_escape_xml(m.content)}</message>'
        for m in all_messages
    ]
    prompt = f"<messages>\n{chr(10).join(lines)}\n</messages>"

    if not prompt.strip():
        return

    logger.info("Processing message: group={}, message_count={} (recent={}, new={})",
                group.name, len(all_messages), len(recent_messages), len(new_messages))

    await set_matrix_typing(message.room_id, True)
    response = await _run_agent(group, prompt, message.room_id)
    await set_matrix_typing(message.room_id, False)

    if response:
        _last_agent_timestamp[message.room_id] = message.timestamp
        _save_state()
        await send_matrix_message(message.room_id, response, message.thread_id)

        # Store bot response in DB for conversation context
        await store_message(
            id=f"bot-{message.event_id}",
            chat_id=message.room_id,
            sender="bot",
            sender_name=ASSISTANT_NAME,
            content=response,
            timestamp=datetime.now(timezone.utc).isoformat(),
            is_from_me=True,
        )


async def _run_agent(
    group: RegisteredGroup, prompt: str, chat_id: str
) -> str | None:
    """Run the agent in a container and return the response."""
    is_main = is_main_room(chat_id)
    session_id = _sessions.get(group.folder)

    # Update tasks snapshot
    all_tasks = await get_all_tasks()
    write_tasks_snapshot(
        group.folder,
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

    # Update available groups snapshot
    available_groups = await _get_available_groups_async()
    write_groups_snapshot(
        group.folder,
        is_main,
        available_groups,
    )

    try:
        output = await run_container_agent(
            group,
            ContainerInput(
                prompt=prompt,
                session_id=session_id,
                group_folder=group.folder,
                chat_jid=chat_id,
                is_main=is_main,
            ),
        )

        if output.new_session_id:
            _sessions[group.folder] = output.new_session_id
            save_json(DATA_DIR / "sessions.json", _sessions)

        if output.status == "error":
            logger.error("Container agent error: group={}, error={}", group.name, output.error)
            return None

        return output.result
    except Exception as e:
        logger.error("Agent error: group={}, error={}", group.name, e)
        return None


# --- IPC Processing ---


async def _send_message(room_id: str, text: str) -> None:
    """Send a message to a Matrix room."""
    try:
        await send_matrix_message(room_id, text)
        logger.info("Message sent: room={}, length={}", room_id, len(text))

        # Store bot message in DB for conversation context
        await store_message(
            id=f"bot-ipc-{int(datetime.now(timezone.utc).timestamp() * 1000)}",
            chat_id=room_id,
            sender="bot",
            sender_name=ASSISTANT_NAME,
            content=text,
            timestamp=datetime.now(timezone.utc).isoformat(),
            is_from_me=True,
        )
    except Exception as e:
        logger.error("Failed to send message: room={}, error={}", room_id, e)


async def _process_task_ipc(
    data: dict[str, Any], source_group: str, is_main: bool
) -> None:
    """Process a task IPC file."""
    action = data.get("type")

    if action == "schedule_task":
        prompt: str | None = data.get("prompt")
        schedule_type: str | None = data.get("schedule_type")
        schedule_value: str | None = data.get("schedule_value")
        group_folder: str | None = data.get("groupFolder")

        if not prompt or not schedule_type or not schedule_value or not group_folder:
            return

        if schedule_type not in ("cron", "interval", "once"):
            logger.warning("Invalid schedule_type: {}", schedule_type)
            return

        target_group = group_folder
        if not is_main and target_group != source_group:
            logger.warning("Unauthorized schedule_task attempt: source={}, target={}", source_group, target_group)
            return

        # Find target group's room ID
        target_id = next(
            (k for k, g in _registered_groups.items() if g.folder == target_group),
            None,
        )
        if not target_id:
            logger.warning("Cannot schedule task: target group not registered: {}", target_group)
            return

        # Compute next_run
        next_run: str | None = None
        if schedule_type == "cron":
            try:
                cron = croniter(schedule_value, datetime.now(timezone.utc))
                next_run = cron.get_next(datetime).isoformat()
            except Exception:
                logger.warning("Invalid cron expression: {}", schedule_value)
                return
        elif schedule_type == "interval":
            ms = int(schedule_value)
            if ms <= 0:
                logger.warning("Invalid interval: {}", schedule_value)
                return
            next_run = datetime.fromtimestamp(
                time.time() + ms / 1000, tz=timezone.utc
            ).isoformat()
        elif schedule_type == "once":
            try:
                scheduled = datetime.fromisoformat(schedule_value)
                next_run = scheduled.isoformat()
            except ValueError:
                logger.warning("Invalid timestamp: {}", schedule_value)
                return

        task_id = f"task-{int(time.time() * 1000)}-{id(data) % 1000000:06x}"
        context_mode = data.get("context_mode", "isolated")
        if context_mode not in ("group", "isolated"):
            context_mode = "isolated"

        await create_task(ScheduledTask(
            id=task_id,
            group_folder=target_group,
            chat_jid=target_id,
            prompt=prompt,
            schedule_type=schedule_type,
            schedule_value=schedule_value,
            context_mode=context_mode,
            next_run=next_run,
            status="active",
            created_at=datetime.now(timezone.utc).isoformat(),
        ))
        logger.info("Task created via IPC: task_id={}, source={}, target={}", task_id, source_group, target_group)

    elif action == "pause_task":
        task_id = data.get("taskId")
        if task_id:
            task = await get_task_by_id(task_id)
            if task and (is_main or task.group_folder == source_group):
                await update_task(task_id, status="paused")
                logger.info("Task paused via IPC: task_id={}, source={}", task_id, source_group)
            else:
                logger.warning("Unauthorized task pause: task_id={}, source={}", task_id, source_group)

    elif action == "resume_task":
        task_id = data.get("taskId")
        if task_id:
            task = await get_task_by_id(task_id)
            if task and (is_main or task.group_folder == source_group):
                await update_task(task_id, status="active")
                logger.info("Task resumed via IPC: task_id={}, source={}", task_id, source_group)
            else:
                logger.warning("Unauthorized task resume: task_id={}, source={}", task_id, source_group)

    elif action == "cancel_task":
        task_id = data.get("taskId")
        if task_id:
            task = await get_task_by_id(task_id)
            if task and (is_main or task.group_folder == source_group):
                await delete_task(task_id)
                logger.info("Task cancelled via IPC: task_id={}, source={}", task_id, source_group)
            else:
                logger.warning("Unauthorized task cancel: task_id={}, source={}", task_id, source_group)

    elif action == "refresh_groups":
        if is_main:
            logger.info("Room metadata refresh requested via IPC: source={}", source_group)
            await _sync_room_metadata(force=True)
            available_groups = await _get_available_groups_async()
            write_groups_snapshot(source_group, True, available_groups)
        else:
            logger.warning("Unauthorized refresh_groups attempt: source={}", source_group)

    elif action == "register_group":
        if not is_main:
            logger.warning("Unauthorized register_group attempt: source={}", source_group)
            return
        jid: str | None = data.get("jid")
        name: str | None = data.get("name")
        folder: str | None = data.get("folder")
        trigger: str | None = data.get("trigger")
        if jid and name and folder and trigger:
            _register_group(jid, RegisteredGroup(
                name=name,
                folder=folder,
                trigger=trigger,
                added_at=datetime.now(timezone.utc).isoformat(),
            ))
        else:
            logger.warning("Invalid register_group request - missing fields: {}", data)

    else:
        logger.warning("Unknown IPC task type: {}", action)


async def _start_ipc_watcher() -> None:
    """Start the IPC watcher loop.

    Polls per-group IPC directories for message and task files.
    """
    ipc_base_dir = DATA_DIR / "ipc"
    ipc_base_dir.mkdir(parents=True, exist_ok=True)

    logger.info("IPC watcher started (per-group namespaces)")

    while True:
        try:
            # Scan all group IPC directories
            if ipc_base_dir.exists():
                group_folders = [
                    f.name
                    for f in ipc_base_dir.iterdir()
                    if f.is_dir() and f.name != "errors"
                ]
            else:
                group_folders = []

            for source_group in group_folders:
                is_main = source_group == MAIN_GROUP_FOLDER
                messages_dir = ipc_base_dir / source_group / "messages"
                tasks_dir = ipc_base_dir / source_group / "tasks"

                # Process messages
                if messages_dir.exists():
                    for msg_file in sorted(messages_dir.glob("*.json")):
                        try:
                            data = json.loads(msg_file.read_text(encoding="utf-8"))
                            if data.get("type") == "message" and data.get("chatJid") and data.get("text"):
                                target_group = _registered_groups.get(data["chatJid"])
                                if is_main or (target_group and target_group.folder == source_group):
                                    await _send_message(data["chatJid"], data["text"])
                                    logger.info("IPC message sent: chat={}, source={}", data["chatJid"], source_group)
                                else:
                                    logger.warning("Unauthorized IPC message attempt: chat={}, source={}", data["chatJid"], source_group)
                            msg_file.unlink()
                        except Exception as e:
                            logger.error("Error processing IPC message: file={}, source={}, error={}", msg_file, source_group, e)
                            error_dir = ipc_base_dir / "errors"
                            error_dir.mkdir(parents=True, exist_ok=True)
                            try:
                                msg_file.rename(error_dir / f"{source_group}-{msg_file.name}")
                            except Exception:
                                pass

                # Process tasks
                if tasks_dir.exists():
                    for task_file in sorted(tasks_dir.glob("*.json")):
                        try:
                            data = json.loads(task_file.read_text(encoding="utf-8"))
                            await _process_task_ipc(data, source_group, is_main)
                            task_file.unlink()
                        except Exception as e:
                            logger.error("Error processing IPC task: file={}, source={}, error={}", task_file, source_group, e)
                            error_dir = ipc_base_dir / "errors"
                            error_dir.mkdir(parents=True, exist_ok=True)
                            try:
                                task_file.rename(error_dir / f"{source_group}-{task_file.name}")
                            except Exception:
                                pass
        except Exception as e:
            logger.error("Error in IPC watcher: {}", e)

        await asyncio.sleep(IPC_POLL_INTERVAL)


# --- Startup ---


def _ensure_docker_running() -> None:
    """Check that Docker is available and running."""
    try:
        subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
            check=True,
        )
        logger.debug("Docker daemon is running")
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        logger.error("Docker daemon is not running")
        print("\n" + "=" * 64)
        print("  FATAL: Docker is not running")
        print("")
        print("  Agents cannot run without Docker. To fix:")
        print("  Linux: sudo systemctl start docker")
        print("  macOS: Start Docker Desktop")
        print("")
        print("  Install from: https://docker.com/products/docker-desktop")
        print("=" * 64 + "\n")
        raise RuntimeError("Docker is required but not running")


async def _connect_matrix() -> None:
    """Initialize Matrix connection and start all subsystems."""
    client = await init_matrix_client()
    config = get_matrix_config()

    start_matrix_monitor(_process_matrix_message)

    # Start the sync loop
    logger.info("Starting Matrix sync: homeserver={}, user_id={}", config.homeserver, config.user_id)

    # If E2EE, do a first sync to get device lists, then upload keys and trust devices
    if config.encryption:
        logger.info("E2EE: performing initial sync to get device lists...")
        resp = await client.sync(timeout=30000, full_state=True)

        # Upload device keys if needed
        if client.should_upload_keys:
            logger.info("E2EE: uploading device keys...")
            await client.keys_upload()

        # Query keys for all users we share rooms with
        if client.should_query_keys:
            logger.info("E2EE: querying device keys for room members...")
            await client.keys_query()

        # Auto-trust all devices of all users in our rooms (bot behavior)
        logger.info("E2EE: auto-trusting all devices in joined rooms...")
        for room_id, room in client.rooms.items():
            for user_id in room.users:
                devices = client.device_store.active_user_devices(user_id)
                for device in devices:
                    if client.olm and not client.olm.is_device_verified(device):
                        logger.debug(
                            "E2EE: trusting device {} of user {}",
                            device.device_id,
                            user_id,
                        )
                        client.verify_device(device)

        logger.info("E2EE: device trust setup complete")

    # Sync room metadata on startup
    try:
        await _sync_room_metadata()
    except Exception as e:
        logger.error("Initial room sync failed: {}", e)

    # Start scheduler loop
    scheduler_deps = SchedulerDependencies(
        send_message=_send_message,
        registered_groups=lambda: _registered_groups,
        get_sessions=lambda: _sessions,
    )

    # Start all background tasks
    asyncio.create_task(start_scheduler_loop(scheduler_deps))
    asyncio.create_task(_start_ipc_watcher())

    # Start the matrix-nio sync loop (this blocks)
    await client.sync_forever(timeout=30000, full_state=True)


async def _async_main() -> None:
    """Async main entry point."""
    _ensure_docker_running()

    await init_database()
    logger.info("Database initialized")

    _load_state()

    logger.info("Pyldon running on Matrix (trigger: @{})", ASSISTANT_NAME)
    try:
        await _connect_matrix()
    finally:
        await stop_matrix_client()
        await close_database()


def main_entry() -> None:
    """Synchronous entry point for the `pyldon` console script."""
    try:
        asyncio.run(_async_main())
    except KeyboardInterrupt:
        logger.info("Pyldon shutting down")
    except Exception as e:
        logger.error("Failed to start Pyldon: {}", e)
        raise SystemExit(1)


if __name__ == "__main__":
    main_entry()
