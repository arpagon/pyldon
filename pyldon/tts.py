"""Text-to-speech via Qwen3-TTS OpenAI-compatible API."""

from __future__ import annotations

import tempfile
from pathlib import Path

import httpx
from loguru import logger

from pyldon.config import TTS_API_KEY, TTS_BASE_URL, TTS_ENABLED, TTS_LANGUAGE, TTS_VOICE


async def generate_speech(
    text: str,
    voice: str | None = None,
    language: str | None = None,
) -> Path | None:
    """Generate speech audio from text. Returns path to .ogg file or None on failure."""
    if not TTS_ENABLED:
        logger.warning("TTS not configured (QWEN3_TTS_BASE_URL not set)")
        return None

    voice = voice or TTS_VOICE
    language = language or TTS_LANGUAGE

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            headers = {"Content-Type": "application/json"}
            if TTS_API_KEY:
                headers["Authorization"] = f"Bearer {TTS_API_KEY}"

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
                logger.error("TTS API error: {} {}", resp.status_code, resp.text[:200])
                return None

            # Save wav, convert to ogg
            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            tmp.write(resp.content)
            tmp.close()

            ogg_path = Path(tmp.name).with_suffix(".ogg")
            import subprocess

            result = subprocess.run(
                ["ffmpeg", "-y", "-i", tmp.name, "-c:a", "libopus", "-b:a", "64k", str(ogg_path)],
                capture_output=True,
                timeout=30,
            )
            Path(tmp.name).unlink(missing_ok=True)

            if result.returncode != 0:
                logger.error("ffmpeg conversion failed: {}", result.stderr.decode()[:200])
                return None

            logger.info("TTS generated: {} chars -> {} ({:.1f}KB)", len(text), ogg_path.name, ogg_path.stat().st_size / 1024)
            return ogg_path

    except Exception as e:
        logger.error("TTS generation failed: {}", e)
        return None
