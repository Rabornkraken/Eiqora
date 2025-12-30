"""SEC EDGAR ingestion (raw download + sec_filing table)."""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from typing import Any
from xml.etree import ElementTree

import bs4

from data_collection.common.config import get_env_list, load_common_settings
from data_collection.common.db import insert_raw_object
from data_collection.common.hashing import sha256_bytes
from data_collection.common.http import build_session, request_with_retries
from data_collection.common.paths import utc_date_str
from data_collection.common.storage import build_storage
from data_collection.common.settings import load_config
from data_collection.common.symbols import get_config_symbols
from data_collection.db.connection import get_connection

SEC_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_ARCHIVES = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{primary_doc}"
SEC_INDEX = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/index.json"
_logger = logging.getLogger(__name__)


def _clean_cik(cik: str) -> str:
    stripped = cik.strip().lstrip("0")
    return stripped.zfill(10)


def _iter_recent_filings(submissions: dict[str, Any]) -> list[dict[str, Any]]:
    recent = submissions.get("filings", {}).get("recent", {})
    accession = recent.get("accessionNumber", [])
    forms = recent.get("form", [])
    filing_dates = recent.get("filingDate", [])
    primary_docs = recent.get("primaryDocument", [])
    report_dates = recent.get("reportDate", [])

    filings = []
    for idx, form in enumerate(forms):
        filings.append(
            {
                "accession": accession[idx] if idx < len(accession) else None,
                "form": form,
                "filing_date": filing_dates[idx] if idx < len(filing_dates) else None,
                "primary_doc": primary_docs[idx] if idx < len(primary_docs) else None,
                "report_date": report_dates[idx] if idx < len(report_dates) else None,
            }
        )
    return filings


class RateLimiter:
    def __init__(self, max_requests_per_second: float) -> None:
        self._min_interval = 1.0 / max_requests_per_second if max_requests_per_second > 0 else 0.0
        self._last_request = 0.0

    def wait(self) -> None:
        if self._min_interval <= 0:
            return
        now = time.monotonic()
        elapsed = now - self._last_request
        remaining = self._min_interval - elapsed
        if remaining > 0:
            time.sleep(remaining)
        self._last_request = time.monotonic()


def _load_ticker_cik_map(
    *,
    conn,
    storage,
    session,
    settings,
    url: str,
) -> tuple[dict[str, str], list[dict[str, str]]]:
    response = request_with_retries(session, "GET", url, settings=settings)
    payload = response.json()
    raw_bytes = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    sha = sha256_bytes(raw_bytes)
    object_key = f"sec/ticker_cik/{utc_date_str()}/{sha}.json"
    stored = storage.write_bytes(object_key, raw_bytes, content_type="application/json")
    insert_raw_object(
        source="sec_ticker_cik",
        object_key=stored.object_key,
        content_type="application/json",
        sha256=stored.sha256,
        http_status=response.status_code,
        meta={"url": url},
        conn=conn,
    )
    mapping: dict[str, str] = {}
    entries: list[dict[str, str]] = []
    for _, entry in payload.items():
        ticker = entry.get("ticker")
        cik = entry.get("cik_str")
        title = entry.get("title")
        if not ticker or not cik:
            continue
        padded_cik = str(cik).zfill(10)
        mapping[ticker.upper()] = padded_cik
        entries.append(
            {
                "ticker": ticker.upper(),
                "cik": padded_cik,
                "title": title or None,
            }
        )
    return mapping, entries


def _upsert_issuer_security(conn, entries: list[dict[str, str]]) -> None:
    if not entries:
        return
    with conn.cursor() as cursor:
        for entry in entries:
            cursor.execute(
                """
                INSERT INTO issuer (cik, legal_name, updated_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (cik)
                DO UPDATE SET legal_name = EXCLUDED.legal_name, updated_at = NOW()
                """,
                (entry["cik"], entry.get("title")),
            )
            cursor.execute(
                """
                SELECT 1 FROM security
                WHERE ticker = %s AND cik = %s AND is_primary = TRUE
                LIMIT 1
                """,
                (entry["ticker"], entry["cik"]),
            )
            exists = cursor.fetchone()
            if not exists:
                cursor.execute(
                    """
                    INSERT INTO security (ticker, exchange, cik, is_primary, valid_from, valid_to)
                    VALUES (%s, %s, %s, TRUE, NULL, NULL)
                    """,
                    (entry["ticker"], None, entry["cik"]),
                )


