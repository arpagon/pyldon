"""Owner pairing system for Pyldon.

Migrated from NanoClaw src/pairing.ts.

The first user to complete pairing becomes the owner.
Their room becomes the "main" admin channel.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from pyldon.config import DATA_DIR, MAIN_GROUP_FOLDER
from pyldon.models import Owner, PendingPairing
from pyldon.utils import load_json, save_json

PAIRING_CODE_LENGTH = 8
PAIRING_CODE_TTL_SECONDS = 10 * 60  # 10 minutes

# No ambiguous characters: 0O1I removed
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

OWNER_PATH = DATA_DIR / "owner.json"
PENDING_PATH = DATA_DIR / "pending_pairing.json"


def _generate_code() -> str:
    """Generate a random pairing code."""
    return "".join(secrets.choice(_ALPHABET) for _ in range(PAIRING_CODE_LENGTH))


def get_owner() -> Owner | None:
    """Get the current owner, or None if not paired."""
    data = load_json(OWNER_PATH, None)
    if data is None:
        return None
    return Owner(**data)


def is_owner(user_id: str) -> bool:
    """Check if a user is the owner."""
    owner = get_owner()
    return owner is not None and owner.owner_id == user_id


def is_paired() -> bool:
    """Check if Pyldon has been paired (has an owner)."""
    return get_owner() is not None


def get_main_room_id() -> str | None:
    """Get the main room ID (owner's room)."""
    owner = get_owner()
    return owner.main_room_id if owner else None


def create_pairing_request(user_id: str, room_id: str, room_name: str) -> str:
    """Create a pending pairing request. Returns the code to display to the user."""
    code = _generate_code()
    pending = PendingPairing(
        code=code,
        owner_id=user_id,
        room_id=room_id,
        room_name=room_name,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    save_json(PENDING_PATH, pending.model_dump())
    return code


def get_pending_pairing() -> PendingPairing | None:
    """Get the current pending pairing request, or None if none/expired."""
    data = load_json(PENDING_PATH, None)
    if data is None:
        return None

    pending = PendingPairing(**data)

    # Check if expired
    created_at = datetime.fromisoformat(pending.created_at)
    elapsed = (datetime.now(timezone.utc) - created_at).total_seconds()
    if elapsed > PAIRING_CODE_TTL_SECONDS:
        # Expired, clean up
        try:
            PENDING_PATH.unlink()
        except OSError:
            pass
        return None

    return pending


def approve_pairing(code: str) -> Owner | None:
    """Approve a pairing code. Returns Owner if successful, None if invalid/expired."""
    pending = get_pending_pairing()
    if pending is None:
        return None

    # Normalize code comparison
    if pending.code.upper() != code.upper():
        return None

    # Create owner
    owner = Owner(
        owner_id=pending.owner_id,
        main_room_id=pending.room_id,
        paired_at=datetime.now(timezone.utc).isoformat(),
    )
    save_json(OWNER_PATH, owner.model_dump())

    # Clean up pending
    try:
        PENDING_PATH.unlink()
    except OSError:
        pass

    return owner


def build_pairing_message(code: str) -> str:
    """Build the pairing message to send to the user."""
    ttl_minutes = PAIRING_CODE_TTL_SECONDS // 60
    return "\n".join([
        "**Pyldon is not configured**",
        "",
        f"Pairing code: `{code}`",
        "",
        "If you are the owner, run on the server terminal:",
        "```",
        f"uv run pyldon-pair {code}",
        "```",
        "",
        f"_The code expires in {ttl_minutes} minutes._",
    ])


def is_main_room(room_id: str) -> bool:
    """Check if a room is the main (admin) room."""
    owner = get_owner()
    return owner is not None and owner.main_room_id == room_id
