"""Database connection utilities."""

from __future__ import annotations

import psycopg

from data_collection.common.db import load_database_settings


def get_connection() -> psycopg.Connection:
    settings = load_database_settings()
    return psycopg.connect(settings.url)
