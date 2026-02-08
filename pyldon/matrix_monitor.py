"""Matrix monitor module for Pyldon.

Migrated from NanoClaw src/matrix-monitor.ts.
Handles incoming Matrix events and routes them to the message handler.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger
from nio import (
    AsyncClient,
    InviteMemberEvent,
    RoomMemberEvent,
    RoomMessageText,
)

from pyldon.config import ASSISTANT_NAME, TRIGGER_PATTERN
from pyldon.matrix_client import get_matrix_client, get_matrix_config
from pyldon.models import MatrixMessage, MatrixRoomConfig
from pyldon.pairing import is_main_room, is_paired

MessageHandler = Callable[[MatrixMessage, MatrixRoomConfig | None, bool], Awaitable[None]]


def _build_mention_pattern(user_id: str) -> re.Pattern[str]:
    """Build a regex to match mentions of the bot."""
    localpart = user_id.split(":")[0].lstrip("@")
    escaped_user_id = re.escape(user_id)
    return re.compile(
        rf"(@{re.escape(ASSISTANT_NAME)}|@{re.escape(localpart)}|{escaped_user_id})",
        re.IGNORECASE,
    )


def start_matrix_monitor(on_message: MessageHandler) -> None:
    """Register event callbacks on the Matrix client.

    The client must be initialized before calling this.
    Event handling happens when the client syncs.
    """
    client = get_matrix_client()
    config = get_matrix_config()
    mention_pattern = _build_mention_pattern(config.user_id)

    async def _on_room_message(room: Any, event: RoomMessageText) -> None:
        """Handle incoming room messages."""
        room_id = room.room_id

        logger.debug("Received room.message: room={}, sender={}", room_id, event.sender)

        # Ignore own messages
        if event.sender == config.user_id:
            return

        text = (event.body or "").strip()
        if not text:
            return

        # Get room config
        room_config = None
        if config.rooms and room_id in config.rooms:
            room_config = config.rooms[room_id]
            if room_config.enabled is False:
                return

        # Determine if this is the main room
        is_main = is_paired() and is_main_room(room_id)

        # Check if this is a DM (direct message) - DMs don't require mention
        is_dm = False
        try:
            joined = room.member_count
            is_dm = joined <= 2
        except Exception:
            pass

        # Check if we should respond
        require_mention = (
            False if is_dm
            else (
                room_config.require_mention
                if room_config and room_config.require_mention is not None
                else config.require_mention if not is_main else False
            )
        )

        logger.debug(
            "Message check: room={}, text={}, is_dm={}, require_mention={}, is_main={}",
            room_id, text[:50], is_dm, require_mention, is_main,
        )

        if require_mention and not mention_pattern.search(text) and not TRIGGER_PATTERN.search(text):
            logger.debug("Message ignored - no trigger/mention: room={}", room_id)
            return

        # Get sender display name
        sender_name = event.sender
        try:
            # matrix-nio stores display names in the room member list
            member = room.users.get(event.sender)
            if member and member.display_name:
                sender_name = member.display_name
        except Exception:
            pass

        # Extract thread info
        thread_id = None
        relates_to = getattr(event.source, "get", lambda *_: None)
        if isinstance(event.source, dict):
            content = event.source.get("content", {})
            rel = content.get("m.relates_to", {})
            if rel.get("rel_type") == "m.thread":
                thread_id = rel.get("event_id")

        # Build timestamp from event
        from datetime import datetime, timezone

        ts = getattr(event, "server_timestamp", None) or 0
        timestamp = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat()

        message = MatrixMessage(
            room_id=room_id,
            event_id=event.event_id,
            sender=event.sender,
            sender_name=sender_name,
            content=text,
            timestamp=timestamp,
            thread_id=thread_id,
        )

        logger.info("Processing Matrix message: room={}, sender={}", room_id, sender_name)

        try:
            await on_message(message, room_config, is_main)
        except Exception as e:
            logger.error(
                "Error handling Matrix message: room={}, event_id={}, error={}",
                room_id, event.event_id, e,
            )

    async def _on_invite(room: Any, event: InviteMemberEvent) -> None:
        """Handle room invites - auto-join."""
        logger.info("Received room invite: room={}, inviter={}", room.room_id, event.sender)
        try:
            await client.join(room.room_id)
            logger.info("Auto-joined room: {}", room.room_id)
        except Exception as e:
            logger.error("Failed to join room {}: {}", room.room_id, e)

    # Register callbacks
    client.add_event_callback(_on_room_message, RoomMessageText)
    client.add_event_callback(_on_invite, InviteMemberEvent)

    logger.info("Matrix monitor started")
