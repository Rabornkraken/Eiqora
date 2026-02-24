"""
1-minute OHLCV data collection pipeline using yfinance batch download.

Collects intraday 1-minute bars for current-day pricing and intraday charts.
Uses yf.download() in batch mode (single HTTP request for all symbols).
"""

import logging
import os
from datetime import datetime

import psycopg
import yfinance as yf

from data_collection.common.db import notify_ingest
from data_collection.common.settings import load_config

logger = logging.getLogger(__name__)
NOTIFY_CHANNEL = os.getenv("EIQORA_INGEST_CHANNEL", "eiqora_ingest")
_YF_SYMBOL_MAP: dict[str, str] | None = None


def get_db_url() -> str:
    url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/finance")
    return url.replace("postgresql+psycopg://", "postgresql://")


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
    mapping = _load_symbol_map()
    mapped = mapping.get(symbol.upper())
    if mapped:
        return mapped
    return symbol.replace(".", "-")


def _reverse_symbol(yf_symbol: str, original_tickers: list[str]) -> str:
    """Map a yfinance symbol back to our internal symbol."""
    mapping = _load_symbol_map()
    reverse = {v: k for k, v in mapping.items()}
    if yf_symbol in reverse:
        return reverse[yf_symbol]
    # yfinance uses '-' where we use '.'
    candidate = yf_symbol.replace("-", ".")
    for t in original_tickers:
        if t.upper() == candidate.upper() or t.upper() == yf_symbol.upper():
            return t
    return yf_symbol


def fetch_minute_bars(tickers: list[str], period: str = "1d") -> list[dict]:
    """Fetch 1-minute bars from yfinance using batch download."""
    if not tickers:
        return []

    yf_symbols = [_yfinance_symbol(t) for t in tickers]
    yf_to_orig = {}
    for orig, yfs in zip(tickers, yf_symbols):
        yf_to_orig[yfs] = orig

    logger.info(f"Downloading 1-min bars for {len(tickers)} symbols (period={period})")

    try:
        df = yf.download(
            tickers=yf_symbols,
            interval="1m",
            period=period,
            group_by="ticker",
            progress=False,
            threads=True,
        )
    except Exception as e:
        logger.error(f"yf.download failed: {e}")
        return []

    if df is None or df.empty:
        logger.warning("yf.download returned empty dataframe")
        return []

    all_bars: list[dict] = []

    # Single ticker: columns are flat (Open, High, Low, Close, Volume)
    # Multi ticker: columns are MultiIndex (ticker, field)
    if len(yf_symbols) == 1:
        sym = yf_symbols[0]
        orig = yf_to_orig.get(sym, sym)
        for idx, row in df.iterrows():
            dt = idx.to_pydatetime()
            close_val = row.get("Close")
            if close_val is None or (hasattr(close_val, '__len__') and len(close_val) == 0):
                continue
            try:
                close_f = float(close_val)
            except (TypeError, ValueError):
                continue
            if close_f <= 0:
                continue
            all_bars.append({
                "symbol": orig,
                "datetime": dt,
                "open": float(row.get("Open", 0) or 0),
                "high": float(row.get("High", 0) or 0),
                "low": float(row.get("Low", 0) or 0),
                "close": close_f,
                "volume": int(row.get("Volume", 0) or 0),
            })
    else:
        for sym in yf_symbols:
            orig = yf_to_orig.get(sym, sym)
            try:
                ticker_df = df[sym]
            except KeyError:
                logger.debug(f"No data for {sym} in batch download")
                continue

            for idx, row in ticker_df.iterrows():
                dt = idx.to_pydatetime()
                close_val = row.get("Close")
                if close_val is None or (hasattr(close_val, '__len__') and len(close_val) == 0):
                    continue
                try:
                    close_f = float(close_val)
                except (TypeError, ValueError):
                    continue
                if close_f <= 0:
                    continue
                all_bars.append({
                    "symbol": orig,
                    "datetime": dt,
                    "open": float(row.get("Open", 0) or 0),
                    "high": float(row.get("High", 0) or 0),
                    "low": float(row.get("Low", 0) or 0),
                    "close": close_f,
                    "volume": int(row.get("Volume", 0) or 0),
                })

    logger.info(f"Fetched {len(all_bars)} 1-min bars for {len(tickers)} symbols")
    return all_bars


def upsert_bars(bars: list[dict]) -> int:
    """Insert or update 1-minute bars in database."""
    if not bars:
        return 0

    db_url = get_db_url()
    with psycopg.connect(db_url) as conn:
        inserted = 0
        with conn.cursor() as cursor:
            for bar in bars:
                try:
                    cursor.execute("""
                        INSERT INTO market_bar_1min
                            (symbol, datetime, open, high, low, close, volume, source)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, 'yfinance')
                        ON CONFLICT (symbol, datetime)
                        DO UPDATE SET
                            open = EXCLUDED.open,
                            high = EXCLUDED.high,
                            low = EXCLUDED.low,
                            close = EXCLUDED.close,
                            volume = EXCLUDED.volume
                    """, (
                        bar['symbol'],
                        bar['datetime'],
                        bar['open'],
                        bar['high'],
                        bar['low'],
                        bar['close'],
                        bar['volume'],
                    ))
                    inserted += 1
                except Exception as e:
                    logger.error(f"Error inserting bar: {e}")
        conn.commit()
        return inserted


def run(tickers: list[str]):
    """Main pipeline entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s'
    )

    logger.info(f"Collecting 1-min bars for {len(tickers)} tickers")

    bars = fetch_minute_bars(tickers)
    total_inserted = upsert_bars(bars)

    logger.info(f"Total: {total_inserted} 1-min bars collected")

    if total_inserted:
        latest_at = max(bar["datetime"] for bar in bars)
        try:
            db_url = get_db_url()
            with psycopg.connect(db_url) as conn:
                notify_ingest(
                    conn,
                    NOTIFY_CHANNEL,
                    {
                        "source": "market_bar_1min",
                        "latest_at": latest_at.isoformat(),
                        "total_inserted": total_inserted,
                    },
                )
                conn.commit()
        except Exception as exc:
            logger.warning(f"Failed to notify ingest channel: {exc}")

    return total_inserted