def _insert_sec_filing(
    conn,
    *,
    accession: str,
    cik: str,
    form_type: str,
    filed_at: str | None,
    report_period: str | None,
    primary_doc_url: str,
    raw_id: int,
) -> bool:
    filed_date = datetime.strptime(filed_at, "%Y-%m-%d").date() if filed_at else None
    report_date = datetime.strptime(report_period, "%Y-%m-%d").date() if report_period else None
    is_amendment = form_type.endswith("/A")
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO sec_filing
                (accession, cik, form_type, filed_at, report_period, is_amendment, amends_accession, primary_doc_url, raw_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (accession)
            DO UPDATE SET
                cik = EXCLUDED.cik,
                form_type = EXCLUDED.form_type,
                filed_at = EXCLUDED.filed_at,
                report_period = EXCLUDED.report_period,
                is_amendment = EXCLUDED.is_amendment,
                primary_doc_url = EXCLUDED.primary_doc_url,
                raw_id = EXCLUDED.raw_id
            RETURNING (xmax = 0) AS inserted
            """,
            (
                accession,
                cik,
                form_type,
                filed_date,
                report_date,
                is_amendment,
                None,
                primary_doc_url,
                raw_id,
            ),
        )
        row = cursor.fetchone()
        return bool(row[0]) if row else False


def _decode_text(content: bytes) -> str:
    for encoding in ("utf-8", "latin-1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="ignore")


def _store_section_text(storage, accession: str, text: str) -> str:
    text_bytes = text.encode("utf-8")
    sha = sha256_bytes(text_bytes)
    object_key = f"sec/sections/{accession}/{sha}.txt"
    storage.write_bytes(object_key, text_bytes, content_type="text/plain")
    return object_key


def _upsert_sections(conn, accession: str, storage, content: bytes) -> int:
    text = _decode_text(content)
    soup = bs4.BeautifulSoup(text, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    clean_text = " ".join(soup.stripped_strings)
    if not clean_text:
        return 0
    text_key = _store_section_text(storage, accession, clean_text)
    with conn.cursor() as cursor:
        cursor.execute(
            "DELETE FROM sec_filing_section WHERE accession = %s AND section_name = %s",
            (accession, "full"),
        )
        cursor.execute(
            """
            INSERT INTO sec_filing_section (accession, section_name, text_object_key, parser_version)
            VALUES (%s, %s, %s, %s)
            """,
            (accession, "full", text_key, "v1"),
        )
    return 1


def _xml_text(node: ElementTree.Element | None) -> str | None:
    if node is None or node.text is None:
        return None
    return node.text.strip()


def _parse_form4(content: bytes) -> dict[str, Any]:
    text = _decode_text(content)
    root = ElementTree.fromstring(text)
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0] + "}"

    owner_name = _xml_text(root.find(f".//{ns}reportingOwner/{ns}reportingOwnerId/{ns}rptOwnerName"))
    transactions = []
    for txn in root.findall(f".//{ns}nonDerivativeTransaction"):
        code = _xml_text(txn.find(f".//{ns}transactionCoding/{ns}transactionCode"))
        date = _xml_text(txn.find(f".//{ns}transactionDate/{ns}value"))
        shares = _xml_text(txn.find(f".//{ns}transactionShares/{ns}value"))
        price = _xml_text(txn.find(f".//{ns}transactionPricePerShare/{ns}value"))
        acquired = _xml_text(txn.find(f".//{ns}transactionAcquiredDisposedCode/{ns}value"))
        transactions.append(
            {
                "owner_name": owner_name,
                "transaction_date": date,
                "code": code,
                "shares": shares,
                "price": price,
                "acquired_disposed": acquired,
            }
        )
    return {"owner_name": owner_name, "transactions": transactions}


def _upsert_form4_transactions(conn, accession: str, cik: str, content: bytes) -> int:
    try:
        parsed = _parse_form4(content)
    except Exception:
        return 0
    transactions = parsed.get("transactions", [])
    if not transactions:
        return 0
    with conn.cursor() as cursor:
        cursor.execute("DELETE FROM insider_transaction WHERE accession = %s", (accession,))
        for txn in transactions:
            cursor.execute(
                """
                INSERT INTO insider_transaction
                    (accession, cik, owner_name, transaction_date, code, shares, price, acquired_disposed, raw_fields)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    accession,
                    cik,
                    txn.get("owner_name"),
                    datetime.strptime(txn["transaction_date"], "%Y-%m-%d").date() if txn.get("transaction_date") else None,
                    txn.get("code"),
                    float(txn["shares"]) if txn.get("shares") else None,
                    float(txn["price"]) if txn.get("price") else None,
                    txn.get("acquired_disposed"),
                    json.dumps(txn),
                ),
            )
    return len(transactions)


