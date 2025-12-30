"""Environment-driven configuration helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable


class ConfigError(RuntimeError):
    """Raised when required configuration is missing."""


def get_env(key: str, required: bool = True, default: str | None = None) -> str | None:
    value = os.getenv(key, default)
    if required and not value:
        raise ConfigError(f"Missing required environment variable: {key}")
    return value


def get_env_list(key: str, required: bool = True, delimiter: str = ",") -> list[str]:
    value = os.getenv(key)
    if not value:
        if required:
            raise ConfigError(f"Missing required environment variable: {key}")
        return []
    return [item.strip() for item in value.split(delimiter) if item.strip()]


@dataclass(frozen=True)
class RawStorageSettings:
    backend: str
    local_path: str
    s3_bucket: str | None
    s3_prefix: str | None
    s3_endpoint_url: str | None


@dataclass(frozen=True)
class HttpSettings:
    user_agent: str
    timeout_seconds: int
    max_retries: int
    backoff_seconds: float


@dataclass(frozen=True)
class CommonSettings:
    raw_storage: RawStorageSettings
    http: HttpSettings


def load_common_settings() -> CommonSettings:
    backend = os.getenv("RAW_STORAGE", "local").strip().lower()
    local_path = os.getenv("RAW_STORAGE_PATH", "data_collection/raw")
    s3_bucket = os.getenv("RAW_S3_BUCKET")
    s3_prefix = os.getenv("RAW_S3_PREFIX", "raw")
    s3_endpoint_url = os.getenv("RAW_S3_ENDPOINT_URL")

    user_agent = os.getenv("DATA_COLLECTION_USER_AGENT", "EiqoraDataCollector/0.1")

    http = HttpSettings(
        user_agent=user_agent,
        timeout_seconds=int(os.getenv("HTTP_TIMEOUT_SECONDS", "30")),
        max_retries=int(os.getenv("HTTP_MAX_RETRIES", "5")),
        backoff_seconds=float(os.getenv("HTTP_BACKOFF_SECONDS", "1.5")),
    )

    return CommonSettings(
        raw_storage=RawStorageSettings(
            backend=backend,
            local_path=local_path,
            s3_bucket=s3_bucket,
            s3_prefix=s3_prefix,
            s3_endpoint_url=s3_endpoint_url,
        ),
        http=http,
    )


def require_env_set(keys: Iterable[str]) -> None:
    missing = [key for key in keys if not os.getenv(key)]
    if missing:
        raise ConfigError(f"Missing required environment variables: {', '.join(missing)}")
