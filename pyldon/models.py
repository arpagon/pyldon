"""Pydantic models for all Pyldon data types.

Migrated from NanoClaw src/types.ts and src/matrix-types.ts.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# --- Container & Group Models (from types.ts) ---


class AdditionalMount(BaseModel):
    """A host directory to mount into the agent container."""

    host_path: str
    """Absolute path on host (supports ~ for home)."""

    container_path: str
    """Path inside container (under /workspace/extra/)."""

    readonly: bool = True
    """Default: true for safety."""


class AllowedRoot(BaseModel):
    """An allowed root directory for additional mounts."""

    path: str
    """Absolute path or ~ for home (e.g., '~/projects', '/var/repos')."""

    allow_read_write: bool = False
    """Whether read-write mounts are allowed under this root."""

    description: str | None = None
    """Optional description for documentation."""


class MountAllowlist(BaseModel):
    """Security configuration for additional mounts.

    Stored at ~/.config/pyldon/mount-allowlist.json.
    NOT mounted into any container, making it tamper-proof from agents.
    """

    allowed_roots: list[AllowedRoot]
    """Directories that can be mounted into containers."""

    blocked_patterns: list[str]
    """Glob patterns for paths that should never be mounted."""

    non_main_read_only: bool = True
    """If true, non-main groups can only mount read-only regardless of config."""


class ContainerConfig(BaseModel):
    """Per-group container configuration."""

    additional_mounts: list[AdditionalMount] | None = None
    timeout: int | None = None  # Default: 300000 (5 minutes)
    env: dict[str, str] | None = None


class RegisteredGroup(BaseModel):
    """A registered Matrix room / group."""

    name: str
    folder: str
    trigger: str
    added_at: str
    container_config: ContainerConfig | None = None


class NewMessage(BaseModel):
    """A message from the database."""

    id: str
    chat_jid: str
    sender: str
    sender_name: str
    content: str
    timestamp: str


class ScheduledTask(BaseModel):
    """A scheduled task stored in the database."""

    id: str
    group_folder: str
    chat_jid: str
    prompt: str
    schedule_type: Literal["cron", "interval", "once"]
    schedule_value: str
    context_mode: Literal["group", "isolated"] = "isolated"
    next_run: str | None = None
    last_run: str | None = None
    last_result: str | None = None
    status: Literal["active", "paused", "completed"] = "active"
    created_at: str = ""


class TaskRunLog(BaseModel):
    """Log entry for a task run."""

    task_id: str
    run_at: str
    duration_ms: int
    status: Literal["success", "error"]
    result: str | None = None
    error: str | None = None


# --- Matrix Models (from matrix-types.ts) ---


class MatrixConfig(BaseModel):
    """Matrix connection configuration."""

    homeserver: str
    user_id: str
    access_token: str
    password: str | None = None
    """Password for login-based auth (needed for E2EE device key generation)."""

    device_id: str | None = None
    """Device ID for this bot instance."""

    recovery_key: str | None = None
    """E2EE recovery key for cross-signing / key backup."""

    encryption: bool = False
    """Enable end-to-end encryption (requires additional setup)."""

    rooms: dict[str, MatrixRoomConfig] | None = None
    """Rooms/DMs the bot should respond in. Key is room ID or alias."""

    require_mention: bool = True
    """Default: require @mention to trigger in rooms."""


class MatrixRoomConfig(BaseModel):
    """Per-room configuration."""

    enabled: bool = True
    """If false, ignore this room."""

    require_mention: bool | None = None
    """Override global require_mention for this room."""

    folder: str | None = None
    """Folder name for this room's isolated context."""

    trigger_pattern: str | None = None
    """Custom trigger pattern (regex)."""


class MatrixMessage(BaseModel):
    """An incoming Matrix message."""

    room_id: str
    event_id: str
    sender: str
    sender_name: str
    content: str
    timestamp: str
    thread_id: str | None = None
    reply_to_id: str | None = None
    images: list[dict[str, str]] = Field(default_factory=list)
    """List of images: [{"data": "base64...", "mimeType": "image/png"}]"""


# --- Container I/O Models ---


class ContainerInput(BaseModel):
    """Input sent to the agent container via stdin."""

    model_config = {"populate_by_name": True}

    prompt: str
    session_id: str | None = Field(default=None, serialization_alias="sessionId")
    group_folder: str = Field(serialization_alias="groupFolder")
    chat_jid: str = Field(serialization_alias="chatJid")
    is_main: bool = Field(serialization_alias="isMain")
    is_scheduled_task: bool = Field(default=False, serialization_alias="isScheduledTask")
    images: list[dict[str, str]] = Field(default_factory=list, serialization_alias="images")
    """Images as [{"data": "base64...", "mimeType": "image/png"}]"""


class ContainerOutput(BaseModel):
    """Output received from the agent container via stdout."""

    status: Literal["success", "error"]
    result: str | None = None
    new_session_id: str | None = None
    error: str | None = None


# --- IPC Models ---


class ChatInfo(BaseModel):
    """Chat metadata from the database."""

    jid: str
    name: str
    last_message_time: str


class Owner(BaseModel):
    """The paired owner information."""

    owner_id: str
    """@user:matrix.org"""

    main_room_id: str
    """!room:matrix.org"""

    paired_at: str
    """ISO timestamp."""


class PendingPairing(BaseModel):
    """A pending pairing request."""

    code: str
    owner_id: str
    room_id: str
    room_name: str
    created_at: str


class AvailableGroup(BaseModel):
    """A Matrix room available for group registration."""

    jid: str
    name: str
    last_activity: str
    is_registered: bool


class MountValidationResult(BaseModel):
    """Result of validating a mount against the allowlist."""

    allowed: bool
    reason: str
    real_host_path: str | None = None
    effective_readonly: bool | None = None
