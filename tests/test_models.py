"""Tests for Pyldon models."""

from pyldon.models import (
    AdditionalMount,
    AllowedRoot,
    AvailableGroup,
    ChatInfo,
    ContainerConfig,
    ContainerInput,
    ContainerOutput,
    MatrixConfig,
    MatrixMessage,
    MatrixRoomConfig,
    MountAllowlist,
    MountValidationResult,
    NewMessage,
    Owner,
    PendingPairing,
    RegisteredGroup,
    ScheduledTask,
    TaskRunLog,
)


class TestAdditionalMount:
    def test_defaults(self):
        m = AdditionalMount(host_path="/home/user/projects", container_path="projects")
        assert m.readonly is True

    def test_explicit_readonly(self):
        m = AdditionalMount(host_path="/tmp", container_path="tmp", readonly=False)
        assert m.readonly is False


class TestContainerConfig:
    def test_defaults(self):
        c = ContainerConfig()
        assert c.additional_mounts is None
        assert c.timeout is None
        assert c.env is None

    def test_with_mounts(self):
        c = ContainerConfig(
            additional_mounts=[
                AdditionalMount(host_path="/tmp", container_path="tmp")
            ],
            timeout=60000,
        )
        assert len(c.additional_mounts) == 1
        assert c.timeout == 60000


class TestRegisteredGroup:
    def test_basic(self):
        g = RegisteredGroup(
            name="main",
            folder="main",
            trigger="@Andy",
            added_at="2026-01-01T00:00:00Z",
        )
        assert g.name == "main"
        assert g.container_config is None


class TestScheduledTask:
    def test_defaults(self):
        t = ScheduledTask(
            id="task-1",
            group_folder="main",
            chat_jid="!room:matrix.org",
            prompt="hello",
            schedule_type="cron",
            schedule_value="0 9 * * *",
        )
        assert t.status == "active"
        assert t.context_mode == "isolated"
        assert t.next_run is None


class TestMatrixConfig:
    def test_basic(self):
        c = MatrixConfig(
            homeserver="https://matrix.org",
            user_id="@bot:matrix.org",
            access_token="token123",
        )
        assert c.encryption is False
        assert c.require_mention is True

    def test_with_rooms(self):
        c = MatrixConfig(
            homeserver="https://matrix.org",
            user_id="@bot:matrix.org",
            access_token="token123",
            rooms={
                "!room:matrix.org": MatrixRoomConfig(
                    enabled=True,
                    require_mention=False,
                    folder="my-room",
                )
            },
        )
        assert "!room:matrix.org" in c.rooms
        assert c.rooms["!room:matrix.org"].folder == "my-room"


class TestMatrixMessage:
    def test_basic(self):
        m = MatrixMessage(
            room_id="!room:matrix.org",
            event_id="$event1",
            sender="@user:matrix.org",
            sender_name="User",
            content="Hello",
            timestamp="2026-01-01T00:00:00Z",
        )
        assert m.thread_id is None
        assert m.reply_to_id is None


class TestContainerInput:
    def test_basic(self):
        i = ContainerInput(
            prompt="hello",
            group_folder="main",
            chat_jid="!room:matrix.org",
            is_main=True,
        )
        assert i.session_id is None
        assert i.is_scheduled_task is False


class TestContainerOutput:
    def test_success(self):
        o = ContainerOutput(status="success", result="hello")
        assert o.error is None

    def test_error(self):
        o = ContainerOutput(status="error", error="failed")
        assert o.result is None


class TestOwner:
    def test_basic(self):
        o = Owner(
            owner_id="@user:matrix.org",
            main_room_id="!room:matrix.org",
            paired_at="2026-01-01T00:00:00Z",
        )
        assert o.owner_id == "@user:matrix.org"


class TestMountAllowlist:
    def test_basic(self):
        a = MountAllowlist(
            allowed_roots=[
                AllowedRoot(path="~/projects", allow_read_write=True)
            ],
            blocked_patterns=[".ssh"],
            non_main_read_only=True,
        )
        assert len(a.allowed_roots) == 1
        assert a.non_main_read_only is True
