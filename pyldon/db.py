"""SQLite database operations for Pyldon.

Migrated from NanoClaw src/db.ts. Uses aiosqlite for async operations.
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite
from loguru import logger

from pyldon.config import STORE_DIR
from pyldon.models import (
    ChatInfo,
    NewMessage,
    ScheduledTask,
    TaskRunLog,
)

_db: aiosqlite.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
    jid TEXT PRIMARY KEY,
    name TEXT,
    last_message_time TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT,
    chat_jid TEXT,
    sender TEXT,
    sender_name TEXT,
    content TEXT,
    timestamp TEXT,
    is_from_me INTEGER,
    PRIMARY KEY (id, chat_jid),
    FOREIGN KEY (chat_jid) REFERENCES chats(jid)
);
CREATE INDEX IF NOT EXISTS idx_timestamp ON messages(timestamp);

CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id TEXT PRIMARY KEY,
    group_folder TEXT NOT NULL,
    chat_jid TEXT NOT NULL,
    prompt TEXT NOT NULL,
    schedule_type TEXT NOT NULL,
    schedule_value TEXT NOT NULL,
    context_mode TEXT DEFAULT 'isolated',
    next_run TEXT,
    last_run TEXT,
    last_result TEXT,
    status TEXT DEFAULT 'active',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_next_run ON scheduled_tasks(next_run);
CREATE INDEX IF NOT EXISTS idx_status ON scheduled_tasks(status);

CREATE TABLE IF NOT EXISTS task_run_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    run_at TEXT NOT NULL,
    duration_ms INTEGER NOT NULL,
    status TEXT NOT NULL,
    result TEXT,
    error TEXT,
    FOREIGN KEY (task_id) REFERENCES scheduled_tasks(id)
);
CREATE INDEX IF NOT EXISTS idx_task_run_logs ON task_run_logs(task_id, run_at);
"""


async def init_database() -> None:
    """Initialize the SQLite database and create tables."""
    global _db

    db_path = STORE_DIR / "messages.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    _db = await aiosqlite.connect(str(db_path))
    _db.row_factory = aiosqlite.Row

    await _db.executescript(SCHEMA)
    await _db.commit()

    # Migrations for existing DBs
    for migration_sql in [
        "ALTER TABLE messages ADD COLUMN sender_name TEXT",
        "ALTER TABLE scheduled_tasks ADD COLUMN context_mode TEXT DEFAULT 'isolated'",
    ]:
        try:
            await _db.execute(migration_sql)
            await _db.commit()
        except Exception:
            pass  # Column already exists

    logger.info("Database initialized at {}", db_path)


def _get_db() -> aiosqlite.Connection:
    """Get the database connection, raising if not initialized."""
    if _db is None:
        raise RuntimeError("Database not initialized. Call init_database() first.")
    return _db


async def close_database() -> None:
    """Close the database connection."""
    global _db
    if _db is not None:
        await _db.close()
        _db = None


# --- Chat Operations ---


async def store_chat_metadata(
    chat_jid: str, timestamp: str, name: str | None = None
) -> None:
    """Store chat metadata only (no message content)."""
    db = _get_db()
    if name:
        await db.execute(
            """
            INSERT INTO chats (jid, name, last_message_time) VALUES (?, ?, ?)
            ON CONFLICT(jid) DO UPDATE SET
                name = excluded.name,
                last_message_time = MAX(last_message_time, excluded.last_message_time)
            """,
            (chat_jid, name, timestamp),
        )
    else:
        await db.execute(
            """
            INSERT INTO chats (jid, name, last_message_time) VALUES (?, ?, ?)
            ON CONFLICT(jid) DO UPDATE SET
                last_message_time = MAX(last_message_time, excluded.last_message_time)
            """,
            (chat_jid, chat_jid, timestamp),
        )
    await db.commit()


async def update_chat_name(chat_jid: str, name: str) -> None:
    """Update chat name without changing timestamp for existing chats."""
    db = _get_db()
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        """
        INSERT INTO chats (jid, name, last_message_time) VALUES (?, ?, ?)
        ON CONFLICT(jid) DO UPDATE SET name = excluded.name
        """,
        (chat_jid, name, now),
    )
    await db.commit()


async def get_all_chats() -> list[ChatInfo]:
    """Get all known chats, ordered by most recent activity."""
    db = _get_db()
    cursor = await db.execute(
        """
        SELECT jid, name, last_message_time
        FROM chats
        ORDER BY last_message_time DESC
        """
    )
    rows = await cursor.fetchall()
    return [ChatInfo(jid=r["jid"], name=r["name"], last_message_time=r["last_message_time"]) for r in rows]


async def get_last_group_sync() -> str | None:
    """Get timestamp of last group metadata sync."""
    db = _get_db()
    cursor = await db.execute(
        "SELECT last_message_time FROM chats WHERE jid = '__group_sync__'"
    )
    row = await cursor.fetchone()
    return row["last_message_time"] if row else None


async def set_last_group_sync() -> None:
    """Record that group metadata was synced."""
    db = _get_db()
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT OR REPLACE INTO chats (jid, name, last_message_time) VALUES ('__group_sync__', '__group_sync__', ?)",
        (now,),
    )
    await db.commit()


# --- Message Operations ---


