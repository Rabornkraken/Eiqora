"""SEC RSS fast-lane for 8-K / 13D filings."""

from __future__ import annotations

import argparse
import html
import logging
import os
import re
from datetime import date, datetime, timedelta
from typing import Any
from xml.etree import ElementTree

from data_collection.common.config import get_env_list, load_common_settings
from data_collection.common.db import insert_raw_object
from data_collection.common.hashing import sha256_bytes
from data_collection.common.http import build_session, request_with_retries
from data_collection.common.paths import utc_date_str
from data_collection.common.settings import load_config
from data_collection.common.storage import build_storage
from data_collection.common.symbols import get_config_symbols
from data_collection.db.connection import get_connection

_logger = logging.getLogger(__name__)
ATOM_NS = {"a": "http://www.w3.org/2005/Atom"}
SEC_RSS_URL = "https://www.sec.gov/cgi-bin/browse-edgar"
SEC_DAILY_INDEX = "https://www.sec.gov/Archives/edgar/daily-index/{year}/QTR{quarter}/master.{datestr}.idx"


def _clean_cik(value: str | None) -> str | None:
    if not value:
        return None
    digits = "".join(ch for ch in value if ch.isdigit())
    if not digits:
        return None
    return digits.zfill(10)


def _extract_cik(title: str | None) -> str | None:
    if not title:
        return None
    match = re.search(r"\((\d{4,10})\)", title)
    if not match:
        return None
    return _clean_cik(match.group(1))


def _extract_accession(entry_id: str | None, summary: str | None) -> str | None:
    if entry_id and "accession-number=" in entry_id:
        return entry_id.split("accession-number=")[-1]
    if summary:
        match = re.search(r"AccNo:</b>\\s*([0-9-]+)", summary)
        if match:
            return match.group(1)
    return None


