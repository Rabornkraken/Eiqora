"""Corporate actions via HTML crawling (stockanalysis.com)."""

from __future__ import annotations

import argparse
import csv
import logging
import os
import random
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable
from io import StringIO

import bs4
import requests

from data_collection.common.config import load_common_settings, HttpSettings
from data_collection.common.db import insert_raw_object
from data_collection.common.hashing import sha256_bytes
from data_collection.common.http import build_session, request_with_retries
from data_collection.common.paths import utc_date_str
from data_collection.common.settings import load_config
from data_collection.common.storage import build_storage
from data_collection.common.symbols import get_config_symbols
from data_collection.common.time_utils import parse_date
from data_collection.db.connection import get_connection

_logger = logging.getLogger(__name__)
YAHOO_DOWNLOAD = "https://query1.finance.yahoo.com/v7/finance/download/{ticker}"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:109.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
]


@dataclass(frozen=True)
class CorporateActionRow:
    ticker: str
    action_type: str
    ex_date: date | None
    pay_date: date | None
    ratio: float | None
    cash_amount: float | None
    source_ref: str


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _parse_cash_amount(value: str | None) -> float | None:
    if not value:
        return None
    stripped = value.strip().replace("$", "").replace(",", "")
    try:
        return float(stripped)
    except ValueError:
        return None


def _parse_ratio(value: str | None) -> float | None:
    if not value:
        return None
    raw = value.strip().lower()
    raw = raw.replace("for", ":").replace(" ", "")
    if ":" in raw:
        left, right = raw.split(":", 1)
        try:
            return float(left) / float(right)
        except ValueError:
            return None
    try:
        return float(raw)
    except ValueError:
        return None


def _fetch_html(
    session: requests.Session,
    url: str,
    *,
    use_browser: bool,
    proxy: str | None,
    user_agent: str | None,
    settings: HttpSettings,
) -> tuple[bytes, int]:
    if not use_browser:
        try:
            response = request_with_retries(session, "GET", url, settings=settings)
            return response.content, response.status_code
        except Exception as e:
            _logger.warning("Error fetching %s: %s", url, e)
            return b"", getattr(e, "response", None).status_code if hasattr(e, "response") and getattr(e, "response", None) else 500

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser_type = p.chromium
        proxy_cfg = {"server": proxy} if proxy else None
        browser = browser_type.launch(headless=True, proxy=proxy_cfg)
        context = browser.new_context(user_agent=user_agent)
        page = context.new_page()
        try:
            from playwright_stealth import Stealth

            Stealth().apply_stealth_sync(page)
        except Exception:
            pass
        page.goto(url, wait_until="domcontentloaded", timeout=20000)
        content = page.content().encode("utf-8")
        page.close()
        context.close()
        browser.close()
        return content, 200


def _store_raw(
    *,
    storage,
    conn,
    source: str,
    object_key_prefix: str,
    payload: bytes,
    status_code: int,
    meta: dict,
) -> None:
    sha = sha256_bytes(payload)
    object_key = f"{object_key_prefix}/{sha}.html"
    stored = storage.write_bytes(object_key, payload, content_type="text/html")
    insert_raw_object(
        source=source,
        object_key=stored.object_key,
        content_type="text/html",
        sha256=stored.sha256,
        http_status=status_code,
        meta=meta,
        conn=conn,
    )