async def store_message(
    *,
    id: str,
    chat_id: str,
    sender: str,
    sender_name: str,
    content: str,
    timestamp: str,
    is_from_me: bool,
) -> None:
    """Store a message with full content."""
    db = _get_db()
    await db.execute(
        """
        INSERT OR REPLACE INTO messages
            (id, chat_jid, sender, sender_name, content, timestamp, is_from_me)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (id, chat_id, sender, sender_name, content, timestamp, 1 if is_from_me else 0),
    )
    await db.commit()


async def get_messages_since(
    chat_jid: str, since_timestamp: str, bot_prefix: str
) -> list[NewMessage]:
    """Get messages since a timestamp, including bot responses for context."""
    db = _get_db()
    cursor = await db.execute(
        """
        SELECT id, chat_jid, sender, sender_name, content, timestamp
        FROM messages
        WHERE chat_jid = ? AND timestamp > ?
        ORDER BY timestamp
        """,
        (chat_jid, since_timestamp),
    )
    rows = await cursor.fetchall()
    return [
        NewMessage(
            id=r["id"],
            chat_jid=r["chat_jid"],
            sender=r["sender"],
            sender_name=r["sender_name"] or r["sender"],
            content=r["content"],
            timestamp=r["timestamp"],
        )
        for r in rows
    ]


async def get_recent_messages(
    chat_jid: str, limit: int = 20
) -> list[NewMessage]:
    """Get the most recent messages for conversation context."""
    db = _get_db()
    cursor = await db.execute(
        """
        SELECT id, chat_jid, sender, sender_name, content, timestamp
        FROM messages
        WHERE chat_jid = ?
        ORDER BY timestamp DESC
        LIMIT ?
        """,
        (chat_jid, limit),
    )
    rows = await cursor.fetchall()
    return [
        NewMessage(
            id=r["id"],
            chat_jid=r["chat_jid"],
            sender=r["sender"],
            sender_name=r["sender_name"] or r["sender"],
            content=r["content"],
            timestamp=r["timestamp"],
        )
        for r in reversed(rows)  # Return in chronological order
    ]


# --- Task Operations ---


async def create_task(task: ScheduledTask) -> None:
    """Create a new scheduled task."""
    db = _get_db()
    await db.execute(
        """
        INSERT INTO scheduled_tasks
            (id, group_folder, chat_jid, prompt, schedule_type, schedule_value,
             context_mode, next_run, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            task.id,
            task.group_folder,
            task.chat_jid,
            task.prompt,
            task.schedule_type,
            task.schedule_value,
            task.context_mode,
            task.next_run,
            task.status,
            task.created_at,
        ),
    )
    await db.commit()


async def get_task_by_id(task_id: str) -> ScheduledTask | None:
    """Get a task by ID."""
    db = _get_db()
    cursor = await db.execute(
        "SELECT * FROM scheduled_tasks WHERE id = ?", (task_id,)
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return ScheduledTask(**dict(row))


async def get_tasks_for_group(group_folder: str) -> list[ScheduledTask]:
    """Get all tasks for a group."""
    db = _get_db()
    cursor = await db.execute(
        "SELECT * FROM scheduled_tasks WHERE group_folder = ? ORDER BY created_at DESC",
        (group_folder,),
    )
    rows = await cursor.fetchall()
    return [ScheduledTask(**dict(r)) for r in rows]


async def get_all_tasks() -> list[ScheduledTask]:
    """Get all scheduled tasks."""
    db = _get_db()
    cursor = await db.execute(
        "SELECT * FROM scheduled_tasks ORDER BY created_at DESC"
    )
    rows = await cursor.fetchall()
    return [ScheduledTask(**dict(r)) for r in rows]


async def update_task(task_id: str, **updates: str | None) -> None:
    """Update fields of a scheduled task."""
    if not updates:
        return
    db = _get_db()
    fields = []
    values: list[str | None] = []
    for key, value in updates.items():
        fields.append(f"{key} = ?")
        values.append(value)
    values.append(task_id)
    await db.execute(
        f"UPDATE scheduled_tasks SET {', '.join(fields)} WHERE id = ?",
        tuple(values),
    )
    await db.commit()


async def delete_task(task_id: str) -> None:
    """Delete a task and its run logs."""
    db = _get_db()
    await db.execute("DELETE FROM task_run_logs WHERE task_id = ?", (task_id,))
    await db.execute("DELETE FROM scheduled_tasks WHERE id = ?", (task_id,))
    await db.commit()


async def get_due_tasks() -> list[ScheduledTask]:
    """Get all active tasks whose next_run is in the past."""
    db = _get_db()
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    cursor = await db.execute(
        """
        SELECT * FROM scheduled_tasks
        WHERE status = 'active' AND next_run IS NOT NULL AND next_run <= ?
        ORDER BY next_run
        """,
        (now,),
    )
    rows = await cursor.fetchall()
    return [ScheduledTask(**dict(r)) for r in rows]


async def update_task_after_run(
    task_id: str, next_run: str | None, last_result: str
) -> None:
    """Update a task after it has been run."""
    db = _get_db()
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        """
        UPDATE scheduled_tasks
        SET next_run = ?, last_run = ?, last_result = ?,
            status = CASE WHEN ? IS NULL THEN 'completed' ELSE status END
        WHERE id = ?
        """,
        (next_run, now, last_result, next_run, task_id),
    )
    await db.commit()


async def log_task_run(log: TaskRunLog) -> None:
    """Log a task run."""
    db = _get_db()
    await db.execute(
        """
        INSERT INTO task_run_logs (task_id, run_at, duration_ms, status, result, error)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (log.task_id, log.run_at, log.duration_ms, log.status, log.result, log.error),
    )
    await db.commit()


async def get_task_run_logs(task_id: str, limit: int = 10) -> list[TaskRunLog]:
    """Get recent run logs for a task."""
    db = _get_db()
    cursor = await db.execute(
        """
        SELECT task_id, run_at, duration_ms, status, result, error
        FROM task_run_logs
        WHERE task_id = ?
        ORDER BY run_at DESC
        LIMIT ?
        """,
        (task_id, limit),
    )
    rows = await cursor.fetchall()
    return [TaskRunLog(**dict(r)) for r in rows]
