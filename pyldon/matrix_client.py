"""Matrix client module for Pyldon.

Migrated from NanoClaw src/matrix-client.ts.
Handles Matrix connection, authentication, message sending, and E2EE via matrix-nio.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger
from nio import (
    AsyncClient,
    AsyncClientConfig,
    LoginResponse,
    RoomMessageText,
    RoomSendResponse,
)

from pyldon.config import DATA_DIR, STORE_DIR
from pyldon.models import MatrixConfig

_client: AsyncClient | None = None
_config: MatrixConfig | None = None


def load_matrix_config() -> MatrixConfig:
    """Load Matrix configuration from file or environment variables."""
    import os

    config_path = DATA_DIR / "matrix_config.json"

    # Try loading from config file first
    if config_path.exists():
        data = json.loads(config_path.read_text(encoding="utf-8"))
        return MatrixConfig(**data)

    # Fall back to environment variables
    homeserver = os.environ.get("MATRIX_HOMESERVER")
    user_id = os.environ.get("MATRIX_USER_ID")
    access_token = os.environ.get("MATRIX_ACCESS_TOKEN")

    if not homeserver or not user_id or not access_token:
        raise RuntimeError(
            "Matrix credentials not configured. Set MATRIX_HOMESERVER, MATRIX_USER_ID, "
            "and MATRIX_ACCESS_TOKEN in .env or create data/matrix_config.json"
        )

    return MatrixConfig(
        homeserver=homeserver,
        user_id=user_id,
        access_token=access_token,
        encryption=os.environ.get("MATRIX_ENCRYPTION") == "true",
        require_mention=os.environ.get("MATRIX_REQUIRE_MENTION") != "false",
    )


async def init_matrix_client() -> AsyncClient:
    """Initialize and return the Matrix client."""
    global _client, _config

    if _client is not None:
        return _client

    _config = load_matrix_config()

    store_dir = STORE_DIR / "matrix"
    store_dir.mkdir(parents=True, exist_ok=True)

    client_config = AsyncClientConfig(
        store_sync_tokens=True,
        encryption_enabled=_config.encryption,
    )

    _client = AsyncClient(
        homeserver=_config.homeserver,
        user=_config.user_id,
        store_path=str(store_dir),
        config=client_config,
    )

    # Set the access token directly (no login needed)
    _client.access_token = _config.access_token
    _client.user_id = _config.user_id

    # Load device_id from store if available, otherwise we'll get it from a whoami call
    device_id_path = store_dir / "device_id"
    if device_id_path.exists():
        _client.device_id = device_id_path.read_text(encoding="utf-8").strip()
    else:
        # Get device_id via whoami
        resp = await _client.whoami()
        if hasattr(resp, "device_id") and resp.device_id:
            _client.device_id = resp.device_id
            device_id_path.write_text(resp.device_id, encoding="utf-8")

    # If E2EE is enabled, load or create olm account
    if _config.encryption:
        crypto_dir = store_dir / "crypto"
        crypto_dir.mkdir(parents=True, exist_ok=True)
        logger.info("E2EE crypto storage initialized at {}", crypto_dir)

    logger.info(
        "Matrix client initialized: homeserver={}, user_id={}, encryption={}",
        _config.homeserver,
        _config.user_id,
        _config.encryption,
    )

    return _client


def get_matrix_client() -> AsyncClient:
    """Get the Matrix client, raising if not initialized."""
    if _client is None:
        raise RuntimeError("Matrix client not initialized. Call init_matrix_client() first.")
    return _client


def get_matrix_config() -> MatrixConfig:
    """Get the Matrix configuration."""
    global _config
    if _config is None:
        _config = load_matrix_config()
    return _config


async def send_matrix_message(
    room_id: str, text: str, thread_id: str | None = None
) -> str:
    """Send a text message to a Matrix room. Returns the event ID."""
    client = get_matrix_client()

    content: dict[str, Any] = {
        "msgtype": "m.text",
        "body": text,
    }

    # Thread support
    if thread_id:
        content["m.relates_to"] = {
            "rel_type": "m.thread",
            "event_id": thread_id,
        }

    resp = await client.room_send(
        room_id=room_id,
        message_type="m.room.message",
        content=content,
    )

    event_id = ""
    if isinstance(resp, RoomSendResponse):
        event_id = resp.event_id
        logger.info("Matrix message sent: room={}, event_id={}, length={}", room_id, event_id, len(text))
    else:
        logger.error("Failed to send Matrix message to {}: {}", room_id, resp)

    return event_id


async def set_matrix_typing(room_id: str, is_typing: bool) -> None:
    """Set typing indicator in a room."""
    try:
        client = get_matrix_client()
        await client.room_typing(room_id, typing_state=is_typing, timeout=30000 if is_typing else 0)
    except Exception as e:
        logger.debug("Failed to set typing indicator in {}: {}", room_id, e)


async def stop_matrix_client() -> None:
    """Stop the Matrix client."""
    global _client
    if _client is not None:
        await _client.close()
        _client = None
        logger.info("Matrix client stopped")
