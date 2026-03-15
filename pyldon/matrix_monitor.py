"""Matrix monitor module for Pyldon.

Migrated from NanoClaw src/matrix-monitor.ts.
Handles incoming Matrix events and routes them to the message handler.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from loguru import logger
from nio import (
    AsyncClient,
    InviteMemberEvent,
    MegolmEvent,
    RoomEncryptedAudio,
    RoomEncryptedImage,
    RoomMemberEvent,
    RoomMessageAudio,
    RoomMessageImage,
    RoomMessageText,
)

from pyldon.config import ASSISTANT_NAME, TRIGGER_PATTERN
from pyldon.matrix_client import get_matrix_client, get_matrix_config
from pyldon.models import MatrixMessage, MatrixRoomConfig
from pyldon.pairing import is_main_room, is_paired


async def _download_media(client: AsyncClient, event: Any) -> bytes | None:
    """Download media from Matrix, handling both encrypted and unencrypted."""
    try:
        # Encrypted media (E2EE rooms)
        if hasattr(event, "key") and event.key:
            from nio.crypto import decrypt_attachment

            response = await client.download(event.url)
            if not hasattr(response, "body"):
                logger.error("Failed to download encrypted media: {}", response)
                return None

            key = event.key.get("k", "")
            hash_val = event.hashes.get("sha256", "")
            iv = event.iv

            return decrypt_attachment(response.body, key, hash_val, iv)

        # Unencrypted media
        response = await client.download(event.url)
        if hasattr(response, "body"):
            return response.body

        logger.error("Failed to download media: {}", response)
        return None
    except Exception as e:
        logger.error("Error downloading media: {}", e)
        return None

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
            # Also check formatted_body (HTML) — Matrix mentions appear there
            # as <a href="https://matrix.to/#/@user:server">Name</a>
            formatted_body = ""
            if isinstance(event.source, dict):
                formatted_body = event.source.get("content", {}).get("formatted_body", "")
            if not formatted_body or not mention_pattern.search(formatted_body):
                # No mention — but if group has observe_all_messages, store it silently
                from pyldon.main import _get_group_for_room
                group_info = _get_group_for_room(room_id)
                if group_info and group_info.observe_all_messages:
                    from pyldon.db import store_group_history, store_message, store_chat_metadata
                    from datetime import datetime, timezone

                    sender_name = event.sender
                    try:
                        member = room.users.get(event.sender)
                        if member and member.display_name:
                            sender_name = member.display_name
                    except Exception:
                        pass

                    ts = getattr(event, "server_timestamp", None) or 0
                    timestamp = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat()

                    await store_message(
                        id=event.event_id,
                        chat_id=room_id,
                        sender=event.sender,
                        sender_name=sender_name,
                        content=text,
                        timestamp=timestamp,
                        is_from_me=False,
                    )
                    await store_group_history(
                        group_info.folder,
                        id=event.event_id,
                        sender=event.sender,
                        sender_name=sender_name,
                        content=text,
                        timestamp=timestamp,
                        is_from_me=False,
                    )
                    await store_chat_metadata(room_id, timestamp, sender_name)
                    logger.debug("Message observed (no mention): room={}, sender={}", room_id, sender_name)
                else:
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

    async def _on_megolm_event(room: Any, event: MegolmEvent) -> None:
        """Handle undecryptable encrypted messages."""
        logger.warning(
            "Undecryptable message: room={}, sender={}, device_id={}, session_id={}",
            room.room_id,
            event.sender,
            event.device_id,
            event.session_id,
        )

    async def _on_audio_message(room: Any, event: RoomMessageAudio) -> None:
        """Handle incoming audio/voice messages — transcribe and route as text."""
        from pyldon.config import STT_ENABLED

        room_id = room.room_id
        logger.debug("Received audio message: room={}, sender={}", room_id, event.sender)

        if event.sender == config.user_id:
            return

        if not STT_ENABLED:
            logger.debug("STT disabled, ignoring audio message")
            return

        # Get room config
        room_config = None
        if config.rooms and room_id in config.rooms:
            room_config = config.rooms[room_id]
            if room_config.enabled is False:
                return

        is_main = is_paired() and is_main_room(room_id)

        # DMs don't require mention — voice messages in DM always process
        is_dm = False
        try:
            is_dm = room.member_count <= 2
        except Exception:
            pass

        if not is_dm:
            # For group chats, only process if mention is not required or main room
            # Exception: groups with always_process_audio bypass mention check
            from pyldon.main import _get_group_for_room
            group_info = _get_group_for_room(room_id)
            skip_mention = group_info and group_info.always_process_audio

            if not skip_mention:
                require_mention = (
                    room_config.require_mention
                    if room_config and room_config.require_mention is not None
                    else config.require_mention if not is_main else False
                )
                if require_mention:
                    logger.debug("Audio in group chat requires mention, skipping: room={}", room_id)
                    return

        # Download audio from Matrix (handles encrypted + unencrypted)
        audio_data = await _download_media(client, event)
        if not audio_data:
            logger.error("Failed to download audio: room={}", room_id)
            return

        # Transcribe
        from pyldon.stt import transcribe_audio

        filename = event.body or "voice_message.ogg"
        text = await transcribe_audio(audio_data, filename)

        if not text:
            logger.warning("Audio transcription failed or empty: room={}", room_id)
            return

        # Build message with transcription
        transcribed_content = f"[🎤 Voz]: {text}"

        # Save raw audio to group workspace if group has save_audio enabled
        try:
            from pyldon.main import _get_group_for_room
            group_info = _get_group_for_room(room_id)
            if group_info and group_info.save_audio:
                from pyldon.config import GROUPS_DIR
                audio_dir = GROUPS_DIR / group_info.folder / "audio"
                audio_dir.mkdir(parents=True, exist_ok=True)
                ext = Path(filename).suffix or ".ogg"
                audio_filename = f"{event.event_id.replace('$', '').replace(':', '_')}{ext}"
                audio_save_path = audio_dir / audio_filename
                audio_save_path.write_bytes(audio_data)
                logger.info("Audio saved: {} ({} bytes)", audio_save_path, len(audio_data))
                transcribed_content = f"[🎤 Voz (audio:{audio_filename})]: {text}"
        except Exception as e:
            logger.debug("Could not save raw audio: {}", e)

        sender_name = event.sender
        try:
            member = room.users.get(event.sender)
            if member and member.display_name:
                sender_name = member.display_name
        except Exception:
            pass

        thread_id = None
        if isinstance(event.source, dict):
            content = event.source.get("content", {})
            rel = content.get("m.relates_to", {})
            if rel.get("rel_type") == "m.thread":
                thread_id = rel.get("event_id")

        from datetime import datetime, timezone

        ts = getattr(event, "server_timestamp", None) or 0
        timestamp = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat()

        message = MatrixMessage(
            room_id=room_id,
            event_id=event.event_id,
            sender=event.sender,
            sender_name=sender_name,
            content=transcribed_content,
            timestamp=timestamp,
            thread_id=thread_id,
        )

        logger.info(
            "Audio transcribed: room={}, sender={}, text={}",
            room_id, sender_name, text[:80],
        )

        try:
            await on_message(message, room_config, is_main)
        except Exception as e:
            logger.error("Error handling transcribed audio: room={}, error={}", room_id, e)

    async def _on_image_message(room: Any, event: RoomMessageImage) -> None:
        """Handle incoming image messages — download, resize, save to workspace, pass to agent."""

        room_id = room.room_id
        logger.debug("Received image message: room={}, sender={}", room_id, event.sender)

        if event.sender == config.user_id:
            return

        # Get room config
        room_config = None
        if config.rooms and room_id in config.rooms:
            room_config = config.rooms[room_id]
            if room_config.enabled is False:
                return

        is_main = is_paired() and is_main_room(room_id)

        # DM check
        is_dm = False
        try:
            is_dm = room.member_count <= 2
        except Exception:
            pass

        if not is_dm:
            # Check if group bypasses mention requirement (like audio does)
            from pyldon.main import _get_group_for_room
            group_info = _get_group_for_room(room_id)
            skip_mention = group_info and (group_info.observe_all_messages or group_info.always_process_audio)

            if not skip_mention:
                require_mention = (
                    room_config.require_mention
                    if room_config and room_config.require_mention is not None
                    else config.require_mention if not is_main else False
                )
                if require_mention:
                    logger.debug("Image in group chat requires mention, skipping: room={}", room_id)
                    return

        # Download image from Matrix (handles encrypted + unencrypted)
        image_data = await _download_media(client, event)
        if not image_data:
            logger.error("Failed to download image: room={}", room_id)
            return

        # Determine mime type
        filename = event.body or "image.png"
        mime_type = getattr(event, "mimetype", None) or "image/png"
        if mime_type == "image/png":
            # Fallback to filename extension
            if filename.lower().endswith((".jpg", ".jpeg")):
                mime_type = "image/jpeg"
            elif filename.lower().endswith(".gif"):
                mime_type = "image/gif"
            elif filename.lower().endswith(".webp"):
                mime_type = "image/webp"
        # Also check content info from Matrix
        if isinstance(event.source, dict):
            info = event.source.get("content", {}).get("info", {})
            if info.get("mimetype"):
                mime_type = info["mimetype"]

        # Resize and save image to group workspace
        from pathlib import Path
        from PIL import Image
        import io
        import uuid

        MAX_DIMENSION = 1500  # px — enough for LLM vision, saves bandwidth

        try:
            img = Image.open(io.BytesIO(image_data))
            # Resize if too large
            if max(img.size) > MAX_DIMENSION:
                img.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.LANCZOS)
            # Save as JPEG (smaller than PNG for photos)
            buf = io.BytesIO()
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            img.save(buf, format="JPEG", quality=82)
            saved_bytes = buf.getvalue()
            mime_type = "image/jpeg"
        except Exception as e:
            logger.warning("Image resize failed, using original: {}", e)
            saved_bytes = image_data

        # Write to group images directory (like audio/ for voice messages)
        from pyldon.config import GROUPS_DIR
        from pyldon.main import _get_group_for_room
        group_reg = _get_group_for_room(room_id)
        group_folder = group_reg.folder if group_reg else (room_config.folder if room_config else "unknown")
        images_dir = GROUPS_DIR / group_folder / "images"
        images_dir.mkdir(parents=True, exist_ok=True)
        img_filename = f"{event.event_id.replace('$', '').replace(':', '_')}.jpg"
        img_path = images_dir / img_filename
        img_path.write_bytes(saved_bytes)

        # Container sees this as /workspace/group/images/<file>
        container_path = f"/workspace/group/images/{img_filename}"

        logger.info(
            "Image saved: room={}, sender={}, original={}KB, saved={}KB, path={}",
            room_id, event.sender, len(image_data) // 1024, len(saved_bytes) // 1024, img_path,
        )

        # Build message with image reference (like audio does with [🎤 Voz (audio:file)])
        caption = event.body if event.body and not event.body.startswith("image") else ""
        text_content = f"[🖼️ Imagen (image:{img_filename})]: {caption}" if caption else f"[🖼️ Imagen (image:{img_filename})]"

        sender_name = event.sender
        try:
            member = room.users.get(event.sender)
            if member and member.display_name:
                sender_name = member.display_name
        except Exception:
            pass

        thread_id = None
        if isinstance(event.source, dict):
            content = event.source.get("content", {})
            rel = content.get("m.relates_to", {})
            if rel.get("rel_type") == "m.thread":
                thread_id = rel.get("event_id")

        from datetime import datetime, timezone

        ts = getattr(event, "server_timestamp", None) or 0
        timestamp = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat()

        message = MatrixMessage(
            room_id=room_id,
            event_id=event.event_id,
            sender=event.sender,
            sender_name=sender_name,
            content=text_content,
            timestamp=timestamp,
            thread_id=thread_id,
            images=[{"path": str(img_path), "containerPath": container_path, "mimeType": mime_type}],
        )

        try:
            await on_message(message, room_config, is_main)
        except Exception as e:
            logger.error("Error handling image message: room={}, error={}", room_id, e)

    # Register callbacks  # type: ignore[arg-type]  # matrix-nio callback types are more specific than the base signature
    client.add_event_callback(_on_room_message, RoomMessageText)  # type: ignore[arg-type]
    client.add_event_callback(_on_image_message, RoomMessageImage)  # type: ignore[arg-type]
    client.add_event_callback(_on_image_message, RoomEncryptedImage)  # type: ignore[arg-type]
    client.add_event_callback(_on_audio_message, RoomMessageAudio)  # type: ignore[arg-type]
    client.add_event_callback(_on_audio_message, RoomEncryptedAudio)  # type: ignore[arg-type]
    client.add_event_callback(_on_invite, InviteMemberEvent)  # type: ignore[arg-type]
    client.add_event_callback(_on_megolm_event, MegolmEvent)  # type: ignore[arg-type]

    logger.info("Matrix monitor started")
