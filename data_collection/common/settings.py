"""Load YAML configuration for data collection."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class SettingsError(RuntimeError):
    """Raised when configuration cannot be loaded."""


DEFAULT_CONFIG_PATH = Path("data_collection/config.yaml")


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not config_path.exists():
        raise SettingsError(f"Missing config file: {config_path}")
    with config_path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def get_env_value(env_key: str, required: bool = True) -> str | None:
    value = os.getenv(env_key)
    if required and not value:
        raise SettingsError(f"Missing required environment variable: {env_key}")
    return value
