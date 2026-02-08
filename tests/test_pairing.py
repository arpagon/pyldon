"""Tests for Pyldon pairing module."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pyldon.models import Owner, PendingPairing
from pyldon.pairing import (
    approve_pairing,
    build_pairing_message,
    create_pairing_request,
    get_main_room_id,
    get_owner,
    get_pending_pairing,
    is_main_room,
    is_owner,
    is_paired,
)
import pyldon.pairing as pairing_module


@pytest.fixture(autouse=True)
def _isolated_data(tmp_path, monkeypatch):
    """Use temp directory for all pairing data."""
    monkeypatch.setattr(pairing_module, "OWNER_PATH", tmp_path / "owner.json")
    monkeypatch.setattr(pairing_module, "PENDING_PATH", tmp_path / "pending_pairing.json")


def test_not_paired_initially():
    assert not is_paired()
    assert get_owner() is None
    assert get_main_room_id() is None


def test_create_pairing_request():
    code = create_pairing_request("@user:matrix.org", "!room:matrix.org", "User")
    assert len(code) == 8
    assert code.isalnum()

    pending = get_pending_pairing()
    assert pending is not None
    assert pending.owner_id == "@user:matrix.org"
    assert pending.room_id == "!room:matrix.org"


def test_approve_pairing():
    code = create_pairing_request("@user:matrix.org", "!room:matrix.org", "User")
    owner = approve_pairing(code)

    assert owner is not None
    assert owner.owner_id == "@user:matrix.org"
    assert owner.main_room_id == "!room:matrix.org"
    assert is_paired()
    assert is_owner("@user:matrix.org")
    assert not is_owner("@other:matrix.org")


def test_approve_wrong_code():
    create_pairing_request("@user:matrix.org", "!room:matrix.org", "User")
    owner = approve_pairing("WRONGCODE")
    assert owner is None
    assert not is_paired()


def test_approve_case_insensitive():
    code = create_pairing_request("@user:matrix.org", "!room:matrix.org", "User")
    owner = approve_pairing(code.lower())
    assert owner is not None


def test_is_main_room():
    code = create_pairing_request("@user:matrix.org", "!room:matrix.org", "User")
    approve_pairing(code)

    assert is_main_room("!room:matrix.org")
    assert not is_main_room("!other:matrix.org")


def test_build_pairing_message():
    msg = build_pairing_message("ABCD1234")
    assert "ABCD1234" in msg
    assert "pyldon-pair" in msg


def test_expired_pairing(monkeypatch):
    create_pairing_request("@user:matrix.org", "!room:matrix.org", "User")

    # Make it look expired
    monkeypatch.setattr(pairing_module, "PAIRING_CODE_TTL_SECONDS", -1)
    pending = get_pending_pairing()
    assert pending is None
