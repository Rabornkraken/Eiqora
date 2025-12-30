"""Hashing helpers for raw payloads."""

from __future__ import annotations

import hashlib


def sha256_bytes(data: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(data)
    return digest.hexdigest()
