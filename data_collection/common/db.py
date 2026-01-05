"""Postgres helpers for pipeline ingestion."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Iterable

import psycopg


class DatabaseError(RuntimeError):
    """Raised when database configuration or operations fail."""


@dataclass(frozen=True)
class DatabaseSettings:
    url: str


def _build_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if url:
        return url.replace("postgresql+psycopg://", "postgresql://")

    user = os.getenv("POSTGRES_USER")
    password = os.getenv("POSTGRES_PASSWORD")
    db_name = os.getenv("POSTGRES_DB")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")

    if not all([user, password, db_name]):
        raise DatabaseError("Set DATABASE_URL or POSTGRES_USER/POSTGRES_PASSWORD/POSTGRES_DB")

    return f"postgresql://{user}:{password}@{host}:{port}/{db_name}"


def load_database_settings() -> DatabaseSettings:
    return DatabaseSettings(url=_build_database_url())


def insert_raw_object(
    *,
    source: str,
    object_key: str,
    content_type: str | None,
    sha256: str,
    http_status: int | None,
    meta: dict[str, Any] | None = None,
    conn: psycopg.Connection | None = None,
) -> int:
    """
    Legacy function - raw_object table has been removed.
    Returns a dummy ID for backward compatibility with pipelines.
    """
    # No-op: raw_object table removed, return dummy ID
    return 0
