"""Wait for Postgres to be ready."""

from __future__ import annotations

import time

import psycopg

from data_collection.common.db import load_database_settings


def wait_for_postgres(retries: int = 20, delay: float = 1.5) -> None:
    settings = load_database_settings()
    last_error: Exception | None = None
    for _ in range(retries):
        try:
            with psycopg.connect(settings.url):
                return
        except Exception as exc:
            last_error = exc
            time.sleep(delay)
    raise RuntimeError("Postgres not ready") from last_error


if __name__ == "__main__":
    wait_for_postgres()
