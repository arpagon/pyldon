"""Tests for Pyldon database module."""

import pytest

from pyldon.db import (
    close_database,
    create_task,
    delete_task,
    get_all_chats,
    get_all_tasks,
    get_due_tasks,
    get_messages_since,
    get_task_by_id,
    init_database,
    log_task_run,
    store_chat_metadata,
    store_message,
    update_chat_name,
    update_task,
    update_task_after_run,
)
from pyldon.models import ScheduledTask, TaskRunLog

# Override STORE_DIR for tests
import pyldon.db as db_module


@pytest.fixture
async def db(tmp_path, monkeypatch):
    """Initialize an in-memory-like test database."""
    monkeypatch.setattr(db_module, "STORE_DIR", tmp_path)
    await init_database()
    yield
    await close_database()


class TestChatOperations:
    async def test_store_and_get_chats(self, db):
        await store_chat_metadata("!room1:test", "2026-01-01T00:00:00Z", "Room 1")
        await store_chat_metadata("!room2:test", "2026-01-02T00:00:00Z")

        chats = await get_all_chats()
        assert len(chats) == 2
        assert chats[0].jid == "!room2:test"  # Most recent first

    async def test_update_chat_name(self, db):
        await store_chat_metadata("!room:test", "2026-01-01T00:00:00Z", "Old Name")
        await update_chat_name("!room:test", "New Name")

        chats = await get_all_chats()
        assert chats[0].name == "New Name"


class TestMessageOperations:
    async def test_store_and_get_messages(self, db):
        await store_message(
            id="msg1",
            chat_id="!room:test",
            sender="@user:test",
            sender_name="User",
            content="Hello world",
            timestamp="2026-01-01T00:00:01Z",
            is_from_me=False,
        )
        await store_message(
            id="msg2",
            chat_id="!room:test",
            sender="@user:test",
            sender_name="User",
            content="Second message",
            timestamp="2026-01-01T00:00:02Z",
            is_from_me=False,
        )

        messages = await get_messages_since("!room:test", "2026-01-01T00:00:00Z", "Andy")
        assert len(messages) == 2
        assert messages[0].content == "Hello world"

    async def test_filters_bot_messages(self, db):
        await store_message(
            id="msg1",
            chat_id="!room:test",
            sender="@bot:test",
            sender_name="Bot",
            content="Andy: I am the bot",
            timestamp="2026-01-01T00:00:01Z",
            is_from_me=True,
        )
        await store_message(
            id="msg2",
            chat_id="!room:test",
            sender="@user:test",
            sender_name="User",
            content="Hello",
            timestamp="2026-01-01T00:00:02Z",
            is_from_me=False,
        )

        messages = await get_messages_since("!room:test", "2026-01-01T00:00:00Z", "Andy")
        assert len(messages) == 1
        assert messages[0].content == "Hello"


class TestTaskOperations:
    async def test_create_and_get_task(self, db):
        task = ScheduledTask(
            id="task-1",
            group_folder="main",
            chat_jid="!room:test",
            prompt="Do something",
            schedule_type="cron",
            schedule_value="0 9 * * *",
            next_run="2026-01-02T09:00:00Z",
            status="active",
            created_at="2026-01-01T00:00:00Z",
        )
        await create_task(task)

        retrieved = await get_task_by_id("task-1")
        assert retrieved is not None
        assert retrieved.prompt == "Do something"
        assert retrieved.schedule_type == "cron"

    async def test_get_all_tasks(self, db):
        for i in range(3):
            await create_task(ScheduledTask(
                id=f"task-{i}",
                group_folder="main",
                chat_jid="!room:test",
                prompt=f"Task {i}",
                schedule_type="once",
                schedule_value="2026-12-01T00:00:00Z",
                status="active",
                created_at=f"2026-01-0{i+1}T00:00:00Z",
            ))

        tasks = await get_all_tasks()
        assert len(tasks) == 3

    async def test_update_task(self, db):
        await create_task(ScheduledTask(
            id="task-1",
            group_folder="main",
            chat_jid="!room:test",
            prompt="Do something",
            schedule_type="cron",
            schedule_value="0 9 * * *",
            status="active",
            created_at="2026-01-01T00:00:00Z",
        ))

        await update_task("task-1", status="paused")
        task = await get_task_by_id("task-1")
        assert task.status == "paused"

    async def test_delete_task(self, db):
        await create_task(ScheduledTask(
            id="task-1",
            group_folder="main",
            chat_jid="!room:test",
            prompt="Do something",
            schedule_type="once",
            schedule_value="2026-12-01T00:00:00Z",
            status="active",
            created_at="2026-01-01T00:00:00Z",
        ))

        await delete_task("task-1")
        task = await get_task_by_id("task-1")
        assert task is None

    async def test_log_task_run(self, db):
        await create_task(ScheduledTask(
            id="task-1",
            group_folder="main",
            chat_jid="!room:test",
            prompt="Do something",
            schedule_type="once",
            schedule_value="2026-12-01T00:00:00Z",
            status="active",
            created_at="2026-01-01T00:00:00Z",
        ))

        await log_task_run(TaskRunLog(
            task_id="task-1",
            run_at="2026-01-01T00:01:00Z",
            duration_ms=5000,
            status="success",
            result="Done",
        ))

        # Task should still exist
        task = await get_task_by_id("task-1")
        assert task is not None