def _parse_13f(content: bytes) -> list[dict[str, Any]]:
    text = _decode_text(content)
    root = ElementTree.fromstring(text)
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0] + "}"
    holdings = []
    for info in root.findall(f".//{ns}infoTable"):
        cusip = _xml_text(info.find(f"{ns}cusip"))
        value = _xml_text(info.find(f"{ns}value"))
        shares = _xml_text(info.find(f"{ns}shrsOrPrnAmt/{ns}sshPrnamt"))
        put_call = _xml_text(info.find(f"{ns}putCall"))
        holdings.append(
            {
                "cusip": cusip,
                "value": value,
                "shares": shares,
                "put_call": put_call,
            }
        )
    return holdings


def _upsert_13f_holdings(conn, accession: str, cik: str, content: bytes) -> int:
    try:
        holdings = _parse_13f(content)
    except Exception:
        return 0
    if not holdings:
        return 0
    with conn.cursor() as cursor:
        cursor.execute("DELETE FROM sec_13f_holding WHERE accession = %s", (accession,))
        for holding in holdings:
            cursor.execute(
                """
                INSERT INTO sec_13f_holding
                    (accession, manager_cik, issuer_cusip, shares, value, put_call, raw_fields)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    accession,
                    cik,
                    holding.get("cusip"),
                    float(holding["shares"]) if holding.get("shares") else None,
                    float(holding["value"]) if holding.get("value") else None,
                    holding.get("put_call"),
                    json.dumps(holding),
                ),
            )
    return len(holdings)


def _fetch_filing_index(
    *,
    session: requests.Session,
    storage,
    conn,
    settings: HTTPSettings,
    cik: str,
    accession_no_dashes: str,
    limiter: RateLimiter,
) -> list[dict[str, Any]]:
    index_url = SEC_INDEX.format(cik=str(int(cik)), accession=accession_no_dashes)
    limiter.wait()
    response = request_with_retries(session, "GET", index_url, settings=settings)
    payload = response.json()
    payload_bytes = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    sha = sha256_bytes(payload_bytes)
    object_key = f"sec/index/{cik}/{utc_date_str()}/{accession_no_dashes}/{sha}.json"
    stored = storage.write_bytes(object_key, payload_bytes, content_type="application/json")
    insert_raw_object(
        source="sec_filing_index",
        object_key=stored.object_key,
        content_type="application/json",
        sha256=stored.sha256,
        http_status=response.status_code,
        meta={"cik": cik, "accession": accession_no_dashes, "url": index_url},
        conn=conn,
    )
    items = payload.get("directory", {}).get("item", [])
    if not isinstance(items, list):
        return []
    return items


def _select_form4_xml(items: list[dict[str, Any]], primary_doc: str | None) -> str | None:
    candidates = []
    for item in items:
        name = (item.get("name") or "").lower()
        if not name.endswith(".xml"):
            continue
        if name.endswith(".xsl") or name.endswith(".xsd"):
            continue
        candidates.append(item.get("name"))
        if "form4" in name:
            return item.get("name")
    if primary_doc and primary_doc.lower().endswith(".xml"):
        return primary_doc
    return candidates[0] if candidates else None


def _select_13f_xml(items: list[dict[str, Any]], primary_doc: str | None) -> str | None:
    candidates = []
    for item in items:
        name = (item.get("name") or "").lower()
        if not name.endswith(".xml"):
            continue
        if name.endswith(".xsl") or name.endswith(".xsd"):
            continue
        candidates.append(item.get("name"))
        if "infotable" in name:
            return item.get("name")
    if primary_doc and primary_doc.lower().endswith(".xml"):
        return primary_doc
    return candidates[0] if candidates else None


def _fetch_attachment(
    *,
    session: requests.Session,
    storage,
    conn,
    settings: HTTPSettings,
    limiter: RateLimiter,
    cik: str,
    accession: str,
    filename: str,
    form: str,
) -> tuple[bytes, str]:
    accession_no_dashes = accession.replace("-", "")
    url = SEC_ARCHIVES.format(cik=str(int(cik)), accession=accession_no_dashes, primary_doc=filename)
    limiter.wait()
    response = request_with_retries(session, "GET", url, settings=settings)
    content = response.content
    sha = sha256_bytes(content)
    object_key = f"sec/{form}/{cik}/{utc_date_str()}/{accession}/{sha}-{filename}"
    stored = storage.write_bytes(object_key, content, content_type=response.headers.get("Content-Type"))
    insert_raw_object(
        source="sec_filing_attachment",
        object_key=stored.object_key,
        content_type=response.headers.get("Content-Type"),
        sha256=stored.sha256,
        http_status=response.status_code,
        meta={"cik": cik, "form": form, "accession": accession, "filename": filename, "url": url},
        conn=conn,
    )
    return content, url


def run() -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    common = load_common_settings()
    storage = build_storage(common)
    config = load_config()
    sec_cfg = config.get("sec", {})

    symbols, _, _ = get_config_symbols(config)
    env_ciks = get_env_list("SEC_CIKS", required=False)
    forms = get_env_list("SEC_FORMS", required=False) or sec_cfg.get("forms") or [
        "10-K",
        "10-Q",
        "4",
        "13F-HR",
        "13F-HR/A",
    ]
    trailing_days = int(sec_cfg.get("trailing_rescan_days", 0) or 0)
    trailing_days_env = os.getenv("SEC_TRAILING_DAYS")
    if trailing_days_env:
        trailing_days = int(trailing_days_env)

    session = build_session(common.http)
    user_agent = os.getenv("SEC_USER_AGENT") or sec_cfg.get("user_agent") or common.http.user_agent
    session.headers.update({"User-Agent": user_agent})
    limiter = RateLimiter(sec_cfg.get("max_requests_per_second", 8))
    batch_size_env = os.getenv("SEC_BATCH_SIZE")
    batch_offset_env = os.getenv("SEC_BATCH_OFFSET")
    batch_size = int(batch_size_env) if batch_size_env else None
    batch_offset = int(batch_offset_env) if batch_offset_env else None
    if batch_size is not None and batch_size <= 0:
        batch_size = None
    if batch_offset is not None and batch_offset < 0:
        batch_offset = None

    with get_connection() as conn:
        ciks: list[str] = []
        if env_ciks:
            ciks = [_clean_cik(cik) for cik in env_ciks]
        else:
            ticker_cik_url = sec_cfg.get("ticker_cik_json_url")
            if not ticker_cik_url:
                raise RuntimeError("SEC ticker CIK mapping URL not configured")
            if not symbols:
                raise RuntimeError("No SEC_CIKS and no symbols configured for SEC ingestion")
            ticker_cik_map, ticker_entries = _load_ticker_cik_map(
                conn=conn,
                storage=storage,
                session=session,
                settings=common.http,
                url=ticker_cik_url,
            )
            _upsert_issuer_security(conn, ticker_entries)
            for symbol in symbols:
                cik = ticker_cik_map.get(symbol.upper())
                if cik:
                    ciks.append(cik)

        if batch_size is not None:
            offset = batch_offset or 0
            ciks = ciks[offset : offset + batch_size]

        _logger.info(
            "sec_edgar start: ciks=%s forms=%s batch_size=%s batch_offset=%s trailing_days=%s",
            len(ciks),
            ",".join(forms),
            batch_size,
            batch_offset,
            trailing_days,
        )

        for cik in ciks:
            padded_cik = _clean_cik(cik)
            limiter.wait()
            submissions_url = SEC_SUBMISSIONS.format(cik=padded_cik)
            response = request_with_retries(session, "GET", submissions_url, settings=common.http)
            submissions = response.json()

            submissions_bytes = json.dumps(submissions, ensure_ascii=True).encode("utf-8")
            submissions_sha = sha256_bytes(submissions_bytes)
            submissions_key = f"sec/submissions/{padded_cik}/{utc_date_str()}/{submissions_sha}.json"
            stored = storage.write_bytes(submissions_key, submissions_bytes, content_type="application/json")
            insert_raw_object(
                source="sec_submissions",
                object_key=stored.object_key,
                content_type="application/json",
                sha256=stored.sha256,
                http_status=response.status_code,
                meta={"cik": padded_cik, "url": submissions_url},
                conn=conn,
            )

            processed = 0
            inserted_new = 0
            updated_existing = 0
            sections_inserted = 0
            form4_inserted = 0
            f13_inserted = 0
            form4_xml_used = 0
            f13_xml_used = 0
            for filing in _iter_recent_filings(submissions):
                form = filing.get("form")
                accession = filing.get("accession")
                primary_doc = filing.get("primary_doc")
                if not form or form not in forms:
                    continue
                if not accession or not primary_doc:
                    continue
                filed_at = filing.get("filing_date")
                if trailing_days and filed_at:
                    try:
                        filed_date = datetime.strptime(filed_at, "%Y-%m-%d").date()
                    except Exception:
                        filed_date = None
                    if filed_date and (datetime.utcnow().date() - filed_date).days > trailing_days:
                        continue

                accession_no_dashes = accession.replace("-", "")
                url = SEC_ARCHIVES.format(
                    cik=str(int(padded_cik)),
                    accession=accession_no_dashes,
                    primary_doc=primary_doc,
                )
                limiter.wait()
                doc_response = request_with_retries(session, "GET", url, settings=common.http)
                content = doc_response.content
                sha = sha256_bytes(content)
                fetched_at = utc_date_str()
                object_key = (
                    f"sec/{form}/{padded_cik}/{fetched_at}/{accession}/{sha}-{primary_doc}"
                )
                stored = storage.write_bytes(
                    object_key, content, content_type=doc_response.headers.get("Content-Type")
                )
                raw_id = insert_raw_object(
                    source="sec_filing",
                    object_key=stored.object_key,
                    content_type=doc_response.headers.get("Content-Type"),
                    sha256=stored.sha256,
                    http_status=doc_response.status_code,
                    meta={
                        "cik": padded_cik,
                        "form": form,
                        "accession": accession,
                        "filing_date": filing.get("filing_date"),
                        "report_date": filing.get("report_date"),
                        "primary_doc": primary_doc,
                        "url": url,
                    },
                    conn=conn,
                )
                if _insert_sec_filing(
                    conn,
                    accession=accession,
                    cik=padded_cik,
                    form_type=form,
                    filed_at=filing.get("filing_date"),
                    report_period=filing.get("report_date"),
                    primary_doc_url=url,
                    raw_id=raw_id,
                ):
                    inserted_new += 1
                else:
                    updated_existing += 1
                if form in {"10-K", "10-Q", "8-K"}:
                    sections_inserted += _upsert_sections(conn, accession, storage, content)
                if form == "4":
                    xml_content = content
                    try:
                        items = _fetch_filing_index(
                            session=session,
                            storage=storage,
                            conn=conn,
                            settings=common.http,
                            cik=padded_cik,
                            accession_no_dashes=accession_no_dashes,
                            limiter=limiter,
                        )
                    except Exception:
                        items = []
                    xml_name = _select_form4_xml(items, primary_doc)
                    if xml_name and xml_name != primary_doc:
                        xml_content, _ = _fetch_attachment(
                            session=session,
                            storage=storage,
                            conn=conn,
                            settings=common.http,
                            limiter=limiter,
                            cik=padded_cik,
                            accession=accession,
                            filename=xml_name,
                            form=form,
                        )
                        form4_xml_used += 1
                    form4_inserted += _upsert_form4_transactions(conn, accession, padded_cik, xml_content)
                if form in {"13F-HR", "13F-HR/A"}:
                    xml_content = content
                    try:
                        items = _fetch_filing_index(
                            session=session,
                            storage=storage,
                            conn=conn,
                            settings=common.http,
                            cik=padded_cik,
                            accession_no_dashes=accession_no_dashes,
                            limiter=limiter,
                        )
                    except Exception:
                        items = []
                    xml_name = _select_13f_xml(items, primary_doc)
                    if xml_name and xml_name != primary_doc:
                        xml_content, _ = _fetch_attachment(
                            session=session,
                            storage=storage,
                            conn=conn,
                            settings=common.http,
                            limiter=limiter,
                            cik=padded_cik,
                            accession=accession,
                            filename=xml_name,
                            form=form,
                        )
                        f13_xml_used += 1
                    f13_inserted += _upsert_13f_holdings(conn, accession, padded_cik, xml_content)
                processed += 1

            conn.commit()
            _logger.info(
                "sec_edgar cik=%s processed=%s inserted_new=%s updated=%s sections=%s form4_rows=%s f13_rows=%s form4_xml=%s f13_xml=%s",
                padded_cik,
                processed,
                inserted_new,
                updated_existing,
                sections_inserted,
                form4_inserted,
                f13_inserted,
                form4_xml_used,
                f13_xml_used,
            )


if __name__ == "__main__":
    run()
