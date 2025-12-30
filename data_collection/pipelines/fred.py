"""FRED macro series ingestion pipeline."""

from __future__ import annotations

import argparse
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta

from data_collection.common.db import insert_raw_object
from data_collection.common.hashing import sha256_bytes
from data_collection.common.http import build_session, request_with_retries
from data_collection.common.storage import build_storage
from data_collection.common.settings import get_env_value, load_config
from data_collection.common.time_utils import parse_date
from data_collection.db.connection import get_connection
import psycopg
from data_collection.common.config import load_common_settings

FRED_URL = "https://api.stlouisfed.org/fred/series/observations"

_logger = logging.getLogger(__name__)


def _upsert_observations(conn, rows: list[tuple]) -> None:
    if not rows:
        return
    with conn.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO macro_observation
                (source_system, series_id, date, value, raw_id, meta)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_system, series_id, date)
            DO UPDATE SET value = EXCLUDED.value, raw_id = EXCLUDED.raw_id, meta = EXCLUDED.meta
            """,
            rows,
        )


def _fetch_series(session, settings, api_key: str, series_id: str, start: date | None, end: date | None) -> dict:
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
    }
    if start:
        params["observation_start"] = start.isoformat()
    if end:
        params["observation_end"] = end.isoformat()
    response = request_with_retries(session, "GET", FRED_URL, settings=settings, params=params)
    return {"payload": response.json(), "status_code": response.status_code, "params": params, "series_id": series_id}


def run(start: date | None = None, end: date | None = None, recent_days: int = 30) -> None:
    config = load_config()
    fred_cfg = config["macro"]["fred"]
    api_key = get_env_value(fred_cfg["api_key_env"], required=True)
    series_ids = fred_cfg["series_ids"]

    if start is None and end is None:
        end = date.today()
        start = end - timedelta(days=recent_days)

    common = load_common_settings()
    storage = build_storage(common)
    session = build_session(common.http)
    concurrency = int(os.getenv("FRED_CONCURRENCY", "4"))

    with get_connection() as conn:
        _logger.info(
            "fred start: series=%s start=%s end=%s concurrency=%s",
            len(series_ids),
            start.isoformat() if start else None,
            end.isoformat() if end else None,
            concurrency,
        )
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            future_map = {
                executor.submit(_fetch_series, session, common.http, api_key, series_id, start, end): series_id
                for series_id in series_ids
            }
            for future in as_completed(future_map):
                result = future.result()
                series_id = result["series_id"]
                payload = result["payload"]
                status_code = result["status_code"]
                params = result["params"]

                raw_bytes = json.dumps(payload, ensure_ascii=True).encode("utf-8")
                sha = sha256_bytes(raw_bytes)
                object_key = f"fred/{series_id}/{date.today().isoformat()}/{sha}.json"
                stored = storage.write_bytes(object_key, raw_bytes, content_type="application/json")
                raw_id = insert_raw_object(
                    source="fred",
                    object_key=stored.object_key,
                    content_type="application/json",
                    sha256=stored.sha256,
                    http_status=status_code,
                    meta={"series_id": series_id, "params": params},
                    conn=conn,
                )

                rows = []
                for obs in payload.get("observations", []):
                    value = obs.get("value")
                    if value is None or value == ".":
                        continue
                    obs_date = datetime.strptime(obs["date"], "%Y-%m-%d").date()
                    rows.append(
                        (
                            "fred",
                            series_id,
                            obs_date,
                            float(value),
                            raw_id,
                            psycopg.types.json.Jsonb(
                                {"realtime_start": obs.get("realtime_start"), "realtime_end": obs.get("realtime_end")}
                            ),
                        )
                    )

                _upsert_observations(conn, rows)
                conn.commit()
                _logger.info("fred series=%s rows=%s", series_id, len(rows))

        conn.commit()


def main() -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="FRED macro pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run FRED ingestion")
    run_parser.add_argument("--start", help="Start date (YYYY-MM-DD)")
    run_parser.add_argument("--end", help="End date (YYYY-MM-DD)")
    run_parser.add_argument("--recent-days", type=int, default=30)

    args = parser.parse_args()
    start = parse_date(args.start) if args.start else None
    end = parse_date(args.end) if args.end else None
    run(start=start, end=end, recent_days=args.recent_days)


if __name__ == "__main__":
    main()
