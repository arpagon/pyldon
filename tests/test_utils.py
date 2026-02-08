"""Tests for Pyldon utils module."""

import json
from pathlib import Path

from pyldon.utils import load_json, save_json


def test_load_json_missing_file(tmp_path: Path):
    result = load_json(tmp_path / "nonexistent.json", {"key": "default"})
    assert result == {"key": "default"}


def test_load_json_existing_file(tmp_path: Path):
    data = {"hello": "world", "count": 42}
    path = tmp_path / "test.json"
    path.write_text(json.dumps(data))

    result = load_json(path, {})
    assert result == data


def test_load_json_invalid_json(tmp_path: Path):
    path = tmp_path / "bad.json"
    path.write_text("not valid json{{{")

    result = load_json(path, "fallback")
    assert result == "fallback"


def test_save_json_creates_dirs(tmp_path: Path):
    path = tmp_path / "nested" / "dir" / "file.json"
    data = {"key": "value"}

    save_json(path, data)

    assert path.exists()
    loaded = json.loads(path.read_text())
    assert loaded == data


def test_save_json_overwrites(tmp_path: Path):
    path = tmp_path / "test.json"
    save_json(path, {"first": True})
    save_json(path, {"second": True})

    loaded = json.loads(path.read_text())
    assert loaded == {"second": True}
