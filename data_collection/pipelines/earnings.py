"""Earnings calendar pipeline (YFinance)."""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import os
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import yfinance as yf

from data_collection.common.db import insert_raw_object, notify_ingest
from data_collection.common.hashing import sha256_bytes
from data_collection.common.http import HttpError, build_session, request_with_retries
from data_collection.common.storage import build_storage
from data_collection.common.settings import load_config
from data_collection.common.symbols import get_config_symbols
from data_collection.common.time_utils import today_in_timezone
from data_collection.db.connection import get_connection
from data_collection.common.config import HttpSettings, load_common_settings

_logger = logging.getLogger(__name__)
NOTIFY_CHANNEL = os.getenv("EIQORA_INGEST_CHANNEL", "eiqora_ingest")
NASDAQ_REFERRER = "https://www.nasdaq.com/market-activity/earnings"
SEC_ITEM_202_RE = re.compile(r"item\s*2\.02", re.IGNORECASE)

_YF_SYMBOL_MAP: dict[str, str] | None = None


def _load_symbol_map() -> dict[str, str]:
    global _YF_SYMBOL_MAP
    if _YF_SYMBOL_MAP is not None:
        return _YF_SYMBOL_MAP
    try:
        config = load_config()
        mapping = config.get("yfinance", {}).get("symbol_map", {})
        _YF_SYMBOL_MAP = {k.upper(): v for k, v in mapping.items()}
    except Exception:
        _YF_SYMBOL_MAP = {}
    return _YF_SYMBOL_MAP


def _yfinance_symbol(symbol: str) -> str:
    """Convert internal symbol to yfinance format (e.g. BRK.B -> BRK-B)."""
    mapping = _load_symbol_map()
    mapped = mapping.get(symbol.upper())
    if mapped:
        return mapped
    return symbol.replace(".", "-")

def _fetch_yfinance_earnings(symbols: list[str]) -> list[dict[str, object]]:
    """Fetch earnings data using YFinance (historical + estimates)."""
    rows = []
    
    for i, symbol in enumerate(symbols):
        try:
            if i % 10 == 0:
                _logger.info(f"Processing {i}/{len(symbols)}: {symbol}")
                
            yf_sym = _yfinance_symbol(symbol)
            ticker = yf.Ticker(yf_sym)
            
            # 1. Historical Data (from Income Statement)
            # This gives us actual Revenue and EPS for past quarters
            try:
                # Transpose so dates are rows
                q_stmt = ticker.quarterly_income_stmt.T
                q_financials = ticker.quarterly_financials.T
                
                # Combine info if possible, but mainly we need Revenue (from stmt) and Basic EPS (from financials)
                # Note: YFinance structure varies. Total Revenue is usually in income_stmt.
                # Basic EPS is in financials.
                
                # Let's iterate through the last 4 quarters available
                common_dates = q_stmt.index.intersection(q_financials.index)
                
                for d in common_dates:
                    if not isinstance(d, (date, datetime, pd.Timestamp)):
                        continue
                        
                    earnings_date = d.date() if isinstance(d, (datetime, pd.Timestamp)) else d
                    
                    revenue = _parse_float(q_stmt.loc[d].get("Total Revenue"))
                    eps = _parse_float(q_financials.loc[d].get("Basic EPS"))
                    
                    if revenue is not None or eps is not None:
                        rows.append({
                            "symbol": symbol,
                            "earnings_date": earnings_date,
                            "time_of_day": None, # YF doesn't give this history easily
                            "eps_est": None,
                            "eps_actual": eps,
                            "revenue_est": None,
                            "revenue_actual": revenue,
                            "source": "yfinance_hist",
                            "fiscal_quarter": None, # Could infer, but safer to leave null
                            "revenue_growth_yoy": None, # Will be calc in SQL
                            "guidance": None
                        })
                        
            except Exception as e:
                _logger.debug(f"Failed to fetch historical for {symbol}: {e}")

            # 2. Upcoming / Recent Estimates (from Calendar)
            try:
                cal = ticker.calendar
                if isinstance(cal, dict) and "Earnings Date" in cal:
                    # New YFinance structure returns a dict with lists
                    dates = cal.get("Earnings Date", [])
                    earnings_avg = cal.get("Earnings Average")
                    revenue_avg = cal.get("Revenue Average")
                    
                    for d in dates:
                        if not d: continue
                        # YFinance dates here might be just dates or datetimes
                        evt_date = d.date() if hasattr(d, "date") else d
                        
                        rows.append({
                            "symbol": symbol,
                            "earnings_date": evt_date,
                            "time_of_day": None,
                            "eps_est": _parse_float(earnings_avg),
                            "eps_actual": None, # It's a forecast
                            "revenue_est": _parse_float(revenue_avg),
                            "revenue_actual": None,
                            "source": "yfinance_cal",
                            "fiscal_quarter": None,
                            "revenue_growth_yoy": None,
                            "guidance": None
                        })
            except Exception as e:
                _logger.debug(f"Failed to fetch calendar for {symbol}: {e}")
                
        except Exception as e:
            _logger.error(f"Error fetching {symbol}: {e}")
            
    return rows


