"""
Daily OHLCV pipeline (yfinance provider).

Drop-in replacement for `stooq_daily.py` after Stooq started blocking
unauthenticated requests in early 2026. yfinance is free, reliable, and
already a transitive dependency (used by `vix.py`, the news pipeline,
ticker_profile, etc.) — so this introduces no new external dependency.

Public surface mirrors `stooq_daily.py` so the scheduler can swap providers
with a single import change:

    python -m data_collection.pipelines.yf_daily run
    python -m data_collection.pipelines.yf_daily backfill --start 2021-01-01 --end 2026-04-08
    python -m data_collection.pipelines.yf_daily backfill --start 2025-01-01 --end 2026-04-08 --symbols NVDA,QCOM,T

The pipeline:
  1. Resolves the symbol universe via `get_config_symbols(config)` (which
     itself reads from `eiqora_v2.config.portfolios.get_master_universe()`).
  2. Fetches per-symbol daily history from yfinance (sequential requests
     with a small delay to avoid rate-limit storms).
  3. Stores raw JSON bytes for audit via `insert_raw_object`.
  4. Upserts (symbol, date, OHLCV) into `market_bar_daily` with source='yfinance'.
  5. Sends a `NOTIFY eiqora_ingest` event so the live pipeline can pick up the
     ingest and rebuild the watchlist.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import date, datetime, timedelta
from typing import Any

from data_collection.common.config import load_common_settings
from data_collection.common.db import insert_raw_object, notify_ingest
from data_collection.common.hashing import sha256_bytes
from data_collection.common.settings import load_config
from data_collection.common.storage import build_storage
from data_collection.common.symbols import get_config_symbols
from data_collection.common.time_utils import parse_date, today_in_timezone
from data_collection.db.connection import get_connection

_logger = logging.getLogger(__name__)

NOTIFY_CHANNEL = os.getenv("EIQORA_INGEST_CHANNEL", "eiqora_ingest")


def _get_symbols_from_snapshot(conn, asof_date: date) -> list[str]:
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT symbol FROM universe_snapshot WHERE asof_date = %s",
            (asof_date,),
        )
        return [row[0] for row in cursor.fetchall()]


def _merge_symbols(primary: list[str], extra: list[str]) -> list[str]:
    """Merge two symbol lists preserving order, normalizing case, deduping."""
    merged: list[str] = []
    seen: set[str] = set()
    for symbol in list(primary) + list(extra):
        if not symbol:
            continue
        normalized = symbol.strip().upper()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        merged.append(normalized)
    return merged


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


def _to_yf_symbol(symbol: str) -> str:
    """
    Convert an internal ticker (e.g. BRK.B) to the yfinance format (BRK-B).
    yfinance uses dashes for share-class suffixes; everything else passes
    through unchanged.
    """
    return symbol.replace(".", "-")


def _fetch_yf_history(symbol: str, start: date, end_inclusive: date):
    """
    Fetch daily history from yfinance. Returns the pandas DataFrame.
    Empty DataFrame if no data or on error.
    """
    import yfinance as yf

    yf_symbol = _to_yf_symbol(symbol)
    try:
        ticker = yf.Ticker(yf_symbol)
        # auto_adjust=False keeps unadjusted OHLC; matches what Stooq used to return.
        return ticker.history(start=start, end=end_inclusive, auto_adjust=False)
    except Exception as exc:
        _logger.warning("yfinance fetch failed for %s (yf=%s): %s", symbol, yf_symbol, exc)
        return None


def backfill(
    start: date,
    end: date,
    asof_date: date | None = None,
    symbols_override: list[str] | None = None,
    limit: int | None = None,
) -> None:
    """
    Backfill daily bars for a date range.

    Args:
        start: First date (inclusive).
        end: Last date (inclusive).
        asof_date: Universe snapshot date (defaults to today in project tz).
        symbols_override: Optional explicit symbol list.
        limit: Optional cap on the number of symbols processed (useful for tests).
    """
    config = load_config()
    tz_name = config["project"]["timezone"]
    asof_date = asof_date or today_in_timezone(tz_name)
    symbols_config, _, _ = get_config_symbols(config)
    context_symbols = config.get("universe", {}).get("include_context_symbols", []) or []

    common = load_common_settings()
    storage = build_storage(common)

    with get_connection() as conn:
        symbols = symbols_override or symbols_config or _get_symbols_from_snapshot(conn, asof_date)
        if not symbols_override and context_symbols:
            symbols = _merge_symbols(symbols, list(context_symbols))
        if not symbols:
            raise RuntimeError(f"No symbols found for universe_snapshot {asof_date}")
        if limit:
            symbols = symbols[:limit]

        _logger.info(
            "yf_daily start: symbols=%s start=%s end=%s",
            len(symbols),
            start.isoformat(),
            end.isoformat(),
        )

        has_raw_id = _market_bar_has_raw_id(conn)
        end_inclusive = end + timedelta(days=1)  # yfinance end is exclusive

        successful = 0
        total_rows = 0
        for symbol in symbols:
            hist = _fetch_yf_history(symbol, start, end_inclusive)
            if hist is None or hist.empty:
                _logger.info("yf_daily symbol=%s rows=0 (no data)", symbol)
                continue

            # Build a JSON payload for the raw_object audit row
            payload = {
                "symbol": symbol,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "bars": [
                    {
                        "date": (idx.date() if hasattr(idx, "date") else idx).isoformat(),
                        "open": float(row["Open"]) if row.get("Open") is not None else None,
                        "high": float(row["High"]) if row.get("High") is not None else None,
                        "low": float(row["Low"]) if row.get("Low") is not None else None,
                        "close": float(row["Close"]) if row.get("Close") is not None else None,
                        "volume": int(row["Volume"]) if row.get("Volume") is not None else None,
                    }
                    for idx, row in hist.iterrows()
                ],
            }
            raw_bytes = json.dumps(payload, ensure_ascii=True).encode("utf-8")
            sha = sha256_bytes(raw_bytes)
            object_key = f"yfinance/bars/{symbol}/{asof_date.isoformat()}/{sha}.json"
            stored = storage.write_bytes(object_key, raw_bytes, content_type="application/json")
            raw_id = insert_raw_object(
                source="yfinance_bars",
                object_key=stored.object_key,
                content_type="application/json",
                sha256=stored.sha256,
                http_status=200,
                meta={
                    "symbol": symbol,
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                },
                conn=conn,
            )

            bar_rows: list[tuple[Any, ...]] = []
            for idx, row in hist.iterrows():
                bar_date = idx.date() if hasattr(idx, "date") else idx
                if bar_date < start or bar_date > end:
                    continue
                if has_raw_id:
                    bar_rows.append(
                        (
                            symbol,
                            bar_date,
                            float(row["Open"]) if row.get("Open") is not None else None,
                            float(row["High"]) if row.get("High") is not None else None,
                            float(row["Low"]) if row.get("Low") is not None else None,
                            float(row["Close"]) if row.get("Close") is not None else None,
                            int(row["Volume"]) if row.get("Volume") is not None else None,
                            None,  # vwap
                            None,  # trade_count
                            raw_id,
                            "yfinance",
                        )
                    )
                else:
                    bar_rows.append(
                        (
                            symbol,
                            bar_date,
                            float(row["Open"]) if row.get("Open") is not None else None,
                            float(row["High"]) if row.get("High") is not None else None,
                            float(row["Low"]) if row.get("Low") is not None else None,
                            float(row["Close"]) if row.get("Close") is not None else None,
                            int(row["Volume"]) if row.get("Volume") is not None else None,
                            None,
                            None,
                            "yfinance",
                        )
                    )

            _upsert_bars(conn, bar_rows, has_raw_id)
            conn.commit()
            successful += 1
            total_rows += len(bar_rows)
            _logger.info("yf_daily symbol=%s rows=%s", symbol, len(bar_rows))

        _logger.info(
            "yf_daily complete: %d/%d symbols ingested, %d total bars",
            successful, len(symbols), total_rows,
        )


def run() -> None:
    """Run daily bar collection for recent trading days (default command for scheduler)."""
    config = load_config()
    tz_name = config["project"]["timezone"]
    end_date = today_in_timezone(tz_name)
    # Fetch last 5 days to catch weekends and holidays
    start_date = end_date - timedelta(days=5)

    _logger.info("Running yf_daily bar collection: %s to %s", start_date, end_date)
    backfill(start=start_date, end=end_date)

    try:
        with get_connection() as conn:
            notify_ingest(
                conn,
                NOTIFY_CHANNEL,
                {
                    "source": "market_bar_daily",
                    "latest_at": datetime.combine(end_date, datetime.min.time()).isoformat(),
                },
            )
            conn.commit()
    except Exception as exc:
        _logger.warning("Failed to notify ingest channel for daily bars: %s", exc)


def main() -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Daily OHLCV pipeline (yfinance)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("run", help="Fetch recent trading days (last 5 days)")

    backfill_parser = subparsers.add_parser("backfill", help="Backfill daily bars")
    backfill_parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    backfill_parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    backfill_parser.add_argument("--asof-date", help="Universe snapshot date (YYYY-MM-DD)")
    backfill_parser.add_argument("--symbols", help="Comma-separated symbol list override")
    backfill_parser.add_argument("--limit", type=int, help="Limit number of symbols")

    args = parser.parse_args()
    if args.command == "run":
        run()
    elif args.command == "backfill":
        asof_date = parse_date(args.asof_date) if args.asof_date else None
        symbols_override = (
            [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
            if args.symbols
            else None
        )
        backfill(
            parse_date(args.start),
            parse_date(args.end),
            asof_date=asof_date,
            symbols_override=symbols_override,
            limit=args.limit,
        )


if __name__ == "__main__":
    main()
