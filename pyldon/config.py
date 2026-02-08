"""Configuration constants for Pyldon.

Migrated from NanoClaw src/config.ts.
"""

from __future__ import annotations

import os
import re
from pathlib import Path


def _escape_regex(s: str) -> str:
    """Escape special regex characters."""
    return re.escape(s)


ASSISTANT_NAME: str = os.environ.get("ASSISTANT_NAME", "Andy")
POLL_INTERVAL: int = 2000  # ms
SCHEDULER_POLL_INTERVAL: int = 60  # seconds (was ms in TS, using seconds for asyncio)
IPC_POLL_INTERVAL: int = 1  # seconds

# Absolute paths needed for container mounts
PROJECT_ROOT: Path = Path.cwd()
HOME_DIR: Path = Path(os.environ.get("HOME", str(Path.home())))

# Mount security: allowlist stored OUTSIDE project root, never mounted into containers
MOUNT_ALLOWLIST_PATH: Path = HOME_DIR / ".config" / "pyldon" / "mount-allowlist.json"
STORE_DIR: Path = PROJECT_ROOT / "store"
GROUPS_DIR: Path = PROJECT_ROOT / "groups"
DATA_DIR: Path = PROJECT_ROOT / "data"
MAIN_GROUP_FOLDER: str = "main"

CONTAINER_IMAGE: str = os.environ.get("CONTAINER_IMAGE", "pyldon-agent:latest")
CONTAINER_TIMEOUT: int = int(os.environ.get("CONTAINER_TIMEOUT", "300000"))
CONTAINER_MAX_OUTPUT_SIZE: int = int(
    os.environ.get("CONTAINER_MAX_OUTPUT_SIZE", "10485760")
)  # 10MB default

TRIGGER_PATTERN: re.Pattern[str] = re.compile(
    rf"^@{_escape_regex(ASSISTANT_NAME)}\b", re.IGNORECASE
)

# Timezone for scheduled tasks (cron expressions, etc.)
TIMEZONE: str = os.environ.get("TZ", "UTC")

# Room metadata sync interval (24 hours)
ROOM_SYNC_INTERVAL_MS: int = 24 * 60 * 60 * 1000