def run_yfinance(limit_symbols: int | None = None) -> None:
    config = load_config()
    symbols_config, _, _ = get_config_symbols(config)
    symbols = list(symbols_config)
    
    if limit_symbols:
        symbols = symbols[:limit_symbols]
        
    _logger.info(f"Fetching YFinance earnings for {len(symbols)} symbols...")
    
    rows = _fetch_yfinance_earnings(symbols)
    _logger.info(f"Fetched {len(rows)} earnings events.")
    
    if not rows:
        return

    # Store raw archive (optional, keeping system pattern)
    common = load_common_settings()
    storage = build_storage(common)
    today = datetime.now()
    raw_bytes = json.dumps(rows, ensure_ascii=True, default=str).encode("utf-8")
    sha = sha256_bytes(raw_bytes)
    object_key = f"earnings/yfinance/{today.date().isoformat()}/{sha}.json"
    stored = storage.write_bytes(object_key, raw_bytes, content_type="application/json")

    with get_connection() as conn:
        insert_raw_object(
            source="yfinance_earnings",
            object_key=stored.object_key,
            content_type="application/json",
            sha256=stored.sha256,
            http_status=200,
            conn=conn,
        )
        
        _upsert_events(conn, rows)
        notify_ingest(
            conn,
            NOTIFY_CHANNEL,
            {
                "source": "earnings_event",
                "count": len(rows),
                "provider": "yfinance",
            },
        )
        
        # Calculate YoY Growth
        _logger.info("Calculating YoY Growth...")
        with conn.cursor() as cursor:
             cursor.execute("""
                UPDATE earnings_event e
                SET revenue_growth_yoy = (
                    (e.revenue_actual - prev.revenue_actual) / NULLIF(ABS(prev.revenue_actual), 0) * 100
                )
                FROM earnings_event prev
                WHERE e.symbol = prev.symbol
                  AND prev.earnings_date < e.earnings_date - interval '10 months'
                  AND prev.earnings_date > e.earnings_date - interval '14 months'
                  AND e.revenue_actual IS NOT NULL
                  AND prev.revenue_actual IS NOT NULL
                  AND (e.revenue_growth_yoy IS NULL OR e.revenue_growth_yoy = 0)
                  AND e.updated_at >= now() - interval '5 minutes'
            """)
        conn.commit()
    
    _logger.info("Earnings update complete.")



USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:109.0) Gecko/20100101 Firefox/121.0",
]


def _parse_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        import math
        if math.isnan(value) or math.isinf(value):
            return None
        return float(value)
    text = str(value).strip()
    if text == "":
        return None
    # Remove currency symbols and commas
    text = text.replace("$", "").replace(",", "").strip()
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
    # Stagger requests to avoid bursts
    time.sleep(random.uniform(2.0, 5.0))
    
    _logger.info("Fetching nasdaq calendar for %s", target_date)
    params = {"date": target_date.strftime("%Y-%m-%d")}
    
    # Session is already primed by caller with rotated UA
    headers = _nasdaq_headers(settings) # Relies on settings containing the UA?
    # _nasdaq_headers implementation checks settings.user_agent?
    # I should check _nasdaq_headers. If not, I should set User-Agent header manually here.
    headers["User-Agent"] = session.headers.get("User-Agent") or settings.user_agent
    
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
    # Add the date to each row (NASDAQ API doesn't include it in the response)
    for row in rows:
        row["date"] = target_date.strftime("%Y-%m-%d")
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


