import argparse
import asyncio
import logging
from datetime import date, timedelta

from eiqora_v2.services.backfill_analogs import label_historical_setups


def _parse_date(value: str | None, fallback: date) -> date:
    if not value:
        return fallback
    return date.fromisoformat(value)


async def run_backfill(start: date, end: date, interval: int) -> None:
    symbols = ["NVDA", "AAPL"]
    logging.info("Backfilling analog events for %s", ", ".join(symbols))
    logging.info("Range: %s to %s (interval=%s days)", start, end, interval)
    await label_historical_setups(
        symbols=symbols,
        start_date=start,
        end_date=end,
        sample_interval_days=interval,
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    today = date.today()
    default_start = today - timedelta(days=365 * 5)

    parser = argparse.ArgumentParser(
        description="Backfill analog_event for NVDA and AAPL over the last 5 years."
    )
    parser.add_argument("--start", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", help="End date (YYYY-MM-DD)")
    parser.add_argument(
        "--interval",
        type=int,
        default=1,
        help="Sample interval in days (1 = every trading day)",
    )

    args = parser.parse_args()
    start_date = _parse_date(args.start, default_start)
    end_date = _parse_date(args.end, today)

    asyncio.run(run_backfill(start_date, end_date, args.interval))


if __name__ == "__main__":
    main()
