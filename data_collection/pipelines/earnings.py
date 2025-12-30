"""Earnings calendar pipeline (Alpha Vantage + Nasdaq fallback)."""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta

from data_collection.common.db import insert_raw_object
from data_collection.common.hashing import sha256_bytes
from data_collection.common.http import build_session, request_with_retries, HttpError
from data_collection.common.config import HttpSettings
from data_collection.common.storage import build_storage
from data_collection.common.settings import get_env_value, load_config
from data_collection.common.symbols import get_config_symbols
from data_collection.common.time_utils import today_in_timezone
from data_collection.db.connection import get_connection
from data_collection.common.config import load_common_settings

_logger = logging.getLogger(__name__)



# _upsert_events moved to end of file



def _parse_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if text == "":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _fetch_nasdaq(
    session,
    settings,
    endpoint: str,
    target_date: date,
) -> list[dict[str, object]]:
    params = {"date": target_date.strftime("%Y-%m-%d")}
    headers = {
        "Accept": "application/json",
        "Referer": "https://www.nasdaq.com/market-activity/earnings",
    }
    fast_settings = HttpSettings(
        user_agent=settings.user_agent,
        timeout_seconds=10,
        max_retries=2,
        backoff_seconds=1.0,
    )
    response = request_with_retries(session, "GET", endpoint, settings=fast_settings, params=params, headers=headers)
    payload = response.json()
    data = payload.get("data") or {}
    rows = data.get("rows") or []
    return rows


def _fetch_alphavantage(
    session,
    settings,
    endpoint: str,
    api_key: str,
    horizon: str,
) -> list[dict[str, object]]:
    params = {"function": "EARNINGS_CALENDAR", "horizon": horizon, "apikey": api_key}
    response = request_with_retries(session, "GET", endpoint, settings=settings, params=params)
    content = response.text
    reader = csv.DictReader(io.StringIO(content))
    return [row for row in reader if row.get("symbol")]


def _iter_dates(start: date, end: date) -> list[date]:
    days = []
    cursor = start
    while cursor <= end:
        days.append(cursor)
        cursor += timedelta(days=1)
    return days


