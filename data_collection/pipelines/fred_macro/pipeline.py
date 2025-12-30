"""FRED macro time series ingestion pipeline."""

from __future__ import annotations

import json
import os

from data_collection.common.config import get_env_list, load_common_settings, require_env_set
from data_collection.common.db import insert_raw_object
from data_collection.common.hashing import sha256_bytes
from data_collection.common.http import build_session, request_with_retries
from data_collection.common.paths import utc_date_str
from data_collection.common.storage import build_storage

FRED_URL = "https://api.stlouisfed.org/fred/series/observations"


def run() -> None:
    common = load_common_settings()
    storage = build_storage(common)

    require_env_set(["FRED_API_KEY", "FRED_SERIES_IDS"])
    api_key = os.environ["FRED_API_KEY"]
    series_ids = get_env_list("FRED_SERIES_IDS")

    session = build_session(common.http)

    observation_start = os.getenv("FRED_START")
    observation_end = os.getenv("FRED_END")

    for series_id in series_ids:
        params = {
            "series_id": series_id,
            "api_key": api_key,
            "file_type": "json",
        }
        if observation_start:
            params["observation_start"] = observation_start
        if observation_end:
            params["observation_end"] = observation_end

        response = request_with_retries(session, "GET", FRED_URL, settings=common.http, params=params)
        payload = response.json()
        raw_bytes = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        sha = sha256_bytes(raw_bytes)
        object_key = f"fred/{series_id}/{utc_date_str()}/{sha}.json"
        stored = storage.write_bytes(object_key, raw_bytes, content_type="application/json")
        insert_raw_object(
            source="fred",
            object_key=stored.object_key,
            content_type="application/json",
            sha256=stored.sha256,
            http_status=response.status_code,
            meta={"series_id": series_id, "params": params},
        )


if __name__ == "__main__":
    run()
