"""Populate issuer/security tables from the SEC ticker map."""

from __future__ import annotations

import logging
import os

from data_collection.common.config import load_common_settings
from data_collection.common.http import build_session
from data_collection.common.settings import load_config
from data_collection.common.storage import build_storage
from data_collection.common.symbols import get_config_symbols
from data_collection.db.connection import get_connection
from data_collection.pipelines.sec_edgar.pipeline import _load_ticker_cik_map, _upsert_issuer_security

_logger = logging.getLogger(__name__)


def run() -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    common = load_common_settings()
    config = load_config()
    sec_cfg = config.get("sec", {})
    ticker_cik_url = sec_cfg.get("ticker_cik_json_url")
    if not ticker_cik_url:
        raise RuntimeError("SEC ticker CIK mapping URL not configured")

    symbols, _, _ = get_config_symbols(config)
    symbol_filter = {symbol.upper() for symbol in symbols} if symbols else None

    session = build_session(common.http)
    user_agent = os.getenv("SEC_USER_AGENT") or sec_cfg.get("user_agent") or common.http.user_agent
    session.headers.update({"User-Agent": user_agent})

    storage = build_storage(common)
    with get_connection() as conn:
        mapping, entries = _load_ticker_cik_map(
            conn=conn,
            storage=storage,
            session=session,
            settings=common.http,
            url=ticker_cik_url,
        )
        if symbol_filter:
            entries = [entry for entry in entries if entry["ticker"] in symbol_filter]
        _upsert_issuer_security(conn, entries)
        conn.commit()

    _logger.info(
        "sec_ticker_map done: total=%s filtered=%s",
        len(mapping),
        len(entries),
    )


if __name__ == "__main__":
    run()
