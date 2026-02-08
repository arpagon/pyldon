"""Utility functions for Pyldon.

Migrated from NanoClaw src/utils.ts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypeVar

from loguru import logger

T = TypeVar("T")


def load_json(file_path: str | Path, default: T) -> T:
    """Load JSON from a file, returning default on error or missing file."""
    file_path = Path(file_path)
    try:
        if file_path.exists():
            return json.loads(file_path.read_text(encoding="utf-8"))
    except Exception:
        logger.debug(f"Failed to load JSON from {file_path}, using default")
    return default


def save_json(file_path: str | Path, data: Any) -> None:
    """Save data as JSON to a file, creating parent directories as needed."""
    file_path = Path(file_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