def _normalize_header(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


def _find_table_by_headers(soup: bs4.BeautifulSoup, headers: Iterable[str]) -> bs4.Tag | None:
    wanted = [_normalize_header(h) for h in headers]
    for table in soup.find_all("table"):
        header_cells = table.find_all("th")
        header_text = [_normalize_header(cell.get_text(strip=True)) for cell in header_cells]
        if all(header in header_text for header in wanted):
            return table
    return None


def _parse_dividends(ticker: str, html: bytes) -> list[CorporateActionRow]:
    soup = bs4.BeautifulSoup(html, "html.parser")
    table = _find_table_by_headers(soup, ["Ex Dividend Date", "Cash Amount"])
    if not table:
        return []
    rows: list[CorporateActionRow] = []
    for tr in table.find_all("tr")[1:]:
        cols = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
        if len(cols) < 4:
            continue
        ex_date = _parse_date(cols[0])
        pay_date = _parse_date(cols[2]) if len(cols) > 2 else None
        amount = _parse_cash_amount(cols[3]) if len(cols) > 3 else None
        source_ref = f"{ticker}/dividend/{cols[0]}:{cols[3]}"
        rows.append(
            CorporateActionRow(
                ticker=ticker,
                action_type="dividend",
                ex_date=ex_date,
                pay_date=pay_date,
                ratio=None,
                cash_amount=amount,
                source_ref=source_ref,
            )
        )
    return rows


def _parse_splits(ticker: str, html: bytes) -> list[CorporateActionRow]:
    soup = bs4.BeautifulSoup(html, "html.parser")
    table = _find_table_by_headers(soup, ["Date", "Split Ratio"])
    if not table:
        return []
    rows: list[CorporateActionRow] = []
    for tr in table.find_all("tr")[1:]:
        cols = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
        if len(cols) < 2:
            continue
        ex_date = _parse_date(cols[0])
        ratio = _parse_ratio(cols[1])
        source_ref = f"{ticker}/split/{cols[0]}:{cols[1]}"
        rows.append(
            CorporateActionRow(
                ticker=ticker,
                action_type="split",
                ex_date=ex_date,
                pay_date=None,
                ratio=ratio,
                cash_amount=None,
                source_ref=source_ref,
            )
        )
    return rows


def _filter_by_date(rows: list[CorporateActionRow], start: date | None, end: date | None) -> list[CorporateActionRow]:
    if not start and not end:
        return rows
    filtered = []
    for row in rows:
        if not row.ex_date:
            continue
        if start and row.ex_date < start:
            continue
        if end and row.ex_date > end:
            continue
        filtered.append(row)
    return filtered


def _yahoo_symbol(ticker: str) -> str:
    return ticker.replace(".", "-")


def _fetch_yahoo_csv(
    session: requests.Session,
    ticker: str,
    event: str,
    start: date | None,
    end: date | None,
    settings: HttpSettings,
) -> tuple[str, int]:
    period1 = 0
    period2 = int(datetime.utcnow().timestamp())
    if start:
        period1 = int(datetime.combine(start, datetime.min.time()).timestamp())
    if end:
        period2 = int(datetime.combine(end, datetime.max.time()).timestamp())
    params = {
        "period1": str(period1),
        "period2": str(period2),
        "interval": "1d",
        "events": event,
        "includeAdjustedClose": "true",
    }
    url = YAHOO_DOWNLOAD.format(ticker=_yahoo_symbol(ticker))
    try:
        response = request_with_retries(session, "GET", url, params=params, settings=settings)
        return response.text, response.status_code
    except Exception as e:
        _logger.warning("Error fetching Yahoo CSV for %s: %s", ticker, e)
        return "", getattr(e, "response", None).status_code if hasattr(e, "response") and getattr(e, "response", None) else 500


def _parse_yahoo_dividends(ticker: str, csv_text: str) -> list[CorporateActionRow]:
    reader = csv.DictReader(StringIO(csv_text))
    rows: list[CorporateActionRow] = []
    for row in reader:
        ex_date = _parse_date(row.get("Date"))
        amount = _parse_cash_amount(row.get("Dividends"))
        if not ex_date and amount is None:
            continue
        source_ref = f"{ticker}/dividend/{row.get('Date')}:{row.get('Dividends')}"
        rows.append(
            CorporateActionRow(
                ticker=ticker,
                action_type="dividend",
                ex_date=ex_date,
                pay_date=None,
                ratio=None,
                cash_amount=amount,
                source_ref=source_ref,
            )
        )
    return rows


def _parse_yahoo_splits(ticker: str, csv_text: str) -> list[CorporateActionRow]:
    reader = csv.DictReader(StringIO(csv_text))
    rows: list[CorporateActionRow] = []
    for row in reader:
        ex_date = _parse_date(row.get("Date"))
        ratio = _parse_ratio(row.get("Stock Splits"))
        if not ex_date and ratio is None:
            continue
        source_ref = f"{ticker}/split/{row.get('Date')}:{row.get('Stock Splits')}"
        rows.append(
            CorporateActionRow(
                ticker=ticker,
                action_type="split",
                ex_date=ex_date,
                pay_date=None,
                ratio=ratio,
                cash_amount=None,
                source_ref=source_ref,
            )
        )
    return rows


def _upsert_actions(conn, cik_map: dict[str, str], rows: list[CorporateActionRow]) -> int:
    if not rows:
        return 0
    inserted = 0
    with conn.cursor() as cursor:
        for row in rows:
            cik = cik_map.get(row.ticker)
            cursor.execute(
                """
                INSERT INTO corporate_action
                    (cik, ticker, action_type, ex_date, pay_date, ratio, cash_amount, currency, source, source_ref)
                SELECT %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                WHERE NOT EXISTS (
                    SELECT 1 FROM corporate_action
                    WHERE ticker = %s
                      AND action_type = %s
                      AND ex_date IS NOT DISTINCT FROM %s
                      AND pay_date IS NOT DISTINCT FROM %s
                      AND ratio IS NOT DISTINCT FROM %s
                      AND cash_amount IS NOT DISTINCT FROM %s
                      AND source = %s
                )
                """,
                (
                    cik,
                    row.ticker,
                    row.action_type,
                    row.ex_date,
                    row.pay_date,
                    row.ratio,
                    row.cash_amount,
                    "USD",
                    "stockanalysis",
                    row.source_ref,
                    row.ticker,
                    row.action_type,
                    row.ex_date,
                    row.pay_date,
                    row.ratio,
                    row.cash_amount,
                    "stockanalysis",
                ),
            )
            if cursor.rowcount:
                inserted += 1
    return inserted


def run(start: date | None, end: date | None) -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    config = load_config()
    symbols, _, _ = get_config_symbols(config)
    if not symbols:
        raise RuntimeError("No symbols configured for corporate actions crawling")

    common = load_common_settings()
    storage = build_storage(common)
    session = build_session(common.http)
    user_agent = os.getenv("DATA_COLLECTION_USER_AGENT") or common.http.user_agent
    if user_agent:
        session.headers.update({"User-Agent": user_agent})

    use_browser = os.getenv("CORP_ACTIONS_BROWSER_PRIMARY", "0") == "1"
    proxy = os.getenv("CORP_ACTIONS_BROWSER_PROXY")

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT ticker, cik FROM security WHERE cik IS NOT NULL")
            cik_map = {row[0]: row[1] for row in cursor.fetchall()}

        for ticker in symbols:
            # Rotate User-Agent
            ua = random.choice(USER_AGENTS)
            session.headers.update({"User-Agent": ua})

            ticker_lower = ticker.lower()
            dividend_url = f"https://stockanalysis.com/stocks/{ticker_lower}/dividend/"
            split_url = f"https://stockanalysis.com/stocks/{ticker_lower}/split/"

            for url, parser, source in (
                (dividend_url, _parse_dividends, "stockanalysis_dividend"),
                (split_url, _parse_splits, "stockanalysis_split"),
            ):
                # Small jittered sleep between requests
                time.sleep(random.uniform(1.0, 3.0))

                payload, status_code = _fetch_html(
                    session,
                    url,
                    use_browser=use_browser,
                    proxy=proxy,
                    user_agent=ua,
                    settings=common.http,
                )
                _store_raw(
                    storage=storage,
                    conn=conn,
                    source=source,
                    object_key_prefix=f"corporate_actions/stockanalysis/{ticker}/{utc_date_str()}",
                    payload=payload,
                    status_code=status_code,
                    meta={"ticker": ticker, "url": url},
                )
                rows = parser(ticker, payload)
                rows = _filter_by_date(rows, start, end)
                inserted = _upsert_actions(conn, cik_map, rows)
                _logger.info(
                    "corp_actions ticker=%s source=%s fetched=%s parsed=%s inserted=%s",
                    ticker,
                    source,
                    status_code,
                    len(rows),
                    inserted,
                )
                if (status_code >= 400 or not rows) and source == "stockanalysis_split":
                    time.sleep(random.uniform(0.5, 1.5))
                    csv_text, csv_status = _fetch_yahoo_csv(session, ticker, "split", start, end, settings=common.http)
                    _store_raw(
                        storage=storage,
                        conn=conn,
                        source="yahoo_split",
                        object_key_prefix=f"corporate_actions/yahoo/{ticker}/{utc_date_str()}",
                        payload=csv_text.encode("utf-8", errors="ignore"),
                        status_code=csv_status,
                        meta={"ticker": ticker, "url": YAHOO_DOWNLOAD.format(ticker=_yahoo_symbol(ticker)), "event": "split"},
                    )
                    yahoo_rows = _parse_yahoo_splits(ticker, csv_text)
                    inserted = _upsert_actions(conn, cik_map, yahoo_rows)
                    _logger.info(
                        "corp_actions ticker=%s source=yahoo_split fetched=%s parsed=%s inserted=%s",
                        ticker,
                        csv_status,
                        len(yahoo_rows),
                        inserted,
                    )
                if (status_code >= 400 or not rows) and source == "stockanalysis_dividend":
                    time.sleep(random.uniform(0.5, 1.5))
                    csv_text, csv_status = _fetch_yahoo_csv(session, ticker, "div", start, end, settings=common.http)
                    _store_raw(
                        storage=storage,
                        conn=conn,
                        source="yahoo_dividend",
                        object_key_prefix=f"corporate_actions/yahoo/{ticker}/{utc_date_str()}",
                        payload=csv_text.encode("utf-8", errors="ignore"),
                        status_code=csv_status,
                        meta={"ticker": ticker, "url": YAHOO_DOWNLOAD.format(ticker=_yahoo_symbol(ticker)), "event": "div"},
                    )
                    yahoo_rows = _parse_yahoo_dividends(ticker, csv_text)
                    inserted = _upsert_actions(conn, cik_map, yahoo_rows)
                    _logger.info(
                        "corp_actions ticker=%s source=yahoo_dividend fetched=%s parsed=%s inserted=%s",
                        ticker,
                        csv_status,
                        len(yahoo_rows),
                        inserted,
                    )
            conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Corporate actions crawler (stockanalysis)")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", help="Crawl corporate actions")
    run_parser.add_argument("--start", help="Start date (YYYY-MM-DD)", default=None)
    run_parser.add_argument("--end", help="End date (YYYY-MM-DD)", default=None)

    args = parser.parse_args()
    if args.command == "run":
        run(
            parse_date(args.start) if args.start else None,
            parse_date(args.end) if args.end else None,
        )


if __name__ == "__main__":
    main()