def _parse_nasdaq_items(items: list[dict], source: str, symbols_config: set[str] | None) -> list[dict]:
    rows = []
    for item in items:
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

        try:
            report_date = datetime.strptime(earnings_date, "%Y-%m-%d").date()
        except ValueError:
            continue

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
        else:
            raw_time = item.get("time") or ""
            if "pre-market" in raw_time:
                time_of_day = "pre-market"
            elif "after-hours" in raw_time:
                time_of_day = "after-hours"
            else:
                time_of_day = None
            eps_est = _parse_float(item.get("epsForecast"))
            eps_actual = _parse_float(item.get("eps"))
            revenue_est = _parse_float(item.get("revenueForecast"))
            revenue_actual = _parse_float(item.get("revenue"))

        rows.append(
            {
                "symbol": symbol,
                "earnings_date": report_date,
                "time_of_day": time_of_day,
                "eps_est": eps_est,
                "eps_actual": eps_actual,
                "revenue_est": revenue_est,
                "revenue_actual": revenue_actual,
                "source": source,
                "fiscal_quarter": fiscal_quarter,
                "revenue_growth_yoy": None,
                "guidance": guidance,
            }
        )
    return rows


def _backfill_actuals_from_yfinance(conn, symbols: list[str]) -> int:
    """Backfill missing eps_actual/revenue_actual using yfinance quarterly financials.

    NASDAQ often returns NULL for eps/revenue actuals, especially for recent
    earnings. YFinance quarterly_financials has the actual reported numbers
    keyed by fiscal quarter end date. We match each fiscal quarter to the
    nearest earnings_event row (report date is typically 15-60 days after
    fiscal quarter end) and fill in the missing actuals.
    """
    with conn.cursor() as cursor:
        cursor.execute("""
            SELECT DISTINCT symbol
            FROM earnings_event
            WHERE eps_actual IS NULL
              AND earnings_date < CURRENT_DATE
              AND earnings_date > CURRENT_DATE - interval '180 days'
        """)
        missing_symbols = {row[0] for row in cursor.fetchall()}

    target = missing_symbols & set(symbols) if symbols else missing_symbols
    if not target:
        _logger.info("backfill: no symbols need eps_actual backfill")
        return 0

    _logger.info(f"backfill: attempting yfinance actuals for {len(target)} symbols")
    updated = 0

    for symbol in sorted(target):
        try:
            yf_sym = _yfinance_symbol(symbol)
            ticker = yf.Ticker(yf_sym)

            q_stmt = ticker.quarterly_income_stmt
            q_financials = ticker.quarterly_financials
            if q_stmt is None or q_stmt.empty or q_financials is None or q_financials.empty:
                continue

            q_stmt_t = q_stmt.T
            q_financials_t = q_financials.T
            common_dates = q_stmt_t.index.intersection(q_financials_t.index)

            for d in common_dates:
                if not isinstance(d, (date, datetime, pd.Timestamp)):
                    continue
                fiscal_end = d.date() if isinstance(d, (datetime, pd.Timestamp)) else d

                eps = _parse_float(q_financials_t.loc[d].get("Basic EPS"))
                revenue = _parse_float(q_stmt_t.loc[d].get("Total Revenue"))

                if eps is None and revenue is None:
                    continue

                # Match to earnings_event: report date is typically 15-60 days
                # after fiscal quarter end. Search window: fiscal_end to +65 days.
                with conn.cursor() as cursor:
                    set_parts = []
                    params = []
                    if eps is not None:
                        set_parts.append("eps_actual = %s")
                        params.append(eps)
                    if revenue is not None:
                        set_parts.append("revenue_actual = %s")
                        params.append(revenue)
                    set_parts.append("updated_at = now()")

                    params.extend([symbol, fiscal_end, fiscal_end])

                    cursor.execute(f"""
                        UPDATE earnings_event
                        SET {', '.join(set_parts)}
                        WHERE symbol = %s
                          AND eps_actual IS NULL
                          AND earnings_date BETWEEN %s AND %s + interval '65 days'
                          AND earnings_date < CURRENT_DATE
                    """, params)

                    if cursor.rowcount > 0:
                        updated += cursor.rowcount
                        _logger.info(f"backfill: {symbol} updated {cursor.rowcount} rows "
                                     f"from fiscal quarter ending {fiscal_end}")

        except Exception as e:
            _logger.debug(f"backfill: failed for {symbol}: {e}")

    conn.commit()
    _logger.info(f"backfill: updated {updated} earnings events with actuals from yfinance")
    return updated


