import argparse
import logging
import os
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

import psycopg
import requests
import yaml
import yfinance as yf


SYMBOLS = ["NVDA", "AAPL"]
FRED_URL = "https://api.stlouisfed.org/fred/series/observations"


def _get_db_url() -> str:
    url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/finance")
    return url.replace("postgresql+psycopg://", "postgresql://")


def _write_temp_config(temp_dir: str) -> Path:
    config_path = Path("data_collection/config.yaml")
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    symbols_path = Path(temp_dir) / "symbols.txt"
    symbols_path.write_text("\n".join(SYMBOLS) + "\n", encoding="utf-8")
    config.setdefault("universe", {})
    config["universe"]["symbols_file"] = str(symbols_path)
    temp_config_path = Path(temp_dir) / "config.yaml"
    temp_config_path.write_text(
        yaml.safe_dump(config, sort_keys=False),
        encoding="utf-8",
    )
    return temp_config_path


def _patch_config(temp_config_path: Path) -> None:
    from data_collection.common import settings as dc_settings
    from data_collection.common import symbols as dc_symbols

    dc_settings.DEFAULT_CONFIG_PATH = temp_config_path
    dc_symbols.DEFAULT_CONFIG_PATH = temp_config_path


def _backfill_daily_bars(start: date, end: date) -> None:
    from data_collection.pipelines import yf_daily

    logging.info("Backfilling daily bars for %s", ", ".join(SYMBOLS))
    yf_daily.backfill(start, end, symbols_override=SYMBOLS)


def _backfill_vix(start: date, end: date) -> None:
    logging.info("Backfilling VIX daily bars")
    db_url = _get_db_url()
    symbols = {
        "^VIX": "IDX_VIX",
        "^VVIX": "IDX_VVIX",
        "^MOVE": "IDX_MOVE",
    }
    end_inclusive = end + timedelta(days=1)
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            for yf_symbol, db_symbol in symbols.items():
                try:
                    hist = yf.Ticker(yf_symbol).history(start=start, end=end_inclusive)
                except Exception as exc:
                    logging.warning("VIX fetch failed for %s: %s", yf_symbol, exc)
                    continue
                if hist.empty:
                    logging.info("No VIX data for %s", yf_symbol)
                    continue
                for idx, row in hist.iterrows():
                    bar_date = idx.date()
                    cur.execute(
                        """
                        INSERT INTO market_bar_daily
                            (symbol, date, open, high, low, close, volume, source)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, 'yfinance')
                        ON CONFLICT (symbol, date)
                        DO UPDATE SET
                            open = EXCLUDED.open,
                            high = EXCLUDED.high,
                            low = EXCLUDED.low,
                            close = EXCLUDED.close,
                            volume = EXCLUDED.volume,
                            source = EXCLUDED.source
                        """,
                        (
                            db_symbol,
                            bar_date,
                            float(row["Open"]) if row.get("Open") is not None else None,
                            float(row["High"]) if row.get("High") is not None else None,
                            float(row["Low"]) if row.get("Low") is not None else None,
                            float(row["Close"]) if row.get("Close") is not None else None,
                            int(row["Volume"]) if row.get("Volume") is not None else 0,
                        ),
                    )
                conn.commit()
                logging.info("VIX bars inserted for %s", yf_symbol)


def _backfill_earnings(days_back: int) -> None:
    from data_collection.pipelines import earnings

    logging.info("Backfilling earnings for %s days", days_back)
    os.environ.setdefault("EARNINGS_SOURCE", "sec")
    earnings.run(window_past=days_back, window_future=0)


def _backfill_sec_filings(days_back: int) -> None:
    from data_collection.pipelines.sec_edgar import pipeline as sec_edgar_pipeline

    logging.info("Backfilling SEC 8-K filings for %s days", days_back)
    os.environ["SEC_FORMS"] = "8-K,8-K/A"
    os.environ["SEC_TRAILING_DAYS"] = str(days_back)
    sec_edgar_pipeline.run()


def _backfill_corporate_actions(start: date, end: date) -> None:
    from data_collection.pipelines import corporate_actions_crawler

    logging.info("Backfilling corporate actions")
    corporate_actions_crawler.run(start, end)


def _gdelt_backfill(start: date, end: date, chunk_days: int, max_docs: int | None) -> None:
    from data_collection.pipelines import gdelt

    logging.info("Backfilling news via GDELT (%s-day chunks)", chunk_days)
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=chunk_days - 1), end)
        os.environ["GDELT_START_DATETIME"] = cursor.strftime("%Y%m%d000000")
        os.environ["GDELT_END_DATETIME"] = chunk_end.strftime("%Y%m%d235959")
        logging.info("GDELT window %s -> %s", cursor, chunk_end)
        gdelt.run(max_docs=max_docs)
        cursor = chunk_end + timedelta(days=1)

    os.environ.pop("GDELT_START_DATETIME", None)
    os.environ.pop("GDELT_END_DATETIME", None)


def _get_fred_api_key() -> str | None:
    from data_collection.common.settings import load_config

    config = load_config()
    env_key = config.get("macro", {}).get("fred", {}).get("api_key_env", "FRED_API_KEY")
    return os.getenv(env_key)


