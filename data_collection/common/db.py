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
    payload_meta = psycopg.types.json.Jsonb(meta) if meta is not None else None
    if conn is None:
        settings = load_database_settings()
        with psycopg.connect(settings.url) as local_conn:
            raw_id = _insert_raw_object(local_conn, source, object_key, content_type, sha256, http_status, payload_meta)
            local_conn.commit()
            return raw_id

    raw_id = _insert_raw_object(conn, source, object_key, content_type, sha256, http_status, payload_meta)
    return raw_id


def _insert_raw_object(
    conn: psycopg.Connection,
    source: str,
    object_key: str,
    content_type: str | None,
    sha256: str,
    http_status: int | None,
    payload_meta: psycopg.types.json.Jsonb | None,
) -> int:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO raw_object (source, object_key, content_type, sha256, http_status, meta)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING raw_id
            """,
            (source, object_key, content_type, sha256, http_status, payload_meta),
        )
        raw_id = cursor.fetchone()[0]
    return int(raw_id)