def run(
    window_past: int | None = None,
    window_future: int | None = None,
    limit_symbols: int | None = None,
) -> None:
    config = load_config()
    tz_name = config["project"]["timezone"]
    earnings_cfg = config.get("earnings", {})
    nasdaq_cfg = earnings_cfg["nasdaq"]
    alpha_cfg = earnings_cfg.get("alphavantage", {})
    symbols_config, _, _ = get_config_symbols(config)
    symbols_list = list(symbols_config)
    if limit_symbols:
        symbols_list = symbols_list[:limit_symbols]
    symbol_filter = set(symbols_list) if symbols_list else None

    default_past = earnings_cfg.get("window_past_days", alpha_cfg.get("window_past_days", 30))
    default_future = earnings_cfg.get("window_future_days", alpha_cfg.get("window_future_days", 14))
    window_past = window_past if window_past is not None else int(default_past)
    window_future = window_future if window_future is not None else int(default_future)

    today = today_in_timezone(tz_name)
    start_date = today - timedelta(days=window_past)
    end_date = today + timedelta(days=window_future)
    date_from = start_date.isoformat()
    date_to = end_date.isoformat()

    common = load_common_settings()
    storage = build_storage(common)
    session = build_session(common.http)

    sec_user_agent = os.getenv("SEC_USER_AGENT") or config.get("sec", {}).get("user_agent") or common.http.user_agent
    sec_settings = HttpSettings(
        user_agent=sec_user_agent,
        timeout_seconds=common.http.timeout_seconds,
        max_retries=common.http.max_retries,
        backoff_seconds=common.http.backoff_seconds,
    )
    sec_session = build_session(sec_settings)

    payload: list[dict[str, object]] = []
    rows: list[dict[str, object]] = []
    source = None
    source_override = os.getenv("EARNINGS_SOURCE", "").strip().lower()

    if source_override == "yfinance":
        run_yfinance(limit_symbols=limit_symbols)
        return

    if source_override == "sec":
        source = "sec_8k"
        with get_connection() as conn:
            payload = _fetch_sec_earnings(conn, sec_session, sec_settings, symbols_list, start_date, end_date)
        rows = payload
    else:
        # Use NASDAQ as primary source
        nasdaq_endpoint = nasdaq_cfg["earnings_calendar_endpoint"]
        all_days = _iter_dates(start_date, end_date)
        
        # Batching for progress updates and resilience
        batch_size = 20
        total_batches = (len(all_days) + batch_size - 1) // batch_size
        concurrency = int(os.getenv("NASDAQ_EARNINGS_CONCURRENCY", "6"))
        
        _logger.info("earnings source=nasdaq days=%s concurrency=%s batches=%s", len(all_days), concurrency, total_batches)
        
        source = "nasdaq"
        
        # We process in batches and upsert immediately to give feedback
        with get_connection() as conn:
            for batch_idx, i in enumerate(range(0, len(all_days), batch_size)):
                batch_days = all_days[i : i + batch_size]
                
                # Rotate User Agent and Session per batch for robustness
                batch_ua = random.choice(USER_AGENTS)
                _logger.info("earnings batch %s/%s start=%s end=%s ua=%s", batch_idx + 1, total_batches, batch_days[0], batch_days[-1], batch_ua[:30] + "...")
                
                # Create fresh session for this batch
                batch_settings = common.http
                # Clone settings to update UA (HttpSettings is immutable? It's a structure. I'll pass UA to session headers)
                batch_session = build_session(batch_settings)
                batch_session.headers.update({"User-Agent": batch_ua})
                
                # Prime the session once for the batch
                try:
                    _prime_nasdaq_session(batch_session, batch_settings)
                except Exception as e:
                    _logger.warning("Failed to prime session for batch %s: %s", batch_idx + 1, e)
                    # Continue anyway, maybe it works without priming or retries handle it
                
                with ThreadPoolExecutor(max_workers=concurrency) as executor:
                    future_map = {
                        executor.submit(_fetch_nasdaq, batch_session, common.http, nasdaq_endpoint, day): day
                        for day in batch_days
                    }
                    batch_payload = []
                    for future in as_completed(future_map):
                        try:
                            batch_payload.extend(future.result())
                        except HttpError:
                            continue
                
                # Parse and upsert batch
                batch_rows = _parse_nasdaq_items(batch_payload, source, symbol_filter)
                if batch_rows:
                    _upsert_events(conn, batch_rows)
                    _logger.info("earnings batch %s upsert rows=%s", batch_idx + 1, len(batch_rows))
                    conn.commit()
                
                # Accumulate for final raw object storage
                payload.extend(batch_payload)
                rows.extend(batch_rows)

        # Fallback SEC logic (only if no data found at all?)
        if not rows and os.getenv("EARNINGS_SEC_FALLBACK", "1") != "0":
            _logger.info("earnings fallback=sec_8k start=%s end=%s", start_date, end_date)
            with get_connection() as conn:
                sec_payload = _fetch_sec_earnings(conn, sec_session, sec_settings, symbols_list, start_date, end_date)
            if sec_payload:
                source = "sec_8k"
                payload = sec_payload
                rows = _parse_nasdaq_items(payload, source, symbol_filter) # Re-use parse logic if compatible?
                # Actually SEC payload structure might differ. 
                # _fetch_sec_earnings returns LIST OF DICTS compatible with rows structure directly?
                # Original code: rows = payload.
                # So _fetch_sec_earnings returns processed rows?
                # Let's check original code: "rows = payload". Yes.
                rows = sec_payload

    if source is None:
        source = "nasdaq"

    raw_bytes = json.dumps(payload, ensure_ascii=True, default=str).encode("utf-8")
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

        if raw_id is not None and rows:
            for row in rows:
                if "raw_id" in row:
                    row["raw_id"] = raw_id

        # Rows were prepared above based on the selected source.

        _logger.info("earnings upsert rows=%s source=%s", len(rows), source)
        _upsert_events(conn, rows)
        if rows:
            notify_ingest(
                conn,
                NOTIFY_CHANNEL,
                {
                    "source": "earnings_event",
                    "count": len(rows),
                    "provider": source,
                },
            )
        
        # Backfill missing eps_actual/revenue_actual from yfinance
        _logger.info("Running yfinance actuals backfill...")
        _backfill_actuals_from_yfinance(conn, symbols_list)

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


