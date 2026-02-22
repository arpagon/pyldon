#!/usr/bin/env python3
"""Pyldon STT transcriber — runs inside GPU Docker container.

Uses onnx-asr with Parakeet TDT v3 ONNX model (no PyTorch needed).

Usage:
  python3 transcribe.py /path/to/audio.wav
  cat audio.ogg | python3 transcribe.py --stdin
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time

MODEL_NAME = os.environ.get("PYLDON_STT_MODEL", "nemo-parakeet-tdt-0.6b-v3")


def convert_to_wav(input_path: str, output_path: str) -> bool:
    """Convert any audio format to WAV 16kHz mono PCM_16 using ffmpeg."""
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", input_path,
                "-ar", "16000", "-ac", "1", "-sample_fmt", "s16",
                "-f", "wav", output_path,
            ],
            capture_output=True,
            check=True,
            timeout=30,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"[stt] ffmpeg conversion failed: {e}", file=sys.stderr)
        return False


def main() -> None:
    start = time.time()

    with tempfile.TemporaryDirectory() as tmpdir:
        if "--stdin" in sys.argv:
            input_path = os.path.join(tmpdir, "input_audio")
            with open(input_path, "wb") as f:
                f.write(sys.stdin.buffer.read())
        elif len(sys.argv) > 1 and sys.argv[1] != "--stdin":
            input_path = sys.argv[1]
        else:
            print(json.dumps({"error": "No input. Use: transcribe.py <file> or --stdin"}))
            sys.exit(1)

        if not os.path.exists(input_path):
            print(json.dumps({"error": f"File not found: {input_path}"}))
            sys.exit(1)

        file_size = os.path.getsize(input_path)
        print(f"[stt] Input: {file_size} bytes", file=sys.stderr)

        # Convert to WAV 16kHz mono PCM_16
        wav_path = os.path.join(tmpdir, "audio.wav")
        if not convert_to_wav(input_path, wav_path):
            print(json.dumps({"error": "Failed to convert audio to WAV"}))
            sys.exit(1)

        wav_size = os.path.getsize(wav_path)
        print(f"[stt] WAV: {wav_size} bytes", file=sys.stderr)

        # Load model and transcribe
        print(f"[stt] Loading model: {MODEL_NAME}", file=sys.stderr)
        import onnx_asr

        model = onnx_asr.load_model(MODEL_NAME)

        load_time = time.time() - start
        print(f"[stt] Model loaded in {load_time:.1f}s", file=sys.stderr)

        result = model.recognize(wav_path)
        text = str(result).strip() if result else ""

        total_time = time.time() - start
        print(f"[stt] Transcribed in {total_time:.1f}s: {text[:80]}...", file=sys.stderr)

        output = {
            "text": text,
            "model": MODEL_NAME,
            "duration_s": round(total_time, 2),
            "input_bytes": file_size,
        }
        print(json.dumps(output))


if __name__ == "__main__":
    main()
