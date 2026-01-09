"""Postgres helpers for pipeline ingestion."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Iterable
from pathlib import Path

import psycopg
from psycopg import sql
from dotenv import load_dotenv

# Load .env file from project root
_project_root = Path(__file__).parent.parent.parent
load_dotenv(_project_root / ".env")


class DatabaseError(RuntimeError):
    """Raised when database configuration or operations fail."""


@dataclass(frozen=True)
class DatabaseSettings:
    url: str


def _build_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if url:
        return url.replace("postgresql+psycopg://", "postgresql://")

    # Use defaults matching eiqora_v2/config/settings.py
    user = os.getenv("POSTGRES_USER", "postgres")
    password = os.getenv("POSTGRES_PASSWORD", "postgres")
    db_name = os.getenv("POSTGRES_DB", "finance")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")

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


def notify_ingest(
    conn: psycopg.Connection,
    channel: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """Emit a Postgres NOTIFY message with a JSON payload."""
    message = json.dumps(payload or {}, ensure_ascii=True, default=str)
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL("NOTIFY {}, {}").format(
                sql.Identifier(channel),
                sql.Literal(message),
            )
        )