def _nasdaq_headers(settings: HttpSettings) -> dict[str, str]:
    return {
        "User-Agent": settings.user_agent,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": NASDAQ_REFERRER,
        "Origin": "https://www.nasdaq.com",
        "DNT": "1",
        "Connection": "keep-alive",
    }


def _prime_nasdaq_session(session, settings: HttpSettings) -> None:
    if getattr(session, "_nasdaq_primed", False):
        return
    try:
        session.get(
            NASDAQ_REFERRER,
            headers=_nasdaq_headers(settings),
            timeout=settings.timeout_seconds,
        )
    except Exception:
        pass
    setattr(session, "_nasdaq_primed", True)


def _load_sec_filings(conn, symbols: list[str], start: date, end: date) -> list[tuple[str, date, str | None]]:
    if not symbols:
        return []
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT s.ticker, f.filed_at, f.primary_doc_url
            FROM sec_filing f
            JOIN security s ON s.cik = f.cik
            WHERE s.ticker = ANY(%s)
              AND f.form_type IN ('8-K', '8-K/A')
              AND f.filed_at BETWEEN %s AND %s
            ORDER BY f.filed_at
            """,
            (symbols, start, end),
        )
        return [(row[0], row[1], row[2]) for row in cursor.fetchall()]


def _filing_has_item_202(session, settings: HttpSettings, url: str) -> bool:
    headers = {
        "User-Agent": settings.user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    response = request_with_retries(session, "GET", url, settings=settings, headers=headers)
    content = response.text or ""
    return bool(SEC_ITEM_202_RE.search(content))


def _fetch_sec_earnings(
    conn,
    session,
    settings: HttpSettings,
    symbols: list[str],
    start: date,
    end: date,
) -> list[dict[str, object]]:
    filings = _load_sec_filings(conn, symbols, start, end)
    if not filings:
        return []
    delay = max(0.0, float(os.getenv("SEC_REQUEST_DELAY_SECONDS", "0.2")))
    rows: list[dict[str, object]] = []
    for symbol, filed_at, primary_doc_url in filings:
        if not primary_doc_url or not filed_at:
            continue
        try:
            has_item = _filing_has_item_202(session, settings, primary_doc_url)
        except Exception as exc:
            _logger.warning("sec_earnings fetch failed for %s %s: %s", symbol, primary_doc_url, exc)
            has_item = False
        if delay:
            time.sleep(delay)
        if not has_item:
            continue
        rows.append(
            {
                "symbol": symbol,
                "earnings_date": filed_at,
                "time_of_day": None,
                "eps_est": None,
                "eps_actual": None,
                "revenue_est": None,
                "revenue_actual": None,
                "raw_id": None,
                "source": "sec_8k",
                "fiscal_quarter": None,
                "revenue_growth_yoy": None,
                "guidance": None,
            }
        )
    return rows


def _get_earnings_columns(conn) -> set[str]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'earnings_event'
            """
        )
        return {row[0] for row in cursor.fetchall()}


