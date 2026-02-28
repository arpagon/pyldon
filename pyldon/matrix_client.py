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
    WhoamiResponse,
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


def _save_matrix_config(cfg: MatrixConfig) -> None:
    """Persist matrix config back to disk (after login updates tokens)."""
    config_path = DATA_DIR / "matrix_config.json"
    config_path.write_text(
        json.dumps(cfg.model_dump(exclude_none=True), indent=2),
        encoding="utf-8",
    )


async def init_matrix_client() -> AsyncClient:
    """Initialize and return the Matrix client.

    For E2EE to work, the client must login with password on first run
    to generate Olm device keys. Subsequent runs reuse the stored
    access_token, device_id, and crypto store.
    """
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
        device_id=_config.device_id or "",
        store_path=str(store_dir),
        config=client_config,
    )

    if _config.encryption and _config.password and not _config.device_id:
        # First run with E2EE: login with password to generate device keys
        logger.info("E2EE first run: logging in with password to generate device keys")
        resp = await _client.login(
            password=_config.password,
            device_name="PyldonBot",
        )
        if isinstance(resp, LoginResponse):
            logger.info(
                "Login successful: device_id={}, user_id={}",
                resp.device_id,
                resp.user_id,
            )
            # Update config with new credentials
            _config.access_token = resp.access_token
            _config.device_id = resp.device_id
            _client.access_token = resp.access_token
            _client.device_id = resp.device_id
            # Persist updated config
            _save_matrix_config(_config)
        else:
            logger.error("Login failed: {}", resp)
            raise RuntimeError(f"Matrix login failed: {resp}")
    elif _config.encryption and _config.device_id:
        # Subsequent runs: use stored access_token + device_id, load crypto store
        _client.access_token = _config.access_token
        _client.user_id = _config.user_id
        _client.device_id = _config.device_id
        # Load the olm account and crypto keys from the store DB
        _client.load_store()
        logger.info(
            "E2EE: using stored device_id={}, olm loaded", _config.device_id
        )
    else:
        # No E2EE or no password: use access_token directly
        _client.access_token = _config.access_token
        _client.user_id = _config.user_id

        # Try to get device_id if we don't have one
        if not _config.device_id:
            resp = await _client.whoami()
            if isinstance(resp, WhoamiResponse):
                device_id: str | None = resp.device_id
                if device_id:
                    _client.device_id = device_id

    # If E2EE, trust all devices in rooms we're in (auto-trust for bot use)
    if _config.encryption:
        logger.info(
            "E2EE enabled: device_id={}, store_path={}",
            _client.device_id,
            store_dir,
        )

    logger.info(
        "Matrix client initialized: homeserver={}, user_id={}, device_id={}, encryption={}",
        _config.homeserver,
        _config.user_id,
        _client.device_id,
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
    """Send a text message to a Matrix room with markdown rendering. Returns the event ID."""
    client = get_matrix_client()

    # Convert markdown to HTML for rich rendering in Matrix clients
    from markdown_it import MarkdownIt

    md = MarkdownIt().enable("strikethrough").enable("table")
    html = md.render(text).strip()

    content: dict[str, Any] = {
        "msgtype": "m.text",
        "body": text,
        "format": "org.matrix.custom.html",
        "formatted_body": html,
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


async def send_matrix_audio(
    room_id: str,
    audio_path: str,
    thread_id: str | None = None,
) -> str:
    """Upload and send an audio file to a Matrix room. Returns the event ID."""
    from pathlib import Path
    import mimetypes

    client = get_matrix_client()
    path = Path(audio_path)

    if not path.exists():
        logger.error("Audio file not found: {}", audio_path)
        return ""

    mime_type = mimetypes.guess_type(str(path))[0] or "audio/ogg"
    file_size = path.stat().st_size

    # Upload to Matrix content repository
    with open(path, "rb") as f:
        resp, _maybe_keys = await client.upload(
            f,
            content_type=mime_type,
            filename=path.name,
            filesize=file_size,
        )

    if not hasattr(resp, "content_uri"):
        logger.error("Failed to upload audio to Matrix: {}", resp)
        return ""

    content: dict[str, Any] = {
        "msgtype": "m.audio",
        "body": path.name,
        "url": resp.content_uri,
        "info": {
            "mimetype": mime_type,
            "size": file_size,
        },
    }

    # Try to get duration
    try:
        import subprocess
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            duration_ms = int(float(result.stdout.strip()) * 1000)
            content["info"]["duration"] = duration_ms
    except Exception:
        pass

    if thread_id:
        content["m.relates_to"] = {
            "rel_type": "m.thread",
            "event_id": thread_id,
        }

    resp2 = await client.room_send(
        room_id=room_id,
        message_type="m.room.message",
        content=content,
    )

    event_id = ""
    if isinstance(resp2, RoomSendResponse):
        event_id = resp2.event_id
        logger.info("Audio sent: room={}, event_id={}, file={}", room_id, event_id, path.name)
    else:
        logger.error("Failed to send audio to {}: {}", room_id, resp2)

    return event_id


async def send_matrix_image(
    room_id: str,
    image_path: str,
    thread_id: str | None = None,
) -> str:
    """Upload and send an image file to a Matrix room. Returns the event ID."""
    from pathlib import Path
    import mimetypes

    client = get_matrix_client()
    path = Path(image_path)

    if not path.exists():
        logger.error("Image file not found: {}", image_path)
        return ""

    mime_type = mimetypes.guess_type(str(path))[0] or "image/png"
    file_size = path.stat().st_size

    # Get image dimensions
    width, height = 0, 0
    try:
        from PIL import Image
        with Image.open(path) as img:
            width, height = img.size
    except Exception:
        pass

    # Upload to Matrix content repository
    with open(path, "rb") as f:
        resp, _maybe_keys = await client.upload(
            f,
            content_type=mime_type,
            filename=path.name,
            filesize=file_size,
        )

    if not hasattr(resp, "content_uri"):
        logger.error("Failed to upload image to Matrix: {}", resp)
        return ""

    content: dict[str, Any] = {
        "msgtype": "m.image",
        "body": path.name,
        "url": resp.content_uri,
        "info": {
            "mimetype": mime_type,
            "size": file_size,
        },
    }

    if width and height:
        content["info"]["w"] = width
        content["info"]["h"] = height

    if thread_id:
        content["m.relates_to"] = {
            "rel_type": "m.thread",
            "event_id": thread_id,
        }

    resp2 = await client.room_send(
        room_id=room_id,
        message_type="m.room.message",
        content=content,
    )

    event_id = ""
    if isinstance(resp2, RoomSendResponse):
        event_id = resp2.event_id
        logger.info("Image sent: room={}, event_id={}, file={}", room_id, event_id, path.name)
    else:
        logger.error("Failed to send image to {}: {}", room_id, resp2)

    return event_id


async def stop_matrix_client() -> None:
    """Stop the Matrix client."""
    global _client
    if _client is not None:
        await _client.close()
        _client = None
        logger.info("Matrix client stopped")
