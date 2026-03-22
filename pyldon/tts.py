"""Text-to-speech via Qwen3-TTS OpenAI-compatible API.

The agent marks semantic boundaries with newlines in the text.
Pyldon splits on those boundaries, generates audio chunks sequentially,
and concatenates into a single file.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import httpx
from loguru import logger

from pyldon.config import TTS_API_KEY, TTS_BASE_URL, TTS_ENABLED, TTS_LANGUAGE, TTS_VOICE

# 30 min timeout per chunk — the server queues internally,
# just wait for it to finish.
TTS_TIMEOUT = 1800

# Chunking
CHUNK_MAX_CHARS = 800  # merge small lines up to this


def _split_into_chunks(text: str) -> list[str]:
    """Split text on newlines (semantic boundaries marked by the agent).

    Merges small consecutive segments to avoid too many tiny requests.
    """
    segments = [s.strip() for s in text.split("\n") if s.strip()]

    if not segments:
        return [text.strip()] if text.strip() else []

    chunks: list[str] = []
    current = ""
    for seg in segments:
        if current and len(current) + len(seg) + 1 > CHUNK_MAX_CHARS:
            chunks.append(current)
            current = seg
        else:
            current = f"{current} {seg}".strip() if current else seg

    if current:
        chunks.append(current)

    return chunks


async def _generate_chunk(
    client: httpx.AsyncClient,
    text: str,
    voice: str,
    language: str,
    chunk_idx: int,
    total_chunks: int,
) -> Path | None:
    """Generate TTS for a single chunk. Returns path to .wav file or None."""
    try:
        headers = {"Content-Type": "application/json"}
        if TTS_API_KEY:
            headers["Authorization"] = f"Bearer {TTS_API_KEY}"

        logger.debug("TTS chunk {}/{}: {} chars", chunk_idx + 1, total_chunks, len(text))

        resp = await client.post(
            f"{TTS_BASE_URL}/audio/speech",
            headers=headers,
            json={
                "input": text,
                "voice": voice,
                "response_format": "wav",
                "language": language,
            },
        )

        if resp.status_code != 200:
            logger.error("TTS API error on chunk {}/{}: {} {}",
                         chunk_idx + 1, total_chunks, resp.status_code, resp.text[:200])
            return None

        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False, prefix=f"tts_chunk{chunk_idx:03d}_")
        tmp.write(resp.content)
        tmp.close()
        return Path(tmp.name)

    except httpx.TimeoutException:
        logger.error("TTS timeout on chunk {}/{} ({} chars)", chunk_idx + 1, total_chunks, len(text))
        return None
    except Exception as e:
        logger.error("TTS chunk {}/{} failed ({}): {}", chunk_idx + 1, total_chunks, type(e).__name__, e)
        return None


def _concatenate_wavs(wav_paths: list[Path]) -> Path | None:
    """Concatenate WAV files into a single OGG using ffmpeg."""
    if len(wav_paths) == 1:
        ogg_path = wav_paths[0].with_suffix(".ogg")
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", str(wav_paths[0]), "-c:a", "libopus", "-b:a", "64k", str(ogg_path)],
            capture_output=True, timeout=30,
        )
        if result.returncode != 0:
            logger.error("ffmpeg conversion failed: {}", result.stderr.decode()[:200])
            return None
        return ogg_path

    concat_list = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, prefix="tts_concat_")
    for wav in wav_paths:
        concat_list.write(f"file '{wav}'\n")
    concat_list.close()

    ogg_path = wav_paths[0].with_name("tts_merged.ogg")
    result = subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
         "-i", concat_list.name, "-c:a", "libopus", "-b:a", "64k", str(ogg_path)],
        capture_output=True, timeout=60,
    )
    Path(concat_list.name).unlink(missing_ok=True)

    if result.returncode != 0:
        logger.error("ffmpeg concat failed: {}", result.stderr.decode()[:300])
        return None
    return ogg_path


async def generate_speech(
    text: str,
    voice: str | None = None,
    language: str | None = None,
) -> Path | None:
    """Generate speech from text. Returns path to .ogg file or None.

    The agent marks semantic breaks with newlines. Pyldon handles the rest:
    splitting, sequential generation, concatenation.
    """
    if not TTS_ENABLED:
        logger.warning("TTS not configured (QWEN3_TTS_BASE_URL not set)")
        return None

    voice = voice or TTS_VOICE
    language = language or TTS_LANGUAGE
    chunks = _split_into_chunks(text)

    if not chunks:
        logger.warning("TTS: empty text, nothing to generate")
        return None

    total_chars = sum(len(c) for c in chunks)
    logger.info("TTS: {} chars, {} chunk(s), voice={}", total_chars, len(chunks), voice)

    # Generate sequentially with a generous timeout
    wav_paths: list[Path | None] = []
    async with httpx.AsyncClient(timeout=TTS_TIMEOUT) as client:
        for i, chunk in enumerate(chunks):
            wav = await _generate_chunk(client, chunk, voice, language, i, len(chunks))
            wav_paths.append(wav)

    # Collect results in order
    ordered_wavs = [p for p in wav_paths if p is not None]
    failed = len(chunks) - len(ordered_wavs)

    if not ordered_wavs:
        logger.error("TTS: all {} chunks failed", len(chunks))
        return None
    if failed > 0:
        logger.warning("TTS: {}/{} chunks failed, using successful ones", failed, len(chunks))

    try:
        ogg_path = _concatenate_wavs(ordered_wavs)
        if ogg_path and ogg_path.exists():
            logger.info("TTS: {} chars ({} chunks) -> {} ({:.1f}KB)",
                        total_chars, len(chunks), ogg_path.name, ogg_path.stat().st_size / 1024)
            return ogg_path
        return None
    finally:
        for p in ordered_wavs:
            p.unlink(missing_ok=True)
