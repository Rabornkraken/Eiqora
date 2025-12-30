"""SEC fails-to-deliver dataset downloader."""

from __future__ import annotations

import os

from data_collection.common.config import get_env_list, load_common_settings, require_env_set
from data_collection.common.db import insert_raw_object
from data_collection.common.hashing import sha256_bytes
from data_collection.common.http import build_session, request_with_retries
from data_collection.common.paths import utc_date_str
from data_collection.common.storage import build_storage

FTD_URL_TEMPLATE = "https://www.sec.gov/files/data/fails-deliver-data/cnsfails{yyyymm}.zip"


def run() -> None:
    common = load_common_settings()
    storage = build_storage(common)

    require_env_set(["SEC_USER_AGENT", "SEC_FTD_MONTHS"])
    months = get_env_list("SEC_FTD_MONTHS")

    session = build_session(common.http)
    session.headers.update({"User-Agent": os.environ["SEC_USER_AGENT"]})

    for yyyymm in months:
        url = FTD_URL_TEMPLATE.format(yyyymm=yyyymm)
        response = request_with_retries(session, "GET", url, settings=common.http)
        content = response.content
        sha = sha256_bytes(content)
        object_key = f"ftd/{yyyymm}/{utc_date_str()}/{sha}.zip"
        stored = storage.write_bytes(object_key, content, content_type="application/zip")
        insert_raw_object(
            source="sec_ftd",
            object_key=stored.object_key,
            content_type="application/zip",
            sha256=stored.sha256,
            http_status=response.status_code,
            meta={"yyyymm": yyyymm, "url": url},
        )


if __name__ == "__main__":
    run()