def _extract_filed_date(summary: str | None, updated_text: str | None) -> str | None:
    if summary:
        match = re.search(r"Filed:</b>\\s*(\\d{4}-\\d{2}-\\d{2})", summary)
        if match:
            return match.group(1)
    if updated_text:
        try:
            return datetime.fromisoformat(updated_text.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            return None
    return None


def _insert_sec_filing(
    conn,
    *,
    accession: str,
    cik: str,
    form_type: str,
    filed_at: str | None,
    primary_doc_url: str | None,
    raw_id: int | None,
    description: str | None,
) -> bool:
    filed_date = datetime.strptime(filed_at, "%Y-%m-%d").date() if filed_at else None
    is_amendment = form_type.endswith("/A")
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO sec_filing
                (accession, cik, form_type, filed_at, report_period, is_amendment, amends_accession, primary_doc_url, raw_id, description)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (accession)
            DO UPDATE SET
                cik = EXCLUDED.cik,
                form_type = EXCLUDED.form_type,
                filed_at = EXCLUDED.filed_at,
                is_amendment = EXCLUDED.is_amendment,
                primary_doc_url = EXCLUDED.primary_doc_url,
                raw_id = EXCLUDED.raw_id,
                description = EXCLUDED.description
            RETURNING (xmax = 0) AS inserted
            """,
            (
                accession,
                cik,
                form_type,
                filed_date,
                None,
                is_amendment,
                None,
                primary_doc_url,
                raw_id,
                description,
            ),
        )
        row = cursor.fetchone()
        return bool(row[0]) if row else False


def _load_allowed_ciks(conn, symbols: list[str] | None, enable_filter: bool) -> set[str] | None:
    if not enable_filter or not symbols:
        return None
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT DISTINCT cik FROM security WHERE ticker = ANY(%s) AND cik IS NOT NULL",
            (symbols,),
        )
        rows = [row[0] for row in cursor.fetchall()]
    return set(rows) if rows else None


def _parse_feed(content: bytes) -> list[dict[str, Any]]:
    root = ElementTree.fromstring(content)
    entries: list[dict[str, Any]] = []
    for entry in root.findall("a:entry", ATOM_NS):
        title = entry.findtext("a:title", default="", namespaces=ATOM_NS)
        summary = entry.findtext("a:summary", default="", namespaces=ATOM_NS)
        updated = entry.findtext("a:updated", default="", namespaces=ATOM_NS)
        entry_id = entry.findtext("a:id", default="", namespaces=ATOM_NS)
        link = entry.find("a:link", ATOM_NS)
        link_href = link.attrib.get("href") if link is not None else None
        category = entry.find("a:category", ATOM_NS)
        form_type = category.attrib.get("term") if category is not None else None

        summary_clean = html.unescape(summary or "")
        entries.append(
            {
                "title": title,
                "summary": summary_clean,
                "updated": updated,
                "entry_id": entry_id,
                "link": link_href,
                "form_type": form_type,
            }
        )
    return entries


def _quarter_for_month(month: int) -> int:
    if 1 <= month <= 3:
        return 1
    if 4 <= month <= 6:
        return 2
    if 7 <= month <= 9:
        return 3
    return 4


def _daily_index_url(target_date: date) -> str:
    return SEC_DAILY_INDEX.format(
        year=target_date.year,
        quarter=_quarter_for_month(target_date.month),
        datestr=target_date.strftime("%Y%m%d"),
    )


def _parse_daily_index(text: str) -> list[dict[str, str]]:
    lines = text.splitlines()
    start_idx = 0
    for idx, line in enumerate(lines):
        if line.strip().startswith("CIK|Company Name|Form Type|Date Filed|File Name"):
            start_idx = idx + 1
            break
    records = []
    for line in lines[start_idx:]:
        if not line or "|" not in line:
            continue
        parts = line.split("|")
        if len(parts) < 5:
            continue
        records.append(
            {
                "cik": _clean_cik(parts[0]),
                "company_name": parts[1],
                "form_type": parts[2],
                "filed_at": parts[3],
                "file_name": parts[4],
            }
        )
    return records


def _extract_accession_from_path(path: str) -> str | None:
    match = re.search(r"(\\d{10}-\\d{2}-\\d{6})", path)
    if match:
        return match.group(1)
    return None


def run(forms: list[str] | None) -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    config = load_config()
    sec_cfg = config.get("sec", {})
    if not forms:
        forms = get_env_list("SEC_RSS_FORMS", required=False) or ["8-K", "SC 13D", "SC 13D/A"]
    lookback_days = int(os.getenv("SEC_RSS_LOOKBACK_DAYS", "0") or 0)

    common = load_common_settings()
    storage = build_storage(common)
    session = build_session(common.http)
    user_agent = os.getenv("SEC_USER_AGENT") or sec_cfg.get("user_agent") or common.http.user_agent
    session.headers.update({"User-Agent": user_agent})

    symbols, _, _ = get_config_symbols(config)
    enable_filter = os.getenv("SEC_RSS_FILTER", "1") != "0"
    with get_connection() as conn:
        allowed_ciks = _load_allowed_ciks(conn, symbols, enable_filter)
        if enable_filter and symbols and not allowed_ciks:
            _logger.info("sec_rss no cik matches for configured symbols; ingesting without filter")
            allowed_ciks = None

        total_processed = 0
        total_inserted = 0
        total_updated = 0
        for form in forms:
            params = {
                "action": "getcurrent",
                "type": form,
                "output": "atom",
            }
            response = request_with_retries(session, "GET", SEC_RSS_URL, settings=common.http, params=params)
            content = response.content
            sha = sha256_bytes(content)
            object_key = f"sec/rss/{form}/{utc_date_str()}/{sha}.xml"
            stored = storage.write_bytes(object_key, content, content_type="application/atom+xml")
            raw_id = insert_raw_object(
                source="sec_rss",
                object_key=stored.object_key,
                content_type="application/atom+xml",
                sha256=stored.sha256,
                http_status=response.status_code,
                meta={"form": form, "url": SEC_RSS_URL},
                conn=conn,
            )

            entries = _parse_feed(content)
            processed = 0
            inserted = 0
            updated = 0
            skipped_no_accession = 0
            skipped_no_cik = 0
            skipped_filtered = 0
            for item in entries:
                accession = _extract_accession(item.get("entry_id"), item.get("summary"))
                if not accession:
                    skipped_no_accession += 1
                    continue
                cik = _extract_cik(item.get("title"))
                if not cik:
                    skipped_no_cik += 1
                    continue
                if allowed_ciks is not None and cik not in allowed_ciks:
                    skipped_filtered += 1
                    continue
                form_type = item.get("form_type") or form
                filed_at = _extract_filed_date(item.get("summary"), item.get("updated"))
                link = item.get("link")

                if _insert_sec_filing(
                    conn,
                    accession=accession,
                    cik=cik,
                    form_type=form_type,
                    filed_at=filed_at,
                    primary_doc_url=link,
                    raw_id=raw_id,
                    description=item.get("title"),  # Use title as description
                ):
                    inserted += 1
                else:
                    updated += 1
                processed += 1

            total_processed += processed
            total_inserted += inserted
            total_updated += updated
            _logger.info(
                "sec_rss form=%s entries=%s processed=%s inserted=%s updated=%s skipped_no_accession=%s skipped_no_cik=%s skipped_filtered=%s",
                form,
                len(entries),
                processed,
                inserted,
                updated,
                skipped_no_accession,
                skipped_no_cik,
                skipped_filtered,
            )

        if lookback_days > 0:
            start_date = datetime.utcnow().date() - timedelta(days=lookback_days)
            for offset in range(lookback_days + 1):
                target_date = start_date + timedelta(days=offset)
                url = _daily_index_url(target_date)
                try:
                    response = request_with_retries(session, "GET", url, settings=common.http)
                except Exception as exc:
                    _logger.info("sec_rss index skip date=%s error=%s", target_date.isoformat(), exc)
                    continue

                content = response.content
                sha = sha256_bytes(content)
                object_key = f"sec/daily_index/{target_date.isoformat()}/{sha}.idx"
                stored = storage.write_bytes(object_key, content, content_type="text/plain")
                raw_id = insert_raw_object(
                    source="sec_daily_index",
                    object_key=stored.object_key,
                    content_type="text/plain",
                    sha256=stored.sha256,
                    http_status=response.status_code,
                    meta={"date": target_date.isoformat(), "url": url},
                    conn=conn,
                )
                records = _parse_daily_index(content.decode("utf-8", errors="ignore"))

                processed = 0
                inserted = 0
                updated = 0
                skipped_filtered = 0
                skipped_no_accession = 0
                for record in records:
                    form_type = record.get("form_type")
                    if not form_type or form_type not in forms:
                        continue
                    cik = record.get("cik")
                    if not cik:
                        continue
                    if allowed_ciks is not None and cik not in allowed_ciks:
                        skipped_filtered += 1
                        continue
                    file_name = record.get("file_name") or ""
                    accession = _extract_accession_from_path(file_name)
                    if not accession:
                        skipped_no_accession += 1
                        continue
                    primary_doc_url = f"https://www.sec.gov/Archives/{file_name}"
                    filed_at = record.get("filed_at")

                    if _insert_sec_filing(
                        conn,
                        accession=accession,
                        cik=cik,
                        form_type=form_type,
                        filed_at=filed_at,
                        primary_doc_url=primary_doc_url,
                        raw_id=raw_id,
                        description=None,  # No description in daily index
                    ):
                        inserted += 1
                    else:
                        updated += 1
                    processed += 1

                total_processed += processed
                total_inserted += inserted
                total_updated += updated
                _logger.info(
                    "sec_rss index_date=%s processed=%s inserted=%s updated=%s skipped_filtered=%s skipped_no_accession=%s",
                    target_date.isoformat(),
                    processed,
                    inserted,
                    updated,
                    skipped_filtered,
                    skipped_no_accession,
                )

        conn.commit()
        _logger.info(
            "sec_rss done processed=%s inserted=%s updated=%s",
            total_processed,
            total_inserted,
            total_updated,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="SEC RSS fast-lane pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", help="Run SEC RSS ingestion")
    run_parser.add_argument("--forms", help="Comma-separated form types", default=None)
    run_parser.add_argument("--lookback-days", help="Backfill days using daily index", default=None)

    args = parser.parse_args()
    if args.command == "run":
        forms = [item.strip() for item in args.forms.split(",")] if args.forms else None
        if args.lookback_days:
            os.environ["SEC_RSS_LOOKBACK_DAYS"] = str(args.lookback_days)
        run(forms)


if __name__ == "__main__":
    main()
