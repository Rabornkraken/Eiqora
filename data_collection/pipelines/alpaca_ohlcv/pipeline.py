"""Alpaca OHLCV ingestion pipeline."""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

from data_collection.common.config import ConfigError, get_env, get_env_list, load_common_settings
from data_collection.common.db import insert_raw_object
from data_collection.common.hashing import sha256_bytes
from data_collection.common.http import build_session, request_with_retries
from data_collection.common.paths import utc_date_str
from data_collection.common.storage import build_storage

API_URL = "https://data.alpaca.markets/v2/stocks/bars"


def _parse_iso_datetime(value: str) -> str:
    cleaned = value.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned.replace("Z", "+00:00")
    datetime.fromisoformat(cleaned)
    return cleaned


def _fetch_page(
    session,
    settings,
    symbols: list[str],
    timeframe: str,
    start: str,
    end: str,
    limit: int,
    page_token: str | None,
) -> tuple[dict[str, Any], int]:
    params: dict[str, Any] = {
        "symbols": ",".join(symbols),
        "timeframe": timeframe,
        "start": start,
        "end": end,
        "limit": limit,
        "adjustment": os.getenv("ALPACA_ADJUSTMENT", "raw"),
    }
    if page_token:
        params["page_token"] = page_token
    response = request_with_retries(
        session,
        "GET",
        API_URL,
        settings=settings,
        params=params,
    )
    return response.json(), response.status_code


def run() -> None:
    common = load_common_settings()
    storage = build_storage(common)

    api_key = get_env("ALPACA_API_KEY")
    api_secret = get_env("ALPACA_API_SECRET")
    symbols = get_env_list("ALPACA_SYMBOLS")

    timeframe = get_env("ALPACA_TIMEFRAME", required=False, default="1Day")
    start_raw = get_env("ALPACA_START")
    end_raw = get_env("ALPACA_END")
    if not start_raw or not end_raw:
        raise ConfigError("ALPACA_START and ALPACA_END are required (ISO-8601)")

    start = _parse_iso_datetime(start_raw)
    end = _parse_iso_datetime(end_raw)

    session = build_session(common.http)
    session.headers.update({
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": api_secret,
    })

    limit = int(os.getenv("ALPACA_LIMIT", "10000"))

    page_token: str | None = None
    while True:
        payload, status_code = _fetch_page(
            session, common.http, symbols, timeframe, start, end, limit, page_token
        )
        bars_by_symbol = payload.get("bars", {})
        fetched_at = utc_date_str()

        for symbol, bars in bars_by_symbol.items():
            content = {
                "symbol": symbol,
                "timeframe": timeframe,
                "start": start,
                "end": end,
                "bars": bars,
            }
            raw_bytes = json.dumps(content, ensure_ascii=True).encode("utf-8")
            sha = sha256_bytes(raw_bytes)
            object_key = f"alpaca/bars/{timeframe}/{symbol}/{fetched_at}/{sha}.json"
            stored = storage.write_bytes(object_key, raw_bytes, content_type="application/json")
            insert_raw_object(
                source="alpaca",
                object_key=stored.object_key,
                content_type="application/json",
                sha256=stored.sha256,
                http_status=status_code,
                meta={\n                    \"symbol\": symbol,\n                    \"timeframe\": timeframe,\n                    \"start\": start,\n                    \"end\": end,\n                    \"symbols\": symbols,\n                    \"limit\": limit,\n                },
            )

        page_token = payload.get("next_page_token")
        if not page_token:
            break


if __name__ == "__main__":
    run()
