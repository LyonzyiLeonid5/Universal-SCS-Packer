"""Persistent local settings shared by the packer GUI and CLI."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys


FILE_NAME = "packer_settings.json"


def application_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def settings_path() -> Path:
    return application_directory() / FILE_NAME


def defaults() -> dict[str, object]:
    return {
        "language": "en",
        "max_threads": min(32, max(1, os.cpu_count() or 1)),
    }


def load_settings() -> dict[str, object]:
    result = defaults()
    try:
        loaded = json.loads(settings_path().read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            result.update({key: loaded[key] for key in result if key in loaded})
    except (OSError, ValueError):
        pass
    return result


def save_settings(values: dict[str, object]) -> None:
    target = settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)


def positive_int(value: object, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def configured_workers(default: int) -> int:
    settings = load_settings()
    workers = positive_int(os.environ.get("UNIVERSAL_SCS_MAX_THREADS"), 0)
    workers = workers or positive_int(settings.get("max_threads"), default) or default
    return max(1, workers)