def _fetch_fred_series(api_key: str, series_id: str, start: date, end: date) -> list[tuple[date, float]]:
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "observation_start": start.isoformat(),
        "observation_end": end.isoformat(),
    }
    resp = requests.get(FRED_URL, params=params, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    observations = []
    for obs in payload.get("observations", []):
        value = obs.get("value")
        if value is None or value == ".":
            continue
        obs_date = datetime.strptime(obs["date"], "%Y-%m-%d").date()
        observations.append((obs_date, float(value)))
    return observations


def _economic_indicator_columns(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'economic_indicator'
            """
        )
        return {row[0] for row in cur.fetchall()}


def _upsert_economic_indicator(
    conn,
    columns: set[str],
    indicator_name: str,
    event_date: date,
    value: float,
    prev_value: float | None,
) -> None:
    key_cols = []
    if "indicator" in columns and "indicator_date" in columns:
        key_cols = ["indicator", "indicator_date"]
    elif "indicator_name" in columns and "event_date" in columns:
        key_cols = ["indicator_name", "event_date"]
    if not key_cols:
        logging.warning("economic_indicator schema not recognized; skipping")
        return

    record: dict[str, object] = {}
    if "indicator" in columns:
        record["indicator"] = indicator_name
    if "indicator_name" in columns:
        record["indicator_name"] = indicator_name
    if "indicator_date" in columns:
        record["indicator_date"] = event_date
    if "event_date" in columns:
        record["event_date"] = event_date
    if "actual" in columns:
        record["actual"] = value
    if "value" in columns:
        record["value"] = value
    if "previous" in columns:
        record["previous"] = prev_value
    if "previous_value" in columns:
        record["previous_value"] = prev_value
    if "impact" in columns:
        record["impact"] = "high"
    if "is_release_day" in columns:
        record["is_release_day"] = True
    if "period" in columns:
        record["period"] = None

    key_values = [record[col] for col in key_cols]
    where_clause = " AND ".join([f"{col} = %s" for col in key_cols])
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT 1 FROM economic_indicator WHERE {where_clause} LIMIT 1",
            key_values,
        )
        exists = cur.fetchone() is not None
        if exists:
            update_cols = [col for col in record.keys() if col not in key_cols]
            if not update_cols:
                return
            set_clause = ", ".join([f"{col} = %s" for col in update_cols])
            values = [record[col] for col in update_cols] + key_values
            cur.execute(
                f"UPDATE economic_indicator SET {set_clause} WHERE {where_clause}",
                values,
            )
        else:
            columns_list = list(record.keys())
            placeholders = ", ".join(["%s"] * len(columns_list))
            cur.execute(
                f"INSERT INTO economic_indicator ({', '.join(columns_list)}) VALUES ({placeholders})",
                [record[col] for col in columns_list],
            )


def _backfill_economic_indicators(start: date, end: date) -> None:
    api_key = _get_fred_api_key()
    if not api_key:
        logging.warning("Missing FRED API key; skipping economic_indicator backfill")
        return

    series_map = {
        "CPIAUCSL": "CPI",
        "PAYEMS": "NFP",
        "PCEPI": "PCE",
        "GDP": "GDP",
        "FEDFUNDS": "FOMC",
    }

    db_url = _get_db_url()
    with psycopg.connect(db_url) as conn:
        columns = _economic_indicator_columns(conn)
        if not columns:
            logging.warning("economic_indicator table not found; skipping")
            return

        for series_id, indicator_name in series_map.items():
            logging.info("Backfilling %s (%s)", indicator_name, series_id)
            try:
                observations = _fetch_fred_series(api_key, series_id, start, end)
            except Exception as exc:
                logging.warning("FRED fetch failed for %s: %s", series_id, exc)
                continue

            prev_value = None
            for obs_date, value in observations:
                _upsert_economic_indicator(conn, columns, indicator_name, obs_date, value, prev_value)
                prev_value = value
            conn.commit()


def _parse_date(value: str | None, fallback: date) -> date:
    if not value:
        return fallback
    return date.fromisoformat(value)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    today = date.today()
    default_start = today - timedelta(days=365 * 5)

    parser = argparse.ArgumentParser(
        description="Backfill 5 years of data for NVDA and AAPL."
    )
    parser.add_argument("--start", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", help="End date (YYYY-MM-DD)")
    parser.add_argument("--news-chunk-days", type=int, default=30)
    parser.add_argument("--gdelt-max-docs", type=int, default=None)
    parser.add_argument("--skip-news", action="store_true")
    parser.add_argument("--skip-macro", action="store_true")
    parser.add_argument("--skip-sec", action="store_true")
    parser.add_argument("--skip-earnings", action="store_true")
    parser.add_argument("--skip-corporate-actions", action="store_true")
    parser.add_argument("--skip-vix", action="store_true")

    args = parser.parse_args()
    start = _parse_date(args.start, default_start)
    end = _parse_date(args.end, today)
    days_back = (end - start).days

    if start > end:
        raise SystemExit("Start date must be <= end date.")

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_config = _write_temp_config(temp_dir)
        _patch_config(temp_config)

        _backfill_daily_bars(start, end)

        if not args.skip_vix:
            _backfill_vix(start, end)

        if not args.skip_sec:
            _backfill_sec_filings(days_back)

        if not args.skip_earnings:
            _backfill_earnings(days_back)

        if not args.skip_corporate_actions:
            _backfill_corporate_actions(start, end)

        if not args.skip_news:
            _gdelt_backfill(start, end, args.news_chunk_days, args.gdelt_max_docs)

        if not args.skip_macro:
            _backfill_economic_indicators(start, end)


if __name__ == "__main__":
    main()
