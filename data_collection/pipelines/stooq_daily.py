"""Daily OHLCV pipeline (Stooq provider)."""

from __future__ import annotations

import argparse
import csv
import io
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from typing import Any

from data_collection.common.db import insert_raw_object
from data_collection.common.hashing import sha256_bytes
from data_collection.common.http import build_session, request_with_retries
from data_collection.common.symbols import get_config_symbols
from data_collection.common.storage import build_storage
from data_collection.common.settings import load_config
from data_collection.common.time_utils import parse_date, today_in_timezone
from data_collection.db.connection import get_connection
from data_collection.common.config import load_common_settings

_logger = logging.getLogger(__name__)
_thread_local = threading.local()


def _get_symbols(conn, asof_date: date) -> list[str]:
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT symbol FROM universe_snapshot WHERE asof_date = %s",
            (asof_date,),
        )
        return [row[0] for row in cursor.fetchall()]


def _chunk(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _stooq_symbol(symbol: str, suffix: str, symbol_map: dict[str, str]) -> str:
    mapped = symbol_map.get(symbol.upper(), symbol)
    base = mapped.strip().lower()
    if base.endswith(suffix):
        return base
    return f"{base}{suffix}"


def _fetch_stooq_csv(
    session,
    settings,
    base_url: str,
    endpoint: str,
    symbol: str,
) -> tuple[bytes, int]:
    params = {"s": symbol, "i": "d"}
    url = f"{base_url}{endpoint}"
    response = request_with_retries(session, "GET", url, settings=settings, params=params)
    return response.content, response.status_code


def _get_thread_session(settings):
    current = getattr(_thread_local, "session", None)
    current_key = getattr(_thread_local, "settings_key", None)
    settings_key = (
        settings.user_agent,
        settings.timeout_seconds,
        settings.max_retries,
        settings.backoff_seconds,
    )
    if current is None or current_key != settings_key:
        current = build_session(settings)
        _thread_local.session = current
        _thread_local.settings_key = settings_key
    return current


def _fetch_symbol(settings, base_url: str, endpoint: str, stooq_symbol: str) -> tuple[bytes, int]:
    session = _get_thread_session(settings)
    return _fetch_stooq_csv(session, settings, base_url, endpoint, stooq_symbol)


def _market_bar_has_raw_id(conn) -> bool:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'market_bar_daily'
              AND column_name = 'raw_id'
            """
        )
        return cursor.fetchone() is not None


def _upsert_bars(conn, rows: list[tuple[Any, ...]], has_raw_id: bool) -> None:
    if not rows:
        return
    with conn.cursor() as cursor:
        if has_raw_id:
            cursor.executemany(
                """
                INSERT INTO market_bar_daily
                    (symbol, date, open, high, low, close, volume, vwap, trade_count, raw_id, source)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (symbol, date)
                DO UPDATE SET
                    open = EXCLUDED.open,
                    high = EXCLUDED.high,
                    low = EXCLUDED.low,
                    close = EXCLUDED.close,
                    volume = EXCLUDED.volume,
                    vwap = EXCLUDED.vwap,
                    trade_count = EXCLUDED.trade_count,
                    raw_id = EXCLUDED.raw_id,
                    source = EXCLUDED.source
                """,
                rows,
            )
        else:
            cursor.executemany(
                """
                INSERT INTO market_bar_daily
                    (symbol, date, open, high, low, close, volume, vwap, trade_count, source)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (symbol, date)
                DO UPDATE SET
                    open = EXCLUDED.open,
                    high = EXCLUDED.high,
                    low = EXCLUDED.low,
                    close = EXCLUDED.close,
                    volume = EXCLUDED.volume,
                    vwap = EXCLUDED.vwap,
                    trade_count = EXCLUDED.trade_count,
                    source = EXCLUDED.source
                """,
                rows,
            )


def backfill(
    start: date,
    end: date,
    asof_date: date | None = None,
    symbols_override: list[str] | None = None,
    limit: int | None = None,
) -> None:
    config = load_config()
    tz_name = config["project"]["timezone"]
    asof_date = asof_date or today_in_timezone(tz_name)
    symbols_config, _, _ = get_config_symbols(config)

    provider = config.get("market_data", {}).get("provider", "stooq")
    if provider != "stooq":
        raise RuntimeError(f"Unsupported market_data provider: {provider}")

    stooq_cfg = config["stooq"]
    base_url = stooq_cfg["base_url"]
    endpoint = stooq_cfg["daily_endpoint"]
    suffix = stooq_cfg.get("symbol_suffix", ".us")
    symbol_map = {k.upper(): v for k, v in stooq_cfg.get("symbol_map", {}).items()}

    common = load_common_settings()
    storage = build_storage(common)
    concurrency = int(os.getenv("STOOQ_CONCURRENCY", "6"))
    batch_size_env = os.getenv("STOOQ_BATCH_SIZE")
    batch_offset_env = os.getenv("STOOQ_BATCH_OFFSET")
    batch_size = int(batch_size_env) if batch_size_env else None
    batch_offset = int(batch_offset_env) if batch_offset_env else None
    if batch_size is not None and batch_size <= 0:
        batch_size = None
    if batch_offset is not None and batch_offset < 0:
        batch_offset = None

    with get_connection() as conn:
        symbols = symbols_override or symbols_config or _get_symbols(conn, asof_date)
        if not symbols:
            raise RuntimeError(f"No symbols found for universe_snapshot {asof_date}")
        if limit:
            symbols = symbols[:limit]
        if batch_size is not None:
            offset = batch_offset or 0
            symbols = symbols[offset : offset + batch_size]

        _logger.info(
            "stooq_daily start: symbols=%s start=%s end=%s concurrency=%s batch_size=%s batch_offset=%s",
            len(symbols),
            start.isoformat(),
            end.isoformat(),
            concurrency,
            batch_size,
            batch_offset,
        )

        has_raw_id = _market_bar_has_raw_id(conn)

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            future_map = {}
            for symbol in symbols:
                stooq_symbol = _stooq_symbol(symbol, suffix, symbol_map)
                future = executor.submit(_fetch_symbol, common.http, base_url, endpoint, stooq_symbol)
                future_map[future] = (symbol, stooq_symbol)

            for future in as_completed(future_map):
                symbol, stooq_symbol = future_map[future]
                content, status_code = future.result()
                sha = sha256_bytes(content)
                object_key = f"stooq/bars/{stooq_symbol}/{asof_date.isoformat()}/{sha}.csv"
                stored = storage.write_bytes(object_key, content, content_type="text/csv")
                raw_id = insert_raw_object(
                    source="stooq_bars",
                    object_key=stored.object_key,
                    content_type="text/csv",
                    sha256=stored.sha256,
                    http_status=status_code,
                    meta={
                        "symbol": symbol,
                        "stooq_symbol": stooq_symbol,
                        "start": start.isoformat(),
                        "end": end.isoformat(),
                    },
                    conn=conn,
                )

                reader = csv.DictReader(io.StringIO(content.decode("utf-8")))
                bar_rows: list[tuple[Any, ...]] = []
                for row in reader:
                    if not row.get("Date"):
                        continue
                    bar_date = datetime.strptime(row["Date"], "%Y-%m-%d").date()
                    if bar_date < start or bar_date > end:
                        continue
                    if has_raw_id:
                        bar_rows.append(
                            (
                                symbol,
                                bar_date,
                                float(row["Open"]) if row.get("Open") else None,
                                float(row["High"]) if row.get("High") else None,
                                float(row["Low"]) if row.get("Low") else None,
                                float(row["Close"]) if row.get("Close") else None,
                                int(float(row["Volume"])) if row.get("Volume") else None,
                                None,
                                None,
                                raw_id,
                                "stooq",
                            )
                        )
                    else:
                        bar_rows.append(
                            (
                                symbol,
                                bar_date,
                                float(row["Open"]) if row.get("Open") else None,
                                float(row["High"]) if row.get("High") else None,
                                float(row["Low"]) if row.get("Low") else None,
                                float(row["Close"]) if row.get("Close") else None,
                                int(float(row["Volume"])) if row.get("Volume") else None,
                                None,
                                None,
                                "stooq",
                            )
                        )

                _upsert_bars(conn, bar_rows, has_raw_id)
                conn.commit()
                _logger.info("stooq_daily symbol=%s rows=%s", symbol, len(bar_rows))

        conn.commit()


def main() -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Daily OHLCV pipeline (Stooq)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    backfill_parser = subparsers.add_parser("backfill", help="Backfill daily bars")
    backfill_parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    backfill_parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    backfill_parser.add_argument("--asof-date", help="Universe snapshot date (YYYY-MM-DD)")
    backfill_parser.add_argument("--symbols", help="Comma-separated symbol list override")
    backfill_parser.add_argument("--limit", type=int, help="Limit number of symbols")

    args = parser.parse_args()
    if args.command == "backfill":
        asof_date = parse_date(args.asof_date) if args.asof_date else None
        symbols_override = [s.strip() for s in args.symbols.split(",")] if args.symbols else None
        backfill(
            parse_date(args.start),
            parse_date(args.end),
            asof_date=asof_date,
            symbols_override=symbols_override,
            limit=args.limit,
        )


if __name__ == "__main__":
    main()