def run(window_past: int | None = None, window_future: int | None = None) -> None:
    config = load_config()
    tz_name = config["project"]["timezone"]
    nasdaq_cfg = config["earnings"]["nasdaq"]
    alpha_cfg = config["earnings"]["alphavantage"]
    symbols_config, _, _ = get_config_symbols(config)

    window_past = window_past if window_past is not None else int(alpha_cfg["window_past_days"])
    window_future = window_future if window_future is not None else int(alpha_cfg["window_future_days"])

    today = today_in_timezone(tz_name)
    date_from = (today - timedelta(days=window_past)).isoformat()
    date_to = (today + timedelta(days=window_future)).isoformat()

    common = load_common_settings()
    storage = build_storage(common)
    session = build_session(common.http)

    payload = None
    source = None
    alpha_key = get_env_value(alpha_cfg["api_key_env"], required=False)
    if alpha_key:
        horizon = "3month" if window_future <= 90 else "6month"
        _logger.info("earnings source=alphavantage horizon=%s", horizon)
        payload = _fetch_alphavantage(
            session,
            common.http,
            alpha_cfg["earnings_calendar_endpoint"],
            alpha_key,
            horizon,
        )
        source = "alphavantage"
    else:
        nasdaq_endpoint = nasdaq_cfg["earnings_calendar_endpoint"]
        rows: list[dict[str, object]] = []
        days = _iter_dates(today - timedelta(days=window_past), today + timedelta(days=window_future))
        concurrency = int(os.getenv("NASDAQ_EARNINGS_CONCURRENCY", "6"))
        _logger.info("earnings source=nasdaq days=%s concurrency=%s", len(days), concurrency)
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            future_map = {
                executor.submit(_fetch_nasdaq, session, common.http, nasdaq_endpoint, day): day
                for day in days
            }
            for future in as_completed(future_map):
                try:
                    rows.extend(future.result())
                except HttpError:
                    continue
        payload = rows
        source = "nasdaq"

    raw_bytes = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    sha = sha256_bytes(raw_bytes)
    object_key = f"earnings/{source}/{today.isoformat()}/{sha}.json"
    stored = storage.write_bytes(object_key, raw_bytes, content_type="application/json")

    with get_connection() as conn:
        raw_id = insert_raw_object(
            source=f"{source}_earnings_calendar",
            object_key=stored.object_key,
            content_type="application/json",
            sha256=stored.sha256,
            http_status=200,
            meta={"from": date_from, "to": date_to},
            conn=conn,
        )

        # Helper to get previous revenue for YoY calc
        # In a real pipeline we'd query DB for history, but here we only have the current batch.
        # For meaningful YoY, we might need to query the DB for the same quarter last year.
        # Given this is a batch ingestion, let's try to do it if we have the data in the payload or just leave null for now.
        # Actually, best practice: Upsert the raw data first, then run a SQL update to calc growth.
        # But upsert_events needs the columns.
        
        rows = []
        for item in payload:
            symbol = item.get("symbol") if isinstance(item, dict) else None
            if not isinstance(item, dict):
                continue
            if symbols_config and symbol not in symbols_config:
                continue
            if source == "alphavantage":
                earnings_date = item.get("reportDate")
            else:
                earnings_date = item.get("date")
            if not symbol or not earnings_date:
                continue
                
            report_date = datetime.strptime(earnings_date, "%Y-%m-%d").date()
            
            # Infer fiscal quarter (rough approx if not provided)
            # This is hard without reference data. 
            fiscal_quarter = None 
            
            time_of_day = None
            eps_est = None
            eps_actual = None
            revenue_est = None
            revenue_actual = None
            guidance = None
            
            if source == "alphavantage":
                time_of_day = None
                eps_est = _parse_float(item.get("estimate"))
                eps_actual = None
                revenue_est = None
                revenue_actual = None
            else:
                time_of_day = item.get("time")
                eps_est = _parse_float(item.get("epsForecast"))
                eps_actual = _parse_float(item.get("eps"))
                revenue_est = _parse_float(item.get("revenueForecast"))
                revenue_actual = _parse_float(item.get("revenue"))

            rows.append(
                (
                    symbol,
                    report_date,
                    time_of_day,
                    eps_est,
                    eps_actual,
                    revenue_est,
                    revenue_actual,
                    raw_id,
                    source,
                    fiscal_quarter, # new
                    None, # revenue_growth_yoy (will calc in SQL)
                    guidance, # new
                )
            )

        _logger.info("earnings upsert rows=%s source=%s", len(rows), source)
        _upsert_events(conn, rows)
        
        # Post-process: Calculate YoY Growth
        # Only for rows we just updated/inserted
        with conn.cursor() as cursor:
            cursor.execute("""
                UPDATE earnings_event e
                SET revenue_growth_yoy = (
                    (e.revenue_actual - prev.revenue_actual) / NULLIF(ABS(prev.revenue_actual), 0) * 100
                )
                FROM earnings_event prev
                WHERE e.symbol = prev.symbol
                  AND prev.earnings_date < e.earnings_date - interval '10 months' -- approx 1 year ago
                  AND prev.earnings_date > e.earnings_date - interval '14 months'
                  AND e.revenue_actual IS NOT NULL
                  AND prev.revenue_actual IS NOT NULL
                  AND e.revenue_growth_yoy IS NULL
                  AND e.updated_at >= now() - interval '5 minutes'
            """)
        
        conn.commit()


def main() -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Earnings calendar pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Fetch earnings calendar")
    run_parser.add_argument("--past-days", type=int, help="Override past window days")
    run_parser.add_argument("--future-days", type=int, help="Override future window days")
    args = parser.parse_args()
    if args.command == "run":
        run(window_past=args.past_days, window_future=args.future_days)


if __name__ == "__main__":
    main()
