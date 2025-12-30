"""USASpending API ingestion pipeline."""

from __future__ import annotations

import json
import os

from data_collection.common.config import load_common_settings, require_env_set
from data_collection.common.db import insert_raw_object
from data_collection.common.hashing import sha256_bytes
from data_collection.common.http import build_session, request_with_retries
from data_collection.common.paths import utc_date_str
from data_collection.common.storage import build_storage

USASPENDING_BASE = "https://api.usaspending.gov"


def run() -> None:
    common = load_common_settings()
    storage = build_storage(common)

    require_env_set(["USASPENDING_ENDPOINT"])
    endpoint = os.environ["USASPENDING_ENDPOINT"].lstrip("/")
    method = os.getenv("USASPENDING_METHOD")
    params_raw = os.getenv("USASPENDING_PARAMS_JSON")
    payload_raw = os.getenv("USASPENDING_PAYLOAD_JSON")

    params = json.loads(params_raw) if params_raw else None
    payload = json.loads(payload_raw) if payload_raw else None

    if not method:
        method = "POST" if payload else "GET"

    session = build_session(common.http)
    url = f"{USASPENDING_BASE}/{endpoint}"

    response = request_with_retries(
        session,
        method,
        url,
        settings=common.http,
        params=params,
        json=payload,
    )

    content_type = response.headers.get("Content-Type", "application/json")
    raw_bytes = response.content
    sha = sha256_bytes(raw_bytes)
    object_key = f"usaspending/{endpoint}/{utc_date_str()}/{sha}.json"
    stored = storage.write_bytes(object_key, raw_bytes, content_type=content_type)
    insert_raw_object(
        source="usaspending",
        object_key=stored.object_key,
        content_type=content_type,
        sha256=stored.sha256,
        http_status=response.status_code,
        meta={"endpoint": endpoint, "method": method, "params": params, "payload": payload},
    )


if __name__ == "__main__":
    run()
