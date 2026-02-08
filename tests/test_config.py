"""Tests for Pyldon config module."""

import re

from pyldon.config import (
    ASSISTANT_NAME,
    CONTAINER_IMAGE,
    CONTAINER_MAX_OUTPUT_SIZE,
    CONTAINER_TIMEOUT,
    DATA_DIR,
    GROUPS_DIR,
    IPC_POLL_INTERVAL,
    MAIN_GROUP_FOLDER,
    MOUNT_ALLOWLIST_PATH,
    POLL_INTERVAL,
    PROJECT_ROOT,
    SCHEDULER_POLL_INTERVAL,
    STORE_DIR,
    TRIGGER_PATTERN,
)


def test_defaults():
    assert ASSISTANT_NAME == "Andy"
    assert POLL_INTERVAL == 2000
    assert MAIN_GROUP_FOLDER == "main"
    assert CONTAINER_TIMEOUT == 300000
    assert CONTAINER_MAX_OUTPUT_SIZE == 10485760


def test_trigger_pattern():
    assert isinstance(TRIGGER_PATTERN, re.Pattern)
    assert TRIGGER_PATTERN.match("@Andy hello")
    assert TRIGGER_PATTERN.match("@andy test")
    assert not TRIGGER_PATTERN.match("Hello @Andy")
    assert not TRIGGER_PATTERN.match("Just a message")


def test_paths_are_pathlib():
    from pathlib import Path

    assert isinstance(PROJECT_ROOT, Path)
    assert isinstance(STORE_DIR, Path)
    assert isinstance(GROUPS_DIR, Path)
    assert isinstance(DATA_DIR, Path)
    assert isinstance(MOUNT_ALLOWLIST_PATH, Path)
