"""
Weekly profile refresh pipeline.
Refreshes ticker profiles only if older than the configured age.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from datetime import datetime, timedelta

from data_collection.db.connection import get_connection
from eiqora_v2.services.profile_generator import ProfileGenerator

logger = logging.getLogger(__name__)


def get_universe_symbols(limit: int | None = None) -> list[str]:
    """Get active universe symbols from database."""
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT symbol FROM universe_member WHERE active = true ORDER BY symbol")
            symbols = [row[0] for row in cursor.fetchall()]
    if limit is not None:
        symbols = symbols[:limit]
    logger.info("Got %s symbols from universe", len(symbols))
    return symbols


async def refresh_profiles(
    symbols: list[str],
    max_age_days: int,
    concurrency: int,
) -> None:
    """Refresh profiles if missing or older than max_age_days."""
    generator = ProfileGenerator()
    semaphore = asyncio.Semaphore(max(1, concurrency))

    refreshed = 0
    fresh = 0
    failed = 0

    async def process(symbol: str) -> None:
        nonlocal refreshed, fresh, failed
        async with semaphore:
            try:
                profile = await generator._load_profile(symbol)
                age_days = None
                if profile and profile.last_updated:
                    age = datetime.utcnow().replace(tzinfo=None) - profile.last_updated.replace(tzinfo=None)
                    age_days = age.days
                    if age < timedelta(days=max_age_days):
                        logger.info("Profile fresh: %s (age=%s)", symbol, age_days)
                        fresh += 1
                        return

                age_label = "missing" if profile is None else (str(age_days) if age_days is not None else "unknown")
                logger.info("Refreshing profile: %s (age=%s)", symbol, age_label)
                await generator.generate_profile(symbol)
                refreshed += 1
            except Exception as exc:
                failed += 1
                logger.error("Profile refresh failed for %s: %s", symbol, exc, exc_info=True)

    await asyncio.gather(*(process(symbol) for symbol in symbols))
    logger.info(
        "Profile refresh summary: refreshed=%s fresh=%s failed=%s total=%s",
        refreshed,
        fresh,
        failed,
        len(symbols),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh ticker profiles if stale.")
    parser.add_argument("--max-age-days", type=int, default=7, help="Max age (days) before refresh.")
    parser.add_argument("--concurrency", type=int, default=int(os.getenv("PROFILE_REFRESH_CONCURRENCY", "10")))
    parser.add_argument("--limit", type=int, default=None, help="Limit number of symbols (debug).")
    args = parser.parse_args()

    symbols = get_universe_symbols(limit=args.limit)
    if not symbols:
        logger.warning("No symbols in universe, skipping profile refresh")
        return

    logger.info(
        "Refreshing profiles with max_age_days=%s concurrency=%s",
        args.max_age_days,
        max(1, args.concurrency),
    )
    asyncio.run(refresh_profiles(symbols, args.max_age_days, args.concurrency))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    main()
