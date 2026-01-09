"""
Backfill active positions from recent GO trade signals.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from eiqora_v2.tools.db import get_connection
from eiqora_v2.tools.positions import open_position, has_open_position

logger = logging.getLogger(__name__)


def _parse_agent_outputs(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return {}


def _map_conviction(score: Any) -> str | None:
    try:
        value = float(score)
    except (TypeError, ValueError):
        return None
    if value >= 0.75:
        return "HIGH"
    if value >= 0.55:
        return "MEDIUM"
    return "LOW"


async def backfill_positions(
    *,
    since_days: int,
    limit: int | None,
    dry_run: bool,
) -> None:
    cutoff = datetime.utcnow() - timedelta(days=since_days)
    async with get_connection() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (symbol)
                id,
                symbol,
                entry_price,
                stop_loss,
                take_profit,
                conviction,
                reasoning,
                agent_outputs,
                created_at
            FROM trade_signal
            WHERE action = 'GO'
              AND created_at >= $1
            ORDER BY symbol, created_at DESC
            """,
            cutoff,
        )

    if limit is not None:
        rows = rows[:limit]

    created = 0
    skipped = 0
    for row in rows:
        symbol = row["symbol"]
        if await has_open_position(symbol):
            skipped += 1
            logger.info("Skipping %s (already active)", symbol)
            continue

        entry_price = float(row["entry_price"]) if row["entry_price"] is not None else 0.0
        if entry_price <= 0:
            skipped += 1
            logger.warning("Skipping %s (missing entry price)", symbol)
            continue

        outputs = _parse_agent_outputs(row["agent_outputs"])
        decision = outputs.get("decision", {}) if isinstance(outputs, dict) else {}
        rule = decision.get("rule") or {}

        direction = str(rule.get("direction", "LONG")).upper()
        time_stop_days = int(rule.get("time_stop_days", 30))
        position_size_pct = decision.get("position_size_pct")
        conviction = _map_conviction(row["conviction"]) or decision.get("conviction")

        logger.info("Backfilling %s from signal %s", symbol, row["id"])
        if dry_run:
            created += 1
            continue

        await open_position(
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            stop_loss=float(row["stop_loss"]) if row["stop_loss"] is not None else None,
            take_profit=float(row["take_profit"]) if row["take_profit"] is not None else None,
            entry_time=row["created_at"],
            time_stop_days=time_stop_days,
            conviction=conviction,
            reasoning=row["reasoning"],
            position_size_pct=position_size_pct,
            signal_id=str(row["id"]),
        )
        created += 1

    logger.info(
        "Backfill complete: created=%s skipped=%s total=%s",
        created,
        skipped,
        len(rows),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill positions from GO trade signals.")
    parser.add_argument("--since-days", type=int, default=30, help="Lookback window in days.")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of symbols processed.")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing.")
    args = parser.parse_args()

    asyncio.run(
        backfill_positions(
            since_days=args.since_days,
            limit=args.limit,
            dry_run=args.dry_run,
        )
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    main()
