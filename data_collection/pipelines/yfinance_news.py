"""Yahoo Finance news ingestion pipeline."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone

import bs4
import urllib.parse

from data_collection.common.config import HttpSettings, load_common_settings
from data_collection.common.db import insert_raw_object
from data_collection.common.hashing import sha256_bytes
from data_collection.common.http import build_session, request_with_retries, HttpError
from data_collection.common.paths import utc_date_str
from data_collection.common.settings import load_config
from data_collection.common.symbols import get_config_symbols
from data_collection.common.time_utils import parse_date
from data_collection.common.storage import build_storage
from data_collection.db.connection import get_connection

_logger = logging.getLogger(__name__)
_thread_local = threading.local()


def _get_symbols(conn, asof_date: date | None, limit: int | None) -> list[str]:
    with conn.cursor() as cursor:
        if asof_date:
            cursor.execute(
                """
                SELECT symbol
                FROM universe_snapshot
                WHERE asof_date = %s
                """,
                (asof_date,),
            )
        else:
            cursor.execute("SELECT symbol FROM universe_member WHERE active = true")
        symbols = [row[0] for row in cursor.fetchall()]
        return symbols[:limit] if limit else symbols


def _parse_symbols(symbols: str | None) -> list[str]:
    if not symbols:
        return []
    return [item.strip() for item in symbols.split(",") if item.strip()]


def _extract_text(html: str) -> str:
    soup = bs4.BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return " ".join(soup.stripped_strings)


def _document_exists(conn, source: str, source_id: str) -> bool:
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM document WHERE source = %s AND source_id = %s",
            (source, source_id),
        )
        return cursor.fetchone() is not None


def _insert_document(conn, doc: dict, raw_id: int, text_object_key: str | None) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT doc_id FROM document WHERE source = %s AND source_id = %s",
            (doc["source"], doc["source_id"]),
        )
        if cursor.fetchone():
            return

        cursor.execute(
            """
            INSERT INTO document
                (source, source_id, doc_type, cik, ticker, title, published_at, url, raw_id, text_object_key, text)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                doc["source"],
                doc["source_id"],
                doc["doc_type"],
                doc.get("cik"),
                doc.get("ticker"),
                doc.get("title"),
                doc.get("published_at"),
                doc.get("url"),
                raw_id,
                text_object_key,
                doc.get("text"),
            ),
        )


def _get_thread_session(settings) -> object:
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


def _fetch_article_text(session, settings, storage, conn, url: str) -> tuple[str | None, str | None, int | None]:
    try:
        response = request_with_retries(session, "GET", url, settings=settings)
    except HttpError:
        return None, None, None
    content = response.content
    sha = sha256_bytes(content)
    object_key = f"yfinance/news_html/{sha}.html"
    stored = storage.write_bytes(object_key, content, content_type=response.headers.get("Content-Type"))
    raw_id = insert_raw_object(
        source="yfinance_news_article",
        object_key=stored.object_key,
        content_type=response.headers.get("Content-Type"),
        sha256=stored.sha256,
        http_status=response.status_code,
        meta={"url": url},
        conn=conn,
    )
    text = _extract_text(response.text)
    text_bytes = text.encode("utf-8") if text else b""
    text_sha = sha256_bytes(text_bytes) if text else None
    text_key = f"yfinance/news_text/{text_sha}.txt" if text_sha else None
    if text_key and text_bytes:
        storage.write_bytes(text_key, text_bytes, content_type="text/plain")
    return text, text_key, raw_id


def _fetch_article_text_threadsafe(settings, storage, conn, url: str) -> tuple[str | None, str | None, int | None]:
    session = _get_thread_session(settings)
    return _fetch_article_text(session, settings, storage, conn, url)


def _fetch_yahoo_news(session, settings, symbol: str, max_items: int) -> list[dict]:
    query = urllib.parse.quote(symbol)
    url = (
        "https://query1.finance.yahoo.com/v1/finance/search?"
        f"q={query}&newsCount={max_items}&enableFuzzyQuery=false"
    )
    response = request_with_retries(session, "GET", url, settings=settings)
    payload = response.json()
    return payload.get("news", []) or []


