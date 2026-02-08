"""Tests for container IPC MCP tools."""

import json
from pathlib import Path

import pytest

from container.agent_runner.ipc_mcp import create_ipc_mcp
import container.agent_runner.ipc_mcp as ipc_module


@pytest.fixture
def ipc_dir(tmp_path, monkeypatch):
    """Set up temp IPC directories."""
    monkeypatch.setattr(ipc_module, "IPC_DIR", tmp_path)
    monkeypatch.setattr(ipc_module, "MESSAGES_DIR", tmp_path / "messages")
    monkeypatch.setattr(ipc_module, "TASKS_DIR", tmp_path / "tasks")
    return tmp_path


class TestSendMessage:
    def test_writes_ipc_file(self, ipc_dir):
        tools = create_ipc_mcp(
            chat_jid="!room:test",
            group_folder="main",
            is_main=True,
        )
        result = tools["send_message"]("Hello world")
        assert "queued" in result.lower()

        # Check file was written
        msg_files = list((ipc_dir / "messages").glob("*.json"))
        assert len(msg_files) == 1

        data = json.loads(msg_files[0].read_text())
        assert data["type"] == "message"
        assert data["text"] == "Hello world"
        assert data["chatJid"] == "!room:test"


class TestScheduleTask:
    def test_valid_cron(self, ipc_dir):
        tools = create_ipc_mcp(
            chat_jid="!room:test",
            group_folder="main",
            is_main=True,
        )
        result = tools["schedule_task"](
            prompt="Do something",
            schedule_type="cron",
            schedule_value="0 9 * * *",
        )
        assert "scheduled" in result.lower()

    def test_invalid_cron(self, ipc_dir):
        tools = create_ipc_mcp(
            chat_jid="!room:test",
            group_folder="main",
            is_main=True,
        )
        result = tools["schedule_task"](
            prompt="Do something",
            schedule_type="cron",
            schedule_value="bad cron",
        )
        assert "invalid" in result.lower()

    def test_valid_interval(self, ipc_dir):
        tools = create_ipc_mcp(
            chat_jid="!room:test",
            group_folder="main",
            is_main=True,
        )
        result = tools["schedule_task"](
            prompt="Do something",
            schedule_type="interval",
            schedule_value="300000",
        )
        assert "scheduled" in result.lower()

    def test_invalid_interval(self, ipc_dir):
        tools = create_ipc_mcp(
            chat_jid="!room:test",
            group_folder="main",
            is_main=True,
        )
        result = tools["schedule_task"](
            prompt="Do something",
            schedule_type="interval",
            schedule_value="-1",
        )
        assert "invalid" in result.lower()


class TestListTasks:
    def test_no_tasks(self, ipc_dir):
        tools = create_ipc_mcp(
            chat_jid="!room:test",
            group_folder="main",
            is_main=True,
        )
        result = tools["list_tasks"]()
        assert "no scheduled tasks" in result.lower()

    def test_with_tasks(self, ipc_dir):
        # Write a tasks file
        tasks_file = ipc_dir / "current_tasks.json"
        tasks_file.write_text(json.dumps([
            {
                "id": "task-1",
                "groupFolder": "main",
                "prompt": "Test task",
                "schedule_type": "cron",
                "schedule_value": "0 9 * * *",
                "status": "active",
                "next_run": "2026-01-02T09:00:00Z",
            }
        ]))

        tools = create_ipc_mcp(
            chat_jid="!room:test",
            group_folder="main",
            is_main=True,
        )
        result = tools["list_tasks"]()
        assert "task-1" in result


class TestTaskActions:
    def test_pause_task(self, ipc_dir):
        tools = create_ipc_mcp(
            chat_jid="!room:test",
            group_folder="main",
            is_main=True,
        )
        result = tools["pause_task"]("task-1")
        assert "pause" in result.lower()

    def test_resume_task(self, ipc_dir):
        tools = create_ipc_mcp(
            chat_jid="!room:test",
            group_folder="main",
            is_main=True,
        )
        result = tools["resume_task"]("task-1")
        assert "resume" in result.lower()

    def test_cancel_task(self, ipc_dir):
        tools = create_ipc_mcp(
            chat_jid="!room:test",
            group_folder="main",
            is_main=True,
        )
        result = tools["cancel_task"]("task-1")
        assert "cancel" in result.lower()


class TestRegisterGroup:
    def test_main_can_register(self, ipc_dir):
        tools = create_ipc_mcp(
            chat_jid="!room:test",
            group_folder="main",
            is_main=True,
        )
        result = tools["register_group"](
            jid="!other:test",
            name="Other Room",
            folder="other-room",
            trigger="@Bot",
        )
        assert "registered" in result.lower()

    def test_non_main_cannot_register(self, ipc_dir):
        tools = create_ipc_mcp(
            chat_jid="!room:test",
            group_folder="other",
            is_main=False,
        )
        result = tools["register_group"](
            jid="!other:test",
            name="Other Room",
            folder="other-room",
            trigger="@Bot",
        )
        assert "only the main" in result.lower()