def _upsert_events(conn, rows: list[dict[str, object]]) -> None:
    if not rows:
        return
    table_columns = _get_earnings_columns(conn)
    base_order = [
        "symbol",
        "earnings_date",
        "time_of_day",
        "eps_est",
        "eps_actual",
        "revenue_est",
        "revenue_actual",
        "source",
        "fiscal_quarter",
        "revenue_growth_yoy",
        "guidance",
        "raw_id",
    ]
    insert_columns = [col for col in base_order if col in table_columns]
    if not insert_columns:
        raise RuntimeError("earnings_event has no compatible columns for upsert")

    has_updated_at = "updated_at" in table_columns
    insert_cols_sql = ", ".join(insert_columns + (["updated_at"] if has_updated_at else []))
    placeholders = ", ".join(["%s"] * len(insert_columns) + (["now()"] if has_updated_at else []))

    update_columns = [col for col in insert_columns if col not in {"symbol", "earnings_date"}]
    # Use COALESCE to never overwrite existing non-null values with NULL
    update_assignments = [f"{col} = COALESCE(EXCLUDED.{col}, earnings_event.{col})" for col in update_columns]
    if has_updated_at:
        update_assignments.append("updated_at = now()")
    if update_assignments:
        update_sql = ", ".join(update_assignments)
    elif has_updated_at:
        update_sql = "updated_at = now()"
    else:
        update_sql = "symbol = EXCLUDED.symbol"

    values = [tuple(row.get(col) for col in insert_columns) for row in rows]
    with conn.cursor() as cursor:
        cursor.executemany(
            f"""
            INSERT INTO earnings_event ({insert_cols_sql})
            VALUES ({placeholders})
            ON CONFLICT (symbol, earnings_date)
            DO UPDATE SET {update_sql}
            """,
            values,
        )


def main() -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Earnings calendar pipeline")
    parser.add_argument("command", nargs="?", default="run", choices=["run"])
    parser.add_argument("--past-days", type=int, help="Override past window days")
    parser.add_argument("--future-days", type=int, help="Override future window days")
    parser.add_argument("--limit", type=int, help="Limit number of symbols")
    parser.add_argument(
        "--source",
        choices=["nasdaq", "sec", "yfinance"],
        help="Override earnings source",
    )
    args = parser.parse_args()
    if args.source:
        os.environ["EARNINGS_SOURCE"] = args.source
    run(
        window_past=args.past_days,
        window_future=args.future_days,
        limit_symbols=args.limit,
    )


if __name__ == "__main__":
    main()
