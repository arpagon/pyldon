"""Speech-to-text module for Pyldon.

Transcribes audio messages using parakeet-rs (Rust binary with Parakeet TDT v3 ONNX).
Runs directly on the host — no Docker container needed.
Long audio is automatically split into chunks to avoid ONNX dimension errors.
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

# Chunking config
MAX_CHUNK_SECONDS = 240  # 4 minutes — safe limit for parakeet ONNX model
OVERLAP_SECONDS = 2  # small overlap to avoid cutting words


def _get_audio_duration(wav_path: Path) -> float | None:
    """Get duration of a WAV file in seconds using ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(wav_path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return float(result.stdout.strip())
    except Exception:
        return None


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


def _split_wav(wav_path: Path, output_dir: Path) -> list[Path]:
    """Split a WAV file into chunks if it exceeds MAX_CHUNK_SECONDS.

    Returns list of WAV chunk paths (may be just [wav_path] if short enough).
    """
    duration = _get_audio_duration(wav_path)
    if duration is None or duration <= MAX_CHUNK_SECONDS:
        return [wav_path]

    chunks: list[Path] = []
    start = 0.0
    idx = 0
    while start < duration:
        chunk_path = output_dir / f"chunk_{idx:03d}.wav"
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-ss", str(start),
                    "-i", str(wav_path),
                    "-t", str(MAX_CHUNK_SECONDS),
                    "-ar", "16000", "-ac", "1", "-sample_fmt", "s16",
                    "-f", "wav", str(chunk_path),
                ],
                capture_output=True,
                check=True,
                timeout=30,
            )
            chunks.append(chunk_path)
        except Exception as e:
            logger.error("Failed to split chunk {}: {}", idx, e)
            break
        start += MAX_CHUNK_SECONDS - OVERLAP_SECONDS
        idx += 1

    logger.info("Split audio ({:.0f}s) into {} chunks", duration, len(chunks))
    return chunks


async def _transcribe_chunk(wav_path: Path) -> str | None:
    """Transcribe a single WAV chunk using parakeet-cli."""
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
        logger.error("STT chunk timeout after {}s: {}", STT_TIMEOUT_S, wav_path.name)
        process.kill()  # type: ignore[union-attr]
        return None
    except Exception as e:
        logger.error("STT chunk subprocess error: {}", e)
        return None

    # Log stderr
    if stderr:
        for line in stderr.decode(errors="replace").strip().splitlines():
            logger.debug("[stt] {}", line)

    if process.returncode != 0:
        logger.error("parakeet-cli failed on {}: exit_code={}", wav_path.name, process.returncode)
        return None

    try:
        result = json.loads(stdout.decode().strip())
        return result.get("text", "").strip()
    except (json.JSONDecodeError, IndexError) as e:
        logger.error("Failed to parse STT output for {}: {}", wav_path.name, e)
        return None


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

        # Split into chunks if needed
        chunks = _split_wav(wav_path, tmpdir_path)

        # Transcribe all chunks
        texts: list[str] = []
        for chunk in chunks:
            text = await _transcribe_chunk(chunk)
            if text:
                texts.append(text)

        if not texts:
            logger.warning("STT returned empty text")
            return None

        full_text = " ".join(texts)
        duration = _get_audio_duration(wav_path)
        logger.info(
            "STT success: {} chars, duration={}s, chunks={}",
            len(full_text), f"{duration:.1f}" if duration else "?", len(chunks),
        )
        return full_text
