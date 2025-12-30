"""Universe pipeline: SPY holdings snapshot + context symbols."""

from __future__ import annotations

import argparse
import io
from datetime import date
from typing import Iterable

import pandas as pd

from data_collection.common.config import load_common_settings
from data_collection.common.db import insert_raw_object
from data_collection.common.hashing import sha256_bytes
from data_collection.common.http import build_session, request_with_retries
from data_collection.common.symbols import get_config_symbols
from data_collection.common.storage import build_storage
from data_collection.common.settings import load_config
from data_collection.common.time_utils import parse_date, today_in_timezone
from data_collection.db.connection import get_connection


def _normalize_symbol(value: str) -> str | None:
    if not value:
        return None
    symbol = value.strip().upper()
    if not symbol or symbol == "-":
        return None
    return symbol


def _find_header_row(xlsx_bytes: bytes) -> int:
    preview = pd.read_excel(io.BytesIO(xlsx_bytes), sheet_name="holdings", header=None)
    for idx, row in preview.iterrows():
        values = {str(cell).strip().lower() for cell in row if cell == cell}
        if "ticker" in values or "symbol" in values:
            return idx
    raise RuntimeError("Unable to locate header row for SPY holdings")


def _extract_holdings(xlsx_bytes: bytes) -> list[tuple[str, float | None]]:
    header_row = _find_header_row(xlsx_bytes)
    df = pd.read_excel(io.BytesIO(xlsx_bytes), sheet_name="holdings", header=header_row)
    df_columns = {str(col).strip().lower(): col for col in df.columns}
    ticker_col = None
    weight_col = None
    for key in ("ticker", "symbol"):
        for col_name, original in df_columns.items():
            if key == col_name or key in col_name:
                ticker_col = original
                break
        if ticker_col:
            break
    for key in ("weight", "weight %", "weight(%)", "weight (%)"):
        for col_name, original in df_columns.items():
            if key == col_name or key in col_name:
                weight_col = original
                break
        if weight_col:
            break

    if ticker_col is None:
        raise RuntimeError("SPY holdings file missing ticker column")

    rows: list[tuple[str, float | None]] = []
    for _, row in df.iterrows():
        symbol = _normalize_symbol(str(row[ticker_col]) if row[ticker_col] is not None else "")
        if not symbol:
            continue
        weight = None
        if weight_col:
            raw_weight = row.get(weight_col)
            if raw_weight is not None and raw_weight == raw_weight:
                try:
                    weight = float(raw_weight)
                except (TypeError, ValueError):
                    weight = None
        rows.append((symbol, weight))
    return rows


def _upsert_snapshot(
    conn,
    asof_date: date,
    snapshot_rows: list[tuple[str, float | None, str]],
) -> None:
    with conn.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO universe_snapshot (asof_date, symbol, weight, source)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (asof_date, symbol)
            DO UPDATE SET weight = EXCLUDED.weight, source = EXCLUDED.source
            """,
            [(asof_date, symbol, weight, source) for symbol, weight, source in snapshot_rows],
        )


def _sync_members(conn, asof_date: date, symbols: set[str], source: str) -> None:
    with conn.cursor() as cursor:
        cursor.execute("SELECT symbol FROM universe_member WHERE active = true")
        current = {row[0] for row in cursor.fetchall()}

        added = symbols - current
        removed = current - symbols

        if added:
            cursor.executemany(
                """
                INSERT INTO universe_member (symbol, active, added_at, source)
                VALUES (%s, true, %s, %s)
                ON CONFLICT (symbol)
                DO UPDATE SET active = true, added_at = EXCLUDED.added_at, removed_at = NULL, source = EXCLUDED.source
                """,
                [(symbol, asof_date, source) for symbol in sorted(added)],
            )
        if removed:
            cursor.executemany(
                """
                UPDATE universe_member
                SET active = false, removed_at = %s
                WHERE symbol = %s
                """,
                [(asof_date, symbol) for symbol in sorted(removed)],
            )


def run(snapshot_date: date | None = None) -> None:
    config = load_config()
    tz_name = config["project"]["timezone"]
    asof_date = snapshot_date or today_in_timezone(tz_name)

    spy_url = config["universe"]["sources"]["spy_holdings_xlsx_url"]
    context_symbols = set(config["universe"].get("include_context_symbols", []))
    symbols_override, symbols_path, symbols_raw = get_config_symbols(config)

    common = load_common_settings()
    storage = build_storage(common)
    session = build_session(common.http)

    with get_connection() as conn:
        snapshot_rows: list[tuple[str, float | None, str]] = []
        symbols: set[str] = set()
        source = "spy_holdings"

        if symbols_override:
            sha = sha256_bytes(symbols_raw or b"")
            object_key = f"universe/symbols_file/{asof_date.isoformat()}/{sha}.txt"
            stored = storage.write_bytes(object_key, symbols_raw or b"", content_type="text/plain")
            insert_raw_object(
                source="symbols_file",
                object_key=stored.object_key,
                content_type="text/plain",
                sha256=stored.sha256,
                http_status=200,
                meta={"path": str(symbols_path), "asof_date": asof_date.isoformat()},
                conn=conn,
            )
            source = "symbols_file"
            for symbol in symbols_override:
                symbols.add(symbol)
                snapshot_rows.append((symbol, None, source))
        else:
            response = request_with_retries(session, "GET", spy_url, settings=common.http)
            payload = response.content
            sha = sha256_bytes(payload)
            object_key = f"universe/spy_holdings/{asof_date.isoformat()}/{sha}.xlsx"
            stored = storage.write_bytes(
                object_key,
                payload,
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

            insert_raw_object(
                source="spy_holdings",
                object_key=stored.object_key,
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                sha256=stored.sha256,
                http_status=response.status_code,
                meta={"url": spy_url, "asof_date": asof_date.isoformat()},
                conn=conn,
            )

            holdings = _extract_holdings(payload)
            for symbol, weight in holdings:
                symbols.add(symbol)
                snapshot_rows.append((symbol, weight, source))

        if not symbols_override:
            for symbol in sorted(context_symbols):
                symbols.add(symbol)
                snapshot_rows.append((symbol, None, "context"))

        _upsert_snapshot(conn, asof_date, snapshot_rows)
        _sync_members(conn, asof_date, symbols, source)
        conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Universe snapshot pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run universe snapshot")
    run_parser.add_argument("--date", dest="asof_date", help="As-of date (YYYY-MM-DD)")

    args = parser.parse_args()
    snapshot_date = parse_date(args.asof_date) if args.asof_date else None
    run(snapshot_date=snapshot_date)


if __name__ == "__main__":
    main()
