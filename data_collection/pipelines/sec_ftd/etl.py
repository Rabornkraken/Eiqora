"""SEC fails-to-deliver ETL parser (downloads and loads into Postgres)."""

from __future__ import annotations

import argparse
import csv
import io
import os
import re
import zipfile
from datetime import datetime, date

from bs4 import BeautifulSoup

from data_collection.common.config import get_env_list, load_common_settings
from data_collection.common.http import build_session, request_with_retries
from data_collection.db.connection import get_connection

FTD_URL_TEMPLATE = "https://www.sec.gov/files/data/fails-deliver-data/cnsfails{yyyymm}.zip"
FTD_INDEX_URL = "https://www.sec.gov/data-research/sec-markets-data/fails-deliver-data"


def _extract_month(url: str) -> str | None:
    match = re.search(r"cnsfails(\d{6})[a-z]?\.zip", url, re.IGNORECASE)
    if not match:
        return None
    return match.group(1)


def _discover_ftd_urls(session, settings) -> list[str]:
    response = request_with_retries(session, "GET", FTD_INDEX_URL, settings=settings)
    soup = BeautifulSoup(response.text, "html.parser")
    urls: set[str] = set()
    for link in soup.find_all("a"):
        href = link.get("href")
        if not href or ".zip" not in href.lower():
            continue
        if "cnsfails" not in href.lower():
            continue
        if href.startswith("/"):
            href = f"https://www.sec.gov{href}"
        urls.add(href)
    return sorted(urls)


def _parse_date(value: str) -> date | None:
    value = (value or "").strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _parse_float(value: str | None) -> float | None:
    if value is None:
        return None
    cleaned = value.replace("$", "").replace(",", "").strip()
    if cleaned in {"", "N/A", "NA"}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    cleaned = value.replace(",", "").strip()
    if cleaned in {"", "N/A", "NA"}:
        return None
    try:
        return int(float(cleaned))
    except ValueError:
        return None


def _parse_ftd_rows(text: str) -> list[tuple]:
    rows: list[tuple] = []
    reader = csv.reader(io.StringIO(text), delimiter="|")
    for row in reader:
        if not row:
            continue
        header = row[0].strip().lower()
        if "settlement" in header and "date" in header:
            continue
        if len(row) < 5:
            continue
        settlement_date = _parse_date(row[0])
        cusip = row[1].strip() if len(row) > 1 else None
        ticker = row[2].strip() if len(row) > 2 else None
        quantity = _parse_int(row[3]) if len(row) > 3 else None
        issuer_name = row[4].strip() if len(row) > 4 else None
        price = _parse_float(row[5]) if len(row) > 5 else None

        if not settlement_date or not cusip:
            continue

        rows.append((settlement_date, cusip, ticker, issuer_name, price, quantity))
    return rows


def _load_zip_content(content: bytes) -> list[tuple]:
    rows: list[tuple] = []
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        for name in zf.namelist():
            if name.endswith("/"):
                continue
            data = zf.read(name)
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                text = data.decode("latin-1")
            rows.extend(_parse_ftd_rows(text))
    return rows


def _upsert_rows(rows: list[tuple]) -> int:
    if not rows:
        return 0
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO sec_ftd
                    (settlement_date, cusip, ticker, issuer_name, price, quantity)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (settlement_date, cusip)
                DO UPDATE SET
                    ticker = EXCLUDED.ticker,
                    issuer_name = EXCLUDED.issuer_name,
                    price = EXCLUDED.price,
                    quantity = EXCLUDED.quantity
                """,
                rows,
            )
        conn.commit()
    return len(rows)


def run(months: list[str] | None = None) -> None:
    common = load_common_settings()
    user_agent = os.getenv("SEC_USER_AGENT") or common.http.user_agent
    if months is None:
        months = get_env_list("SEC_FTD_MONTHS", required=False)
    if not months:
        today = datetime.utcnow().date()
        start_year = today.year - 5
        months = []
        year = start_year
        month = today.month
        while (year < today.year) or (year == today.year and month <= today.month):
            months.append(f"{year:04d}{month:02d}")
            month += 1
            if month > 12:
                month = 1
                year += 1

    session = build_session(common.http)
    session.headers.update({"User-Agent": user_agent})
    month_set = set(months)
    urls = _discover_ftd_urls(session, common.http)
    if urls:
        urls = [url for url in urls if (_extract_month(url) in month_set)]
    if not urls:
        urls = [FTD_URL_TEMPLATE.format(yyyymm=yyyymm) for yyyymm in months]

    total_rows = 0
    for url in urls:
        yyyymm = _extract_month(url) or "unknown"
        try:
            response = request_with_retries(session, "GET", url, settings=common.http)
        except Exception as exc:
            print(f"sec_ftd_etl {yyyymm}: fetch failed ({exc})")
            continue
        rows = _load_zip_content(response.content)
        inserted = _upsert_rows(rows)
        total_rows += inserted
        print(f"sec_ftd_etl {yyyymm}: {inserted} rows")

    print(f"sec_ftd_etl complete: {total_rows} rows")


def main() -> None:
    parser = argparse.ArgumentParser(description="SEC FTD ETL parser")
    parser.add_argument("--months", help="Comma-separated list of YYYYMM to load")
    args = parser.parse_args()
    months = [m.strip() for m in args.months.split(",")] if args.months else None
    run(months=months)


if __name__ == "__main__":
    main()
