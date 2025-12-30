"""Raw storage backends (local filesystem or S3-compatible)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


from data_collection.common.config import CommonSettings
from data_collection.common.hashing import sha256_bytes


class StorageError(RuntimeError):
    """Raised when storage operations fail."""


@dataclass(frozen=True)
class StoredObject:
    object_key: str
    sha256: str
    bytes_written: int


class StorageBackend:
    def write_bytes(self, object_key: str, data: bytes, content_type: str | None = None) -> StoredObject:
        raise NotImplementedError

    def read_bytes(self, object_key: str) -> bytes:
        raise NotImplementedError

    def write_json(self, object_key: str, payload: Any) -> StoredObject:
        data = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        return self.write_bytes(object_key, data, content_type="application/json")

    def write_text(self, object_key: str, text: str, content_type: str | None = None) -> StoredObject:
        return self.write_bytes(object_key, text.encode("utf-8"), content_type=content_type)


class LocalStorage(StorageBackend):
    def __init__(self, base_path: str) -> None:
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

    def write_bytes(self, object_key: str, data: bytes, content_type: str | None = None) -> StoredObject:
        target = self.base_path / object_key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return StoredObject(object_key=object_key, sha256=sha256_bytes(data), bytes_written=len(data))

    def read_bytes(self, object_key: str) -> bytes:
        target = self.base_path / object_key
        return target.read_bytes()


class S3Storage(StorageBackend):
    def __init__(
        self,
        bucket: str,
        prefix: str | None = None,
        endpoint_url: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
    ) -> None:
        if not bucket:
            raise StorageError("RAW_S3_BUCKET is required for S3 storage")
        import boto3

        self.bucket = bucket
        self.prefix = (prefix or "").strip("/")
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )

    def _full_key(self, object_key: str) -> str:
        if self.prefix:
            return f"{self.prefix}/{object_key.lstrip('/')}"
        return object_key.lstrip("/")

    def write_bytes(self, object_key: str, data: bytes, content_type: str | None = None) -> StoredObject:
        key = self._full_key(object_key)
        extra_args = {"ContentType": content_type} if content_type else None
        if extra_args:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=data, **extra_args)
        else:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=data)
        return StoredObject(object_key=object_key, sha256=sha256_bytes(data), bytes_written=len(data))

    def read_bytes(self, object_key: str) -> bytes:
        key = self._full_key(object_key)
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        return response["Body"].read()


def build_storage(settings: CommonSettings) -> StorageBackend:
    backend = settings.raw_storage.backend
    if backend == "s3":
        access_key = os.getenv("MINIO_ACCESS_KEY") or os.getenv("AWS_ACCESS_KEY_ID")
        secret_key = os.getenv("MINIO_SECRET_KEY") or os.getenv("AWS_SECRET_ACCESS_KEY")
        return S3Storage(
            bucket=settings.raw_storage.s3_bucket or "",
            prefix=settings.raw_storage.s3_prefix,
            endpoint_url=settings.raw_storage.s3_endpoint_url,
            access_key=access_key,
            secret_key=secret_key,
        )
    if backend == "local":
        return LocalStorage(settings.raw_storage.local_path)
    raise StorageError(f"Unsupported RAW_STORAGE backend: {backend}")
