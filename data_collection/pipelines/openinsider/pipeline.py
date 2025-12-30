"""OpenInsider raw scraping ingestion pipeline."""

from __future__ import annotations

import hashlib

from data_collection.common.config import get_env_list, load_common_settings
from data_collection.common.db import insert_raw_object
from data_collection.common.http import build_session, request_with_retries
from data_collection.common.paths import utc_date_str
from data_collection.common.storage import build_storage


def run() -> None:
    common = load_common_settings()
    storage = build_storage(common)
    session = build_session(common.http)

    urls = get_env_list("OPENINSIDER_URLS")

    for url in urls:
        response = request_with_retries(session, "GET", url, settings=common.http)
        content = response.content
        sha = hashlib.sha256(content).hexdigest()
        object_key = f"openinsider/{utc_date_str()}/{sha}.html"
        stored = storage.write_bytes(object_key, content, content_type="text/html")
        insert_raw_object(
            source="openinsider",
            object_key=stored.object_key,
            content_type="text/html",
            sha256=stored.sha256,
            http_status=response.status_code,
            meta={"url": url},
        )


if __name__ == "__main__":
    run()
