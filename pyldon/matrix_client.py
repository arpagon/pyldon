"""Matrix client module for Pyldon.

Migrated from NanoClaw src/matrix-client.ts.
Handles Matrix connection, authentication, message sending, and E2EE via matrix-nio.

E2EE improvements inspired by hermes-agent (NousResearch):
- Megolm key export/import across restarts
- Proactive E2EE maintenance (key upload/query/claim, send_to_device)
- Auto-trust all devices so senders share session keys
- Buffer + retry for undecrypted MegolmEvents
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from loguru import logger
from nio import (
    AsyncClient,
    AsyncClientConfig,
    KeyVerificationCancel,
    KeyVerificationKey,
    KeyVerificationMac,
    KeyVerificationStart,
    LoginResponse,
    MegolmEvent,
    RoomMessageText,
    RoomSendResponse,
    UnknownToDeviceEvent,
    WhoamiResponse,
)
from nio.event_builders.direct_messages import ToDeviceMessage
from nio.responses import ToDeviceError, ToDeviceResponse

from pyldon.config import DATA_DIR, STORE_DIR
from pyldon.models import MatrixConfig

_client: AsyncClient | None = None
_config: MatrixConfig | None = None

# --- E2EE: Megolm key persistence ---
_KEY_EXPORT_DIR = STORE_DIR / "matrix"
_KEY_EXPORT_FILE = _KEY_EXPORT_DIR / "exported_megolm_keys.txt"
_KEY_EXPORT_PASSPHRASE = "pyldon-matrix-e2ee-keys"

# --- E2EE: Pending undecrypted events buffer ---
_MAX_PENDING_EVENTS = 100
_PENDING_EVENT_TTL = 300  # seconds — stop retrying after 5 min
_pending_megolm: list[tuple[Any, MegolmEvent, float]] = []


async def _trust_unknown_devices_in_room(room_id: str) -> bool:
    """Trust any unverified devices for users in a room.

    Returns True if any new devices were trusted.
    Called automatically before sending encrypted messages.
    """
    client = get_matrix_client()
    if not client.olm:
        return False

    trusted_any = False
    room = client.rooms.get(room_id)
    if not room:
        return False

    for user_id in room.users:
        devices = client.device_store.active_user_devices(user_id)
        for device in devices:
            if not client.olm.is_device_verified(device):
                logger.info(
                    "E2EE: auto-trusting new device {} of user {}",
                    device.device_id,
                    user_id,
                )
                client.verify_device(device)
                trusted_any = True

    return trusted_any


async def _room_send_with_trust(room_id: str, content: dict[str, Any]) -> Any:
    """Send a room message, auto-trusting new devices on failure and retrying once."""
    client = get_matrix_client()

    resp = await client.room_send(
        room_id=room_id,
        message_type="m.room.message",
        content=content,
    )

    if not isinstance(resp, RoomSendResponse):
        error_str = str(resp)
        if "not verified" in error_str or "blacklisted" in error_str or "Missing session" in error_str:
            logger.warning("E2EE: send failed in {} ({}), running key maintenance and retrying...", room_id, error_str[:120])
            try:
                await client.keys_query()
                # Claim one-time keys for devices we don't have Olm sessions with
                users = client.get_users_for_key_claiming()
                if users:
                    await client.keys_claim(users)
                await client.send_to_device_messages()
            except Exception as e:
                logger.debug("E2EE: key maintenance error during retry: {}", e)
            await _trust_unknown_devices_in_room(room_id)
            resp = await client.room_send(
                room_id=room_id,
                message_type="m.room.message",
                content=content,
            )

    return resp


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

    resp = await _room_send_with_trust(room_id, content)

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

    # Check if room is encrypted
    room = client.rooms.get(room_id)
    is_encrypted = room.encrypted if room else False

    # Upload to Matrix content repository
    with open(path, "rb") as f:
        resp, maybe_keys = await client.upload(
            f,
            content_type=mime_type,
            filename=path.name,
            filesize=file_size,
            encrypt=is_encrypted,
        )

    if not hasattr(resp, "content_uri"):
        logger.error("Failed to upload audio to Matrix: {}", resp)
        return ""

    content: dict[str, Any] = {
        "msgtype": "m.audio",
        "body": path.name,
        "info": {
            "mimetype": mime_type,
            "size": file_size,
        },
    }

    if is_encrypted and maybe_keys:
        content["file"] = {
            "url": resp.content_uri,
            "key": maybe_keys["key"],
            "iv": maybe_keys["iv"],
            "hashes": maybe_keys["hashes"],
            "v": maybe_keys["v"],
        }
    else:
        content["url"] = resp.content_uri

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

    resp2 = await _room_send_with_trust(room_id, content)

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

    # Check if room is encrypted
    room = client.rooms.get(room_id)
    is_encrypted = room.encrypted if room else False

    # Upload to Matrix content repository
    with open(path, "rb") as f:
        resp, maybe_keys = await client.upload(
            f,
            content_type=mime_type,
            filename=path.name,
            filesize=file_size,
            encrypt=is_encrypted,
        )

    if not hasattr(resp, "content_uri"):
        logger.error("Failed to upload image to Matrix: {}", resp)
        return ""

    content: dict[str, Any] = {
        "msgtype": "m.image",
        "body": path.name,
        "info": {
            "mimetype": mime_type,
            "size": file_size,
        },
    }

    if is_encrypted and maybe_keys:
        content["file"] = {
            "url": resp.content_uri,
            "key": maybe_keys["key"],
            "iv": maybe_keys["iv"],
            "hashes": maybe_keys["hashes"],
            "v": maybe_keys["v"],
        }
    else:
        content["url"] = resp.content_uri

    if width and height:
        content["info"]["w"] = width
        content["info"]["h"] = height

    if thread_id:
        content["m.relates_to"] = {
            "rel_type": "m.thread",
            "event_id": thread_id,
        }

    resp2 = await _room_send_with_trust(room_id, content)

    event_id = ""
    if isinstance(resp2, RoomSendResponse):
        event_id = resp2.event_id
        logger.info("Image sent: room={}, event_id={}, file={}", room_id, event_id, path.name)
    else:
        logger.error("Failed to send image to {}: {}", room_id, resp2)

    return event_id


async def stop_matrix_client() -> None:
    """Stop the Matrix client, exporting Megolm keys for next restart."""
    global _client
    if _client is not None:
        await export_megolm_keys()
        await _client.close()
        _client = None
        logger.info("Matrix client stopped")


# ---------------------------------------------------------------------------
# E2EE: Megolm key export / import
# ---------------------------------------------------------------------------

async def export_megolm_keys() -> None:
    """Export Megolm session keys so the next restart can decrypt old messages."""
    client = _client
    if not client or not _config or not _config.encryption:
        return
    if not getattr(client, "olm", None):
        return
    try:
        _KEY_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        # Remove existing file to avoid atomic-rename collision
        if _KEY_EXPORT_FILE.exists():
            _KEY_EXPORT_FILE.unlink()
        await client.export_keys(str(_KEY_EXPORT_FILE), _KEY_EXPORT_PASSPHRASE)
        logger.info("E2EE: exported Megolm keys for next restart")
    except Exception as exc:
        logger.warning("E2EE: failed to export Megolm keys: {}", exc)


async def import_megolm_keys() -> None:
    """Import previously exported Megolm keys (survives restarts)."""
    client = _client
    if not client or not _config or not _config.encryption:
        return
    if not getattr(client, "olm", None):
        return
    if not _KEY_EXPORT_FILE.exists():
        return
    try:
        await client.import_keys(str(_KEY_EXPORT_FILE), _KEY_EXPORT_PASSPHRASE)
        logger.info("E2EE: imported Megolm keys from backup")
    except Exception as exc:
        logger.debug("E2EE: could not import keys: {}", exc)


# ---------------------------------------------------------------------------
# E2EE: Proactive maintenance (key upload/query/claim, send_to_device)
# ---------------------------------------------------------------------------

async def run_e2ee_maintenance() -> None:
    """Run matrix-nio E2EE housekeeping.

    Should be called periodically (e.g. after each sync or on a timer).
    Drives key management that sync_forever() doesn't fully handle:
    - Upload device keys if needed
    - Query keys for users we share rooms with
    - Claim one-time keys for new devices
    - Send queued to-device messages (key forwards, etc.)
    - Auto-trust all devices so senders share session keys with us
    - Retry decryption for buffered MegolmEvents
    """
    client = _client
    if not client or not _config or not _config.encryption:
        return
    if not getattr(client, "olm", None):
        return

    did_query_keys = client.should_query_keys

    tasks: list[asyncio.Task[Any]] = [
        asyncio.create_task(client.send_to_device_messages()),
    ]

    if client.should_upload_keys:
        tasks.append(asyncio.create_task(client.keys_upload()))

    if did_query_keys:
        tasks.append(asyncio.create_task(client.keys_query()))

    if client.should_claim_keys:
        users = client.get_users_for_key_claiming()
        if users:
            tasks.append(asyncio.create_task(client.keys_claim(users)))

    for coro in asyncio.as_completed(tasks):
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("E2EE: maintenance task failed: {}", exc)

    # After key queries, auto-trust all devices so senders share keys with us.
    if did_query_keys:
        auto_trust_all_devices()

    # Retry any buffered undecrypted events now that new keys may have arrived.
    if _pending_megolm:
        await retry_pending_decryptions()


def auto_trust_all_devices() -> None:
    """Trust/verify all unverified devices we know about.

    When other clients see our device as verified, they proactively share
    Megolm session keys with us. Without this, many clients refuse to
    include an unverified device in key distributions.
    """
    client = _client
    if not client:
        return

    device_store = getattr(client, "device_store", None)
    if not device_store:
        return

    own_device = getattr(client, "device_id", None)
    trusted_count = 0

    try:
        for device in device_store:
            if getattr(device, "device_id", None) == own_device:
                continue
            if not getattr(device, "verified", False):
                client.verify_device(device)
                trusted_count += 1
    except Exception as exc:
        logger.debug("E2EE: auto-trust error: {}", exc)

    if trusted_count:
        logger.info("E2EE: auto-trusted {} new device(s)", trusted_count)


# ---------------------------------------------------------------------------
# E2EE: MegolmEvent buffer + retry
# ---------------------------------------------------------------------------

def buffer_megolm_event(room: Any, event: MegolmEvent) -> None:
    """Buffer an undecrypted MegolmEvent for later retry."""
    global _pending_megolm
    _pending_megolm.append((room, event, time.time()))
    if len(_pending_megolm) > _MAX_PENDING_EVENTS:
        _pending_megolm = _pending_megolm[-_MAX_PENDING_EVENTS:]
    logger.info(
        "E2EE: buffered undecrypted event {} (pending={})",
        getattr(event, "event_id", "?"),
        len(_pending_megolm),
    )


async def retry_pending_decryptions() -> None:
    """Retry decrypting buffered MegolmEvents after new keys arrive."""
    import nio

    global _pending_megolm
    client = _client
    if not client or not _pending_megolm:
        return

    now = time.time()
    still_pending: list[tuple[Any, MegolmEvent, float]] = []

    for room, event, ts in _pending_megolm:
        # Drop events past the TTL
        if now - ts > _PENDING_EVENT_TTL:
            logger.debug(
                "E2EE: dropping expired pending event {} (age {:.0f}s)",
                getattr(event, "event_id", "?"),
                now - ts,
            )
            continue

        try:
            decrypted = client.decrypt_event(event)
        except Exception:
            still_pending.append((room, event, ts))
            continue

        if isinstance(decrypted, nio.MegolmEvent):
            # Still undecryptable
            still_pending.append((room, event, ts))
            continue

        logger.info(
            "E2EE: decrypted buffered event {} ({})",
            getattr(event, "event_id", "?"),
            type(decrypted).__name__,
        )

        # Route to the appropriate handler via the registered callbacks
        try:
            if isinstance(decrypted, nio.RoomMessageText):
                for cb in client.event_callbacks[nio.RoomMessageText]:
                    await cb.func(room, decrypted)
            elif isinstance(
                decrypted,
                (nio.RoomMessageImage, nio.RoomEncryptedImage),
            ):
                for cb in client.event_callbacks[nio.RoomMessageImage]:
                    await cb.func(room, decrypted)
            elif isinstance(
                decrypted,
                (nio.RoomMessageAudio, nio.RoomEncryptedAudio),
            ):
                for cb in client.event_callbacks[nio.RoomMessageAudio]:
                    await cb.func(room, decrypted)
            else:
                logger.debug(
                    "E2EE: decrypted event {} has unhandled type {}",
                    getattr(event, "event_id", "?"),
                    type(decrypted).__name__,
                )
        except Exception as exc:
            logger.warning(
                "E2EE: error processing decrypted event {}: {}",
                getattr(event, "event_id", "?"),
                exc,
            )

    prev_count = len(_pending_megolm)
    _pending_megolm = still_pending
    resolved = prev_count - len(still_pending)
    if resolved:
        logger.info("E2EE: resolved {} buffered event(s), {} still pending", resolved, len(still_pending))


# ---------------------------------------------------------------------------
# E2EE: SAS Key Verification (auto-accept)
# ---------------------------------------------------------------------------

def register_verification_callbacks() -> None:
    """Register to-device callbacks for SAS key verification.

    Allows users to verify the bot's device from Element by clicking
    "Verify session". The bot auto-accepts and confirms the SAS emojis.

    Element sends: request → (we reply ready) → start → key → mac → done
    matrix-nio doesn't parse m.key.verification.request natively, so we
    intercept it via UnknownToDeviceEvent.
    """
    client = _client
    if not client or not _config or not _config.encryption:
        return

    client.add_to_device_callback(_on_unknown_to_device, UnknownToDeviceEvent)
    client.add_to_device_callback(_on_verification_start, KeyVerificationStart)
    client.add_to_device_callback(_on_verification_key, KeyVerificationKey)
    client.add_to_device_callback(_on_verification_mac, KeyVerificationMac)
    client.add_to_device_callback(_on_verification_cancel, KeyVerificationCancel)
    logger.info("E2EE: SAS verification callbacks registered")


async def _on_unknown_to_device(event: UnknownToDeviceEvent) -> None:
    """Handle unknown to-device events — catch m.key.verification.request."""
    if event.type != "m.key.verification.request":
        return

    client = _client
    if not client:
        return

    content = event.source.get("content", {})
    transaction_id = content.get("transaction_id", "")
    from_device = content.get("from_device", "")
    methods = content.get("methods", [])

    logger.info(
        "E2EE: verification request received: sender={}, device={}, transaction={}, methods={}",
        event.sender, from_device, transaction_id, methods,
    )

    if "m.sas.v1" not in methods:
        logger.warning("E2EE: no SAS method in request, ignoring: {}", methods)
        return

    # Reply with m.key.verification.ready
    ready_content = {
        "type": "m.key.verification.ready",
        "transaction_id": transaction_id,
        "from_device": client.device_id,
        "methods": ["m.sas.v1"],
    }

    ready_msg = ToDeviceMessage(
        type="m.key.verification.ready",
        recipient=event.sender,
        recipient_device=from_device,
        content=ready_content,
    )

    resp = await client.to_device(ready_msg)
    if isinstance(resp, ToDeviceError):
        logger.error("E2EE: failed to send verification ready: {}", resp)
    else:
        logger.info("E2EE: sent verification ready for {}", transaction_id)


async def _on_verification_start(event: KeyVerificationStart) -> None:
    """Handle incoming SAS verification start — auto-accept."""
    client = _client
    if not client:
        return

    logger.info(
        "E2EE: verification start received: sender={}, device={}, transaction={}, method={}",
        event.sender, event.from_device, event.transaction_id, event.method,
    )

    if event.method != "m.sas.v1":
        logger.warning("E2EE: unsupported verification method: {}", event.method)
        return

    resp = await client.accept_key_verification(event.transaction_id)
    if isinstance(resp, ToDeviceError):
        logger.error("E2EE: failed to accept verification: {}", resp)
    else:
        logger.info("E2EE: accepted verification {}", event.transaction_id)


async def _on_verification_key(event: KeyVerificationKey) -> None:
    """Handle SAS key exchange — confirm the short auth string."""
    client = _client
    if not client:
        return

    # Get the SAS object for this transaction
    sas = client.key_verifications.get(event.transaction_id)
    if not sas:
        logger.warning("E2EE: no SAS object for transaction {}", event.transaction_id)
        return

    # Log the emojis for reference (even though we auto-confirm)
    try:
        emojis = sas.get_emoji()
        emoji_str = " ".join(e[0] for e in emojis) if emojis else "N/A"
        logger.info("E2EE: SAS emojis for {}: {}", event.transaction_id, emoji_str)
    except Exception:
        pass

    resp = await client.confirm_short_auth_string(event.transaction_id)
    if isinstance(resp, ToDeviceError):
        logger.error("E2EE: failed to confirm SAS: {}", resp)
    else:
        logger.info("E2EE: confirmed SAS for {}", event.transaction_id)


async def _on_verification_mac(event: KeyVerificationMac) -> None:
    """Handle SAS MAC — verification is complete, send done."""
    client = _client
    if not client:
        return

    sas = client.key_verifications.get(event.transaction_id)
    if not sas:
        logger.warning("E2EE: no SAS object for MAC transaction {}", event.transaction_id)
        return

    if sas.verified:
        logger.info(
            "E2EE: ✅ device verified! sender={}, device={}, transaction={}",
            event.sender, sas.other_olm_device.device_id if sas.other_olm_device else "?",
            event.transaction_id,
        )
        # Persist the verified state
        try:
            if sas.other_olm_device:
                client.verify_device(sas.other_olm_device)
                logger.info("E2EE: device {} marked as verified in store", sas.other_olm_device.device_id)
        except Exception as exc:
            logger.warning("E2EE: failed to persist verification: {}", exc)

        # Send m.key.verification.done to complete the flow
        done_content = {
            "transaction_id": event.transaction_id,
        }
        done_msg = ToDeviceMessage(
            type="m.key.verification.done",
            recipient=event.sender,
            recipient_device=sas.other_olm_device.device_id if sas.other_olm_device else event.sender,
            content=done_content,
        )
        resp = await client.to_device(done_msg)
        if isinstance(resp, ToDeviceError):
            logger.error("E2EE: failed to send verification done: {}", resp)
        else:
            logger.info("E2EE: sent verification done for {}", event.transaction_id)
    else:
        logger.warning("E2EE: MAC received but SAS not verified for {}", event.transaction_id)


async def _on_verification_cancel(event: KeyVerificationCancel) -> None:
    """Handle SAS verification cancellation."""
    logger.warning(
        "E2EE: verification cancelled: transaction={}, reason={}, code={}",
        event.transaction_id,
        getattr(event, "reason", "?"),
        getattr(event, "code", "?"),
    )
