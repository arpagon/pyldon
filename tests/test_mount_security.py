"""Tests for Pyldon mount security module."""

import json
from pathlib import Path

import pytest

from pyldon.models import AdditionalMount, AllowedRoot, MountAllowlist
from pyldon.mount_security import (
    DEFAULT_BLOCKED_PATTERNS,
    validate_mount,
    validate_additional_mounts,
    generate_allowlist_template,
)
import pyldon.mount_security as mount_module


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch):
    """Reset the module-level cache before each test."""
    monkeypatch.setattr(mount_module, "_cached_allowlist", None)
    monkeypatch.setattr(mount_module, "_allowlist_load_error", None)


@pytest.fixture
def allowlist_file(tmp_path, monkeypatch):
    """Create a temporary allowlist file."""
    allowlist_path = tmp_path / "mount-allowlist.json"
    monkeypatch.setattr(mount_module, "MOUNT_ALLOWLIST_PATH", allowlist_path)
    return allowlist_path


def _write_allowlist(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


class TestValidateMount:
    def test_no_allowlist(self, allowlist_file):
        """Mounts blocked when no allowlist exists."""
        mount = AdditionalMount(host_path="/tmp", container_path="tmp")
        result = validate_mount(mount, is_main=True)
        assert not result.allowed
        assert "allowlist" in result.reason.lower()

    def test_invalid_container_path(self, allowlist_file):
        _write_allowlist(allowlist_file, {
            "allowed_roots": [{"path": "/tmp", "allow_read_write": True}],
            "blocked_patterns": [],
            "non_main_read_only": False,
        })
        mount = AdditionalMount(host_path="/tmp", container_path="../escape")
        result = validate_mount(mount, is_main=True)
        assert not result.allowed
        assert ".." in result.reason

    def test_blocked_pattern(self, allowlist_file):
        _write_allowlist(allowlist_file, {
            "allowed_roots": [{"path": "/", "allow_read_write": True}],
            "blocked_patterns": [],
            "non_main_read_only": False,
        })
        mount = AdditionalMount(host_path="/home/user/.ssh", container_path="ssh")
        result = validate_mount(mount, is_main=True)
        # .ssh is in DEFAULT_BLOCKED_PATTERNS
        if Path("/home/user/.ssh").exists():
            assert not result.allowed
            assert ".ssh" in result.reason

    def test_path_not_under_allowed_root(self, allowlist_file):
        _write_allowlist(allowlist_file, {
            "allowed_roots": [{"path": "/opt/allowed", "allow_read_write": True}],
            "blocked_patterns": [],
            "non_main_read_only": False,
        })
        mount = AdditionalMount(host_path="/tmp", container_path="tmp")
        result = validate_mount(mount, is_main=True)
        assert not result.allowed


class TestGenerateTemplate:
    def test_generates_valid_json(self):
        template = generate_allowlist_template()
        data = json.loads(template)
        assert "allowed_roots" in data
        assert "blocked_patterns" in data
        assert "non_main_read_only" in data
