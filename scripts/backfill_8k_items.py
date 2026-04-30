"""One-shot backfill of 8-K Item codes into ``sec_filing.items``.

Iterates 8-K rows that have ``items IS NULL`` and queries SEC's per-CIK
submissions JSON to recover the Items list. Idempotent: rows already
populated are skipped, and ON CONFLICT-style updates only set ``items``.

Usage:
    DATABASE_URL=... python scripts/backfill_8k_items.py [--days 60]
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from typing import Iterable

import asyncpg
import asyncio
import httpx

from data_collection.pipelines.sec_rss import (
    _normalize_accession,
    _parse_items_field,
)


SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
USER_AGENT = os.getenv("SEC_USER_AGENT", "Eiqora research panhaolun@gmail.com")
_logger = logging.getLogger("backfill_8k_items")


async def _fetch_cik_items(
    client: httpx.AsyncClient, cik: str
) -> dict[str, list[str]]:
    url = SEC_SUBMISSIONS_URL.format(cik=cik)
    try:
        resp = await client.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
        resp.raise_for_status()
    except Exception as exc:
        _logger.warning("submissions fetch failed cik=%s err=%s", cik, exc)
        return {}

    payload = resp.json()
    recent = (payload.get("filings") or {}).get("recent") or {}
    accs = recent.get("accessionNumber") or []
    items_raw = recent.get("items") or []

    out: dict[str, list[str]] = {}
    for idx, acc in enumerate(accs):
        if idx >= len(items_raw):
            break
        parsed = _parse_items_field(items_raw[idx])
        if parsed:
            out[_normalize_accession(acc) or acc] = parsed
    return out


async def main(days: int) -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        sys.exit("DATABASE_URL env var required")

    conn = await asyncpg.connect(dsn)
    rows = await conn.fetch(
        """
        SELECT cik, accession
        FROM sec_filing
        WHERE form_type LIKE '8-K%'
          AND items IS NULL
          AND filed_at >= CURRENT_DATE - $1::int
        ORDER BY cik, filed_at DESC
        """,
        days,
    )
    _logger.info("rows to backfill: %d", len(rows))

    by_cik: dict[str, list[str]] = {}
    for r in rows:
        by_cik.setdefault(r["cik"], []).append(r["accession"])

    updated = 0
    async with httpx.AsyncClient() as client:
        for cik, accs in by_cik.items():
            mapping = await _fetch_cik_items(client, cik)
            if not mapping:
                continue
            for acc in accs:
                items = mapping.get(_normalize_accession(acc) or acc)
                if not items:
                    continue
                await conn.execute(
                    "UPDATE sec_filing SET items = $1 WHERE accession = $2",
                    items,
                    acc,
                )
                updated += 1
            # SEC asks for <=10 req/sec; one fetch per CIK + 0.15s pause is plenty.
            time.sleep(0.15)

    _logger.info("updated %d rows", updated)
    await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=60)
    args = parser.parse_args()
    asyncio.run(main(args.days))
