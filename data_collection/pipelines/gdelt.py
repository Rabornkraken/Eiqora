"""GDELT 2.0 strict-gated ingestion pipeline."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import bs4
import logging

from data_collection.common.db import insert_raw_object
from data_collection.common.hashing import sha256_bytes
from data_collection.common.http import build_session, request_with_retries, HttpError
from data_collection.common.config import HttpSettings
from data_collection.common.storage import build_storage
from data_collection.common.settings import load_config
from data_collection.common.symbols import get_config_symbols
from data_collection.db.connection import get_connection
from data_collection.common.config import load_common_settings

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
KEYWORDS = {
    "earnings",
    "guidance",
    "profit",
    "revenue",
    "sec",
    "filing",
    "dividend",
    "split",
    "merger",
    "acquisition",
    "bankruptcy",
    "downgrade",
    "upgrade",
    "lawsuit",
    "regulator",
    "antitrust",
    "inflation",
    "rate hike",
    "rate cut",
}

_thread_local = threading.local()
_logger = logging.getLogger(__name__)
_playwright_available = True
_stealth_available = False
_stealth_instance = None

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
except Exception:
    _playwright_available = False
else:
    try:
        from playwright_stealth import Stealth
    except Exception:
        _stealth_available = False
    else:
        _stealth_available = True
        _stealth_instance = Stealth()


def _get_active_symbols(conn, limit: int | None = None) -> list[str]:
    with conn.cursor() as cursor:
        cursor.execute("SELECT symbol FROM universe_member WHERE active = true")
        symbols = [row[0] for row in cursor.fetchall()]
        return symbols[:limit] if limit else symbols


def _score_article(symbol: str, title: str, text: str) -> tuple[float, dict[str, int]]:
    score = 0.0
    symbol_upper = symbol.upper()
    title_upper = title.upper()
    text_upper = text.upper()

    symbol_in_title = int(symbol_upper in title_upper)
    symbol_in_text = int(symbol_upper in text_upper)

    if symbol_upper in title_upper:
        score += 0.4
    if symbol_upper in text_upper:
        score += 0.4

    keyword_hits = 0
    for keyword in KEYWORDS:
        if keyword in text.lower() or keyword in title.lower():
            keyword_hits += 1
    if keyword_hits:
        score += min(0.2, 0.02 * keyword_hits)
    features = {
        "symbol_in_title": symbol_in_title,
        "symbol_in_text": symbol_in_text,
        "keyword_hits": keyword_hits,
    }
    return score, features


def _title_passes(symbol: str, title: str) -> bool:
    symbol_upper = symbol.upper()
    title_upper = title.upper()
    if symbol_upper in title_upper:
        return True
    lower_title = title.lower()
    return any(keyword in lower_title for keyword in KEYWORDS)


def _extract_text(html: str) -> str:
    soup = bs4.BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = " ".join(soup.stripped_strings)
    return re.sub(r"\s+", " ", text)


def _insert_document(conn, doc: dict[str, Any], raw_id: int, text_object_key: str | None) -> None:
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


def _document_exists(conn, source: str, source_id: str) -> bool:
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM document WHERE source = %s AND source_id = %s",
            (source, source_id),
        )
        return cursor.fetchone() is not None


def _get_thread_session(settings) -> Any:
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


def _fetch_article(
    url: str,
    settings,
) -> tuple[str | None, bytes | None, str | None]:
    session = _get_thread_session(settings)
    try:
        response = request_with_retries(session, "GET", url, settings=settings)
    except HttpError:
        return None, None, "http_error"
    return response.text, response.content, None


def _fetch_article_browser(
    url: str,
    timeout_seconds: int,
    user_agent: str,
    proxy: str | None,
    cdp_url: str | None,
) -> tuple[str | None, bytes | None, str | None]:
    if not _playwright_available:
        return None, None, "playwright_unavailable"
    try:
        with sync_playwright() as p:
            if cdp_url:
                browser = p.chromium.connect_over_cdp(cdp_url)
                context = browser.new_context(user_agent=user_agent)
            else:
                launch_kwargs = {"headless": True}
                if proxy:
                    launch_kwargs["proxy"] = {"server": proxy}
                browser = p.chromium.launch(**launch_kwargs)
                context = browser.new_context(user_agent=user_agent)
            page = context.new_page()
            if _stealth_available and _stealth_instance:
                _stealth_instance.apply_stealth_sync(page)
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_seconds * 1000)
            content = page.content()
            context.close()
            browser.close()
            return content, content.encode("utf-8"), None
    except PlaywrightTimeoutError:
        return None, None, "timeout"
    except Exception as exc:
        return None, None, f"browser_error:{type(exc).__name__}:{exc}"


def _resolve_datetime_range() -> tuple[str, str] | None:
    start_env = (os.getenv("GDELT_START_DATETIME") or "").strip().lower()
    end_env = (os.getenv("GDELT_END_DATETIME") or "").strip().lower()
    if start_env == "latest" or end_env == "latest" or not start_env or not end_env:
        end_dt = datetime.utcnow()
        lookback_hours = int(os.getenv("GDELT_LOOKBACK_HOURS", "24"))
        start_dt = end_dt - timedelta(hours=lookback_hours)
        return (
            start_dt.strftime("%Y%m%d%H%M%S"),
            end_dt.strftime("%Y%m%d%H%M%S"),
        )
    return (start_env, end_env)


def _slice_symbols(symbols: list[str], batch_size: int | None, batch_offset: int | None) -> list[str]:
    if batch_size is None:
        return symbols
    offset = batch_offset or 0
    return symbols[offset : offset + batch_size]


def _parse_domain_denylist() -> set[str]:
    raw = os.getenv("GDELT_DOMAIN_DENYLIST", "")
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def _domain_from_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def run(limit_symbols: int | None = None, max_docs: int | None = None) -> None:
    config = load_config()
    gdelt_cfg = config["news"]["gdelt"]
    symbols_config, _, _ = get_config_symbols(config)

    common = load_common_settings()
    storage = build_storage(common)
    session = build_session(common.http)

    embed_threshold = float(gdelt_cfg["embed_threshold"])
    min_chars = int(gdelt_cfg["min_text_chars"])
    max_docs = int(max_docs if max_docs is not None else gdelt_cfg["max_docs_per_symbol_per_poll"])

    batch_size_env = os.getenv("GDELT_BATCH_SIZE")
    batch_offset_env = os.getenv("GDELT_BATCH_OFFSET")
    batch_size = int(batch_size_env) if batch_size_env else None
    batch_offset = int(batch_offset_env) if batch_offset_env else None
    gdelt_range = _resolve_datetime_range()
    article_concurrency = int(os.getenv("GDELT_ARTICLE_CONCURRENCY", "8"))
    article_timeout = int(os.getenv("GDELT_ARTICLE_TIMEOUT_SECONDS", "8"))
    article_retries = int(os.getenv("GDELT_ARTICLE_MAX_RETRIES", "0"))
    article_backoff = float(os.getenv("GDELT_ARTICLE_BACKOFF_SECONDS", "0.5"))
    fetch_multiplier = int(os.getenv("GDELT_MAX_FETCH_MULTIPLIER", "2"))
    prefilter_titles = os.getenv("GDELT_PREFILTER_TITLES", "1").strip() != "0"
    domain_denylist = _parse_domain_denylist()

    article_settings = HttpSettings(
        user_agent=common.http.user_agent,
        timeout_seconds=article_timeout,
        max_retries=article_retries,
        backoff_seconds=article_backoff,
    )
    browser_primary = os.getenv("GDELT_BROWSER_PRIMARY", "0").strip() == "1"
    browser_fallback = os.getenv("GDELT_BROWSER_FALLBACK", "0").strip() == "1"
    browser_timeout = int(os.getenv("GDELT_BROWSER_TIMEOUT_SECONDS", "15"))
    browser_proxy = os.getenv("GDELT_BROWSER_PROXY")
    cdp_url = os.getenv("GDELT_CDP_URL")

    with get_connection() as conn:
        symbols = symbols_config or _get_active_symbols(conn, limit=limit_symbols)
        symbols = _slice_symbols(symbols, batch_size, batch_offset)
        _logger.info(
            "gdelt start: symbols=%s batch_size=%s batch_offset=%s lookback_hours=%s concurrency=%s",
            len(symbols),
            batch_size,
            batch_offset,
            os.getenv("GDELT_LOOKBACK_HOURS", "24"),
            article_concurrency,
        )
        for symbol in symbols:
            seen_urls: set[str] = set()
            _logger.info("gdelt symbol=%s start", symbol)
            params = {
                "query": symbol,
                "mode": "ArtList",
                "format": "json",
                "maxrecords": max_docs * 3,
            }
            if gdelt_range:
                params["startdatetime"], params["enddatetime"] = gdelt_range
            response = request_with_retries(session, "GET", GDELT_URL, settings=common.http, params=params)
            content_type = response.headers.get("Content-Type", "")
            if "json" not in content_type:
                continue
            try:
                payload = response.json()
            except Exception:
                continue
            raw_bytes = json.dumps(payload, ensure_ascii=True).encode("utf-8")
            sha = sha256_bytes(raw_bytes)
            object_key = f"gdelt/{symbol}/{datetime.utcnow().date().isoformat()}/{sha}.json"
            stored = storage.write_bytes(object_key, raw_bytes, content_type="application/json")
            raw_id = insert_raw_object(
                source="gdelt_query",
                object_key=stored.object_key,
                content_type="application/json",
                sha256=stored.sha256,
                http_status=response.status_code,
                meta={"symbol": symbol, "params": params},
                conn=conn,
            )

            articles = payload.get("articles", [])
            candidates: list[tuple[dict[str, Any], str, str]] = []
            for article in articles:
                url = article.get("url")
                title = article.get("title") or ""
                if not url:
                    continue
                if url in seen_urls:
                    continue
                domain = _domain_from_url(url)
                if domain in domain_denylist:
                    continue
                if prefilter_titles and not _title_passes(symbol, title):
                    continue
                seen_urls.add(url)
                source_id = hashlib.sha256(url.encode("utf-8")).hexdigest()
                if _document_exists(conn, "gdelt", source_id):
                    continue
                candidates.append((article, url, title))
                if len(candidates) >= max_docs * fetch_multiplier:
                    break

            accepted = 0
            fetch_failed = 0
            fetch_error_counts: dict[str, int] = {}
            fetch_failed_browser = 0
            fetch_browser_error_counts: dict[str, int] = {}
            browser_success = 0
            too_short = 0
            score_below = 0
            failed_candidates: list[tuple[dict[str, Any], str, str]] = []
            if candidates:
                if browser_primary:
                    if not _playwright_available:
                        _logger.info("gdelt playwright not available; falling back to http")
                    else:
                        for article, url, title in candidates:
                            if accepted >= max_docs:
                                break
                            html_text, raw_bytes, error = _fetch_article_browser(
                                url, browser_timeout, common.http.user_agent, browser_proxy, cdp_url
                            )
                            if error:
                                fetch_failed_browser += 1
                                fetch_browser_error_counts[error] = fetch_browser_error_counts.get(error, 0) + 1
                                failed_candidates.append((article, url, title))
                                continue
                            browser_success += 1
                            text = _extract_text(html_text)
                            if len(text) < min_chars:
                                too_short += 1
                                continue

                            score, features = _score_article(symbol, title, text)
                            if score < embed_threshold:
                                score_below += 1
                                continue

                            text_bytes = text.encode("utf-8")
                            text_sha = sha256_bytes(text_bytes)
                            text_key = f"gdelt/text/{symbol}/{text_sha}.txt"
                            storage.write_bytes(text_key, text_bytes, content_type="text/plain")

                            source_id = hashlib.sha256(url.encode("utf-8")).hexdigest()
                            published = None
                            if article.get("seendate"):
                                try:
                                    published = datetime.strptime(article["seendate"], "%Y%m%dT%H%M%SZ")
                                except Exception:
                                    published = None

                            doc = {
                                "source": "gdelt",
                                "source_id": source_id,
                                "doc_type": "news_article",
                                "ticker": symbol,
                                "title": title,
                                "published_at": published,
                                "url": url,
                                "text": text,
                            }
                            _insert_document(conn, doc, raw_id, text_key)
                            with conn.cursor() as cursor:
                                cursor.execute(
                                    """
                                    INSERT INTO news_relevance (doc_id, score, features_json, model_version, scored_at)
                                    VALUES (
                                        (SELECT doc_id FROM document WHERE source = %s AND source_id = %s),
                                        %s,
                                        %s,
                                        %s,
                                        now()
                                    )
                                    ON CONFLICT (doc_id)
                                    DO UPDATE SET score = EXCLUDED.score, features_json = EXCLUDED.features_json, scored_at = now()
                                    """,
                                    (
                                        doc["source"],
                                        doc["source_id"],
                                        score,
                                        json.dumps(features),
                                        "gdelt_v1",
                                    ),
                                )
                            accepted += 1
                            conn.commit()

                if not browser_primary or (browser_primary and failed_candidates and browser_fallback):
                    with ThreadPoolExecutor(max_workers=article_concurrency) as executor:
                        targets = failed_candidates if browser_primary else candidates
                        future_map = {
                            executor.submit(_fetch_article, url, article_settings): (article, url, title)
                            for article, url, title in targets
                        }
                        for future in as_completed(future_map):
                            if accepted >= max_docs:
                                break
                            article, url, title = future_map[future]
                            html_text, raw_bytes, error = future.result()
                            if error:
                                fetch_failed += 1
                                fetch_error_counts[error] = fetch_error_counts.get(error, 0) + 1
                                continue
                            text = _extract_text(html_text)
                            if len(text) < min_chars:
                                too_short += 1
                                continue

                            score, features = _score_article(symbol, title, text)
                            if score < embed_threshold:
                                score_below += 1
                                continue

                            text_bytes = text.encode("utf-8")
                            text_sha = sha256_bytes(text_bytes)
                            text_key = f"gdelt/text/{symbol}/{text_sha}.txt"
                            storage.write_bytes(text_key, text_bytes, content_type="text/plain")

                            source_id = hashlib.sha256(url.encode("utf-8")).hexdigest()
                            published = None
                            if article.get("seendate"):
                                try:
                                    published = datetime.strptime(article["seendate"], "%Y%m%dT%H%M%SZ")
                                except Exception:
                                    published = None

                            doc = {
                                "source": "gdelt",
                                "source_id": source_id,
                                "doc_type": "news_article",
                                "ticker": symbol,
                                "title": title,
                                "published_at": published,
                                "url": url,
                                "text": text,
                            }
                            _insert_document(conn, doc, raw_id, text_key)
                            with conn.cursor() as cursor:
                                cursor.execute(
                                    """
                                    INSERT INTO news_relevance (doc_id, score, features_json, model_version, scored_at)
                                    VALUES (
                                        (SELECT doc_id FROM document WHERE source = %s AND source_id = %s),
                                        %s,
                                        %s,
                                        %s,
                                        now()
                                    )
                                    ON CONFLICT (doc_id)
                                    DO UPDATE SET score = EXCLUDED.score, features_json = EXCLUDED.features_json, scored_at = now()
                                    """,
                                    (
                                        doc["source"],
                                        doc["source_id"],
                                        score,
                                        json.dumps(features),
                                        "gdelt_v1",
                                    ),
                                )
                            accepted += 1
                            conn.commit()
            _logger.info(
                "gdelt symbol=%s candidates=%s accepted=%s fetch_failed=%s fetch_failed_browser=%s browser_success=%s too_short=%s score_below=%s fetch_errors=%s browser_errors=%s",
                symbol,
                len(candidates),
                accepted,
                fetch_failed,
                fetch_failed_browser,
                browser_success,
                too_short,
                score_below,
                fetch_error_counts,
                fetch_browser_error_counts,
            )
            conn.commit()


def main() -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="GDELT strict gated pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", help="Run GDELT pipeline")
    run_parser.add_argument("--limit-symbols", type=int, help="Limit number of symbols")
    run_parser.add_argument("--max-docs", type=int, help="Limit docs per symbol")
    args = parser.parse_args()
    if args.command == "run":
        run(limit_symbols=args.limit_symbols, max_docs=args.max_docs)


if __name__ == "__main__":
    main()