def run(
    *,
    asof_date: date | None = None,
    limit_symbols: int | None = None,
    max_news_per_symbol: int | None = None,
    symbols: list[str] | None = None,
) -> None:
    config = load_config()
    news_cfg = config.get("news", {}).get("yfinance", {})
    max_per_symbol = int(max_news_per_symbol or news_cfg.get("max_items_per_symbol", 25))
    min_text_chars = int(news_cfg.get("min_text_chars", 200))
    symbols_config, _, _ = get_config_symbols(config)

    common = load_common_settings()
    storage = build_storage(common)
    session = build_session(common.http)

    article_concurrency = int(os.getenv("YFINANCE_NEWS_CONCURRENCY", "6"))
    article_timeout = int(os.getenv("YFINANCE_ARTICLE_TIMEOUT_SECONDS", "8"))
    article_retries = int(os.getenv("YFINANCE_ARTICLE_MAX_RETRIES", "0"))
    article_backoff = float(os.getenv("YFINANCE_ARTICLE_BACKOFF_SECONDS", "0.5"))

    article_settings = HttpSettings(
        user_agent=common.http.user_agent,
        timeout_seconds=article_timeout,
        max_retries=article_retries,
        backoff_seconds=article_backoff,
    )

    with get_connection() as conn:
        symbol_list = symbols or symbols_config or _get_symbols(conn, asof_date=asof_date, limit=limit_symbols)
        _logger.info("yfinance_news start: symbols=%s", len(symbol_list))
        for symbol in symbol_list:
            _logger.info("yfinance_news symbol=%s start", symbol)
            items = _fetch_yahoo_news(session, common.http, symbol, max_per_symbol)
            raw_bytes = json.dumps(items, ensure_ascii=True).encode("utf-8")
            sha = sha256_bytes(raw_bytes)
            object_key = f"yfinance/news_list/{symbol}/{utc_date_str()}/{sha}.json"
            stored = storage.write_bytes(object_key, raw_bytes, content_type="application/json")
            insert_raw_object(
                source="yfinance_news_list",
                object_key=stored.object_key,
                content_type="application/json",
                sha256=stored.sha256,
                http_status=200,
                meta={"symbol": symbol},
                conn=conn,
            )

            candidates: list[tuple[dict, str, str]] = []
            for item in items[:max_per_symbol]:
                link = item.get("link") or item.get("url")
                if not link:
                    continue
                source_id = item.get("uuid") or hashlib.sha256(link.encode("utf-8")).hexdigest()
                if _document_exists(conn, "yfinance_news", source_id):
                    continue
                candidates.append((item, link, source_id))

            inserted = 0
            if candidates:
                with ThreadPoolExecutor(max_workers=article_concurrency) as executor:
                    future_map = {
                        executor.submit(_fetch_article_text_threadsafe, article_settings, storage, conn, link): (item, link, source_id)
                        for item, link, source_id in candidates
                    }
                    for future in as_completed(future_map):
                        item, link, source_id = future_map[future]
                        text, text_key, raw_id = future.result()
                        if not text or len(text) < min_text_chars:
                            continue
                        published = None
                        if item.get("providerPublishTime"):
                            try:
                                published = datetime.fromtimestamp(
                                    int(item["providerPublishTime"]), tz=timezone.utc
                                )
                            except Exception:
                                published = None

                        doc = {
                            "source": "yfinance_news",
                            "source_id": source_id,
                            "doc_type": "news_article",
                            "ticker": symbol,
                            "title": item.get("title"),
                            "published_at": published,
                            "url": link,
                            "text": text,
                        }
                        _insert_document(conn, doc, raw_id, text_key)
                        conn.commit()
                        inserted += 1
            _logger.info("yfinance_news symbol=%s inserted=%s candidates=%s", symbol, inserted, len(candidates))

        conn.commit()


def main() -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Yahoo Finance news pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", help="Run Yahoo Finance news ingestion")
    run_parser.add_argument("--asof-date", help="Universe snapshot date for symbols (YYYY-MM-DD)")
    run_parser.add_argument("--limit-symbols", type=int, help="Limit number of symbols")
    run_parser.add_argument("--max-news-per-symbol", type=int, help="Limit news per symbol")
    run_parser.add_argument("--symbols", help="Comma-separated list of symbols to ingest")
    args = parser.parse_args()
    if args.command == "run":
        asof_date = parse_date(args.asof_date) if args.asof_date else None
        symbol_list = _parse_symbols(args.symbols)
        run(
            asof_date=asof_date,
            limit_symbols=args.limit_symbols,
            max_news_per_symbol=args.max_news_per_symbol,
            symbols=symbol_list,
        )


if __name__ == "__main__":
    main()
