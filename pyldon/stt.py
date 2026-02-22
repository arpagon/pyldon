"""Speech-to-text module for Pyldon.

Transcribes audio messages using parakeet-rs (Rust binary with Parakeet TDT v3 ONNX).
Runs directly on the host — no Docker container needed.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
from pathlib import Path

from loguru import logger

from pyldon.config import DATA_DIR, STT_ENABLED

# Paths
PARAKEET_BINARY = DATA_DIR / "stt" / "parakeet-cli"
MODEL_DIR = DATA_DIR / "models" / "tdt"
STT_TIMEOUT_S = 120


def _convert_to_wav(input_path: Path, output_path: Path) -> bool:
    """Convert any audio format to WAV 16kHz mono PCM_16 using ffmpeg."""
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(input_path),
                "-ar", "16000", "-ac", "1", "-sample_fmt", "s16",
                "-f", "wav", str(output_path),
            ],
            capture_output=True,
            check=True,
            timeout=30,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.error("ffmpeg conversion failed: {}", e)
        return False


async def transcribe_audio(audio_data: bytes, filename: str = "audio.ogg") -> str | None:
    """Transcribe audio bytes to text using parakeet-rs.

    Args:
        audio_data: Raw audio bytes (OGG/Opus, WAV, etc.)
        filename: Original filename hint for format detection.

    Returns:
        Transcribed text, or None on failure.
    """
    if not STT_ENABLED:
        logger.debug("STT disabled, skipping transcription")
        return None

    if not audio_data:
        logger.warning("Empty audio data, skipping")
        return None

    if not PARAKEET_BINARY.exists():
        logger.error("parakeet-cli binary not found at {}", PARAKEET_BINARY)
        return None

    if not MODEL_DIR.exists():
        logger.error("Parakeet model not found at {}", MODEL_DIR)
        return None

    logger.info("Transcribing audio: {} bytes, filename={}", len(audio_data), filename)

    with tempfile.TemporaryDirectory(prefix="pyldon-stt-") as tmpdir:
        tmpdir_path = Path(tmpdir)

        # Write audio to temp file
        input_path = tmpdir_path / filename
        input_path.write_bytes(audio_data)

        # Convert to WAV 16kHz mono (parakeet-rs needs WAV)
        wav_path = tmpdir_path / "audio.wav"
        if not _convert_to_wav(input_path, wav_path):
            return None

        logger.debug("WAV converted: {} bytes", wav_path.stat().st_size)

        # Run parakeet-cli
        try:
            process = await asyncio.create_subprocess_exec(
                str(PARAKEET_BINARY), str(wav_path), str(MODEL_DIR),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=STT_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            logger.error("STT timeout after {}s", STT_TIMEOUT_S)
            process.kill()  # type: ignore[union-attr]
            return None
        except Exception as e:
            logger.error("STT subprocess error: {}", e)
            return None

        # Log stderr
        if stderr:
            for line in stderr.decode(errors="replace").strip().splitlines():
                logger.debug("[stt] {}", line)

        if process.returncode != 0:
            logger.error("parakeet-cli failed: exit_code={}", process.returncode)
            return None

        # Parse JSON output
        try:
            result = json.loads(stdout.decode().strip())
            text = result.get("text", "").strip()

            if not text:
                logger.warning("STT returned empty text")
                return None

            logger.info(
                "STT success: {} chars, duration={}s",
                len(text), result.get("duration_s", "?"),
            )
            return text

        except (json.JSONDecodeError, IndexError) as e:
            logger.error("Failed to parse STT output: {}, stdout={}", e, stdout[:200])
            return None
