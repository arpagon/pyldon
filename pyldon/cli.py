"""Pairing CLI for Pyldon.

Migrated from NanoClaw src/pair.ts.
Usage: uv run pyldon-pair <CODE>
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from pyldon.config import ASSISTANT_NAME, DATA_DIR, GROUPS_DIR, MAIN_GROUP_FOLDER
from pyldon.models import RegisteredGroup
from pyldon.pairing import approve_pairing, get_owner, get_pending_pairing
from pyldon.utils import load_json, save_json


def main() -> None:
    """CLI entry point for pairing."""
    code = sys.argv[1] if len(sys.argv) > 1 else None

    if not code:
        print("Usage: uv run pyldon-pair <CODE>", file=sys.stderr)
        print("", file=sys.stderr)

        pending = get_pending_pairing()
        if pending:
            print("Pending pairing request:", file=sys.stderr)
            print(f"  Code: {pending.code}", file=sys.stderr)
            print(f"  User: {pending.owner_id}", file=sys.stderr)
            print(f"  Room: {pending.room_name}", file=sys.stderr)
            print(f"  Created: {pending.created_at}", file=sys.stderr)
        else:
            owner = get_owner()
            if owner:
                print("Pyldon is already paired:", file=sys.stderr)
                print(f"  Owner: {owner.owner_id}", file=sys.stderr)
                print(f"  Main Room: {owner.main_room_id}", file=sys.stderr)
                print(f"  Paired: {owner.paired_at}", file=sys.stderr)
            else:
                print(
                    "No pending pairing request. Send a message to the bot first.",
                    file=sys.stderr,
                )
        sys.exit(1)

    # Check if already paired
    existing_owner = get_owner()
    if existing_owner:
        print("Pyldon is already paired!", file=sys.stderr)
        print(f"  Owner: {existing_owner.owner_id}", file=sys.stderr)
        print(f"  Main Room: {existing_owner.main_room_id}", file=sys.stderr)
        print(f"  Paired: {existing_owner.paired_at}", file=sys.stderr)
        print("", file=sys.stderr)
        print(
            "To reset, delete data/owner.json and restart Pyldon.", file=sys.stderr
        )
        sys.exit(1)

    # Try to approve
    owner = approve_pairing(code)

    if not owner:
        print("Invalid or expired pairing code.", file=sys.stderr)

        pending = get_pending_pairing()
        if pending:
            print(f"Current valid code: {pending.code}", file=sys.stderr)
        else:
            print(
                "No pending pairing request. Send a message to the bot first.",
                file=sys.stderr,
            )
        sys.exit(1)

    # Register the main group
    groups_path = DATA_DIR / "registered_groups.json"
    groups: dict[str, dict] = load_json(groups_path, {})

    groups[owner.main_room_id] = RegisteredGroup(
        name="main",
        folder=MAIN_GROUP_FOLDER,
        trigger=f"@{ASSISTANT_NAME}",
        added_at=datetime.now(timezone.utc).isoformat(),
    ).model_dump()

    save_json(groups_path, groups)

    # Create the main group folder if it doesn't exist
    main_group_dir = GROUPS_DIR / MAIN_GROUP_FOLDER
    (main_group_dir / "logs").mkdir(parents=True, exist_ok=True)

    print("Pairing successful!")
    print("")
    print(f"  Owner: {owner.owner_id}")
    print(f"  Main Room: {owner.main_room_id}")
    print(f"  Folder: groups/{MAIN_GROUP_FOLDER}/")
    print("")
    print("Restart Pyldon to apply changes:")
    print("  systemctl --user restart pyldon")


if __name__ == "__main__":
    main()
