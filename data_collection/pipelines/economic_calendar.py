"""
Economic Calendar Pipeline.
Fetches economic events from Forex Factory XML feed.

The XML feed at nfs.faireconomy.media provides accurate event dates, but times
are in GMT/UTC.  We convert them to ET before storing.  The feed is rate-limited
to ~2 requests per 5 minutes and only exposes the current week, so we run this
daily via the scheduler to accumulate data over time.
"""

import asyncio
import logging
import os
from datetime import datetime, date, time as dt_time
from typing import Any
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import httpx

from eiqora_v2.tools.db import get_connection

logger = logging.getLogger(__name__)

FF_XML_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"

# Impact levels we keep (case-insensitive)
KEEP_IMPACTS = {"high", "medium"}

UTC = ZoneInfo("UTC")
ET = ZoneInfo("America/New_York")


def _parse_date(raw: str) -> date | None:
    """Parse MM-DD-YYYY date string."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%m-%d-%Y").date()
    except ValueError:
        logger.debug("Unparseable date: %s", raw)
        return None


def _parse_time_utc(raw: str) -> dt_time | None:
    """Parse 12-hour time like '8:30am' or '2:00pm' (GMT/UTC from the feed).
    Returns None for 'All Day', 'Tentative', or empty strings."""
    raw = raw.strip().lower()
    if not raw or raw in ("all day", "tentative"):
        return None
    try:
        return datetime.strptime(raw, "%I:%M%p").time()
    except ValueError:
        logger.debug("Unparseable time: %s", raw)
        return None


def _utc_to_et(event_date: date, utc_time: dt_time | None) -> tuple[date, dt_time | None]:
    """Convert a UTC date+time pair to ET.  Returns (et_date, et_time).
    If utc_time is None (All Day), returns (event_date, None)."""
    if utc_time is None:
        return event_date, None
    utc_dt = datetime.combine(event_date, utc_time, tzinfo=UTC)
    et_dt = utc_dt.astimezone(ET)
    return et_dt.date(), et_dt.time()


async def fetch_xml_calendar() -> list[dict[str, Any]]:
    """Fetch this week's economic calendar from the Forex Factory XML feed.

    Returns a list of event dicts ready for ``upsert_events``.
    """
    events: list[dict[str, Any]] = []

    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(FF_XML_URL, headers={
                "User-Agent": "Mozilla/5.0 (compatible; EiqoraBot/1.0)",
                "Accept": "application/xml, text/xml, */*",
            })

            if resp.status_code == 429 or "rate limit" in resp.text.lower()[:500]:
                logger.warning("Forex Factory XML feed rate-limited; will retry next run")
                return events

            resp.raise_for_status()
            body = resp.text

        # Guard against HTML error pages (Cloudflare / rate-limit)
        if body.lstrip().startswith("<!DOCTYPE") or "<html" in body[:200].lower():
            logger.warning("Received HTML instead of XML (possible rate-limit page)")
            return events

        root = ElementTree.fromstring(body)

        for event_el in root.iter("event"):
            title = (event_el.findtext("title") or "").strip()
            country = (event_el.findtext("country") or "").strip()
            impact = (event_el.findtext("impact") or "").strip()
            date_raw = (event_el.findtext("date") or "").strip()
            time_raw = (event_el.findtext("time") or "").strip()
            forecast = (event_el.findtext("forecast") or "").strip() or None
            previous = (event_el.findtext("previous") or "").strip() or None

            # Only keep USD, HIGH/MEDIUM impact
            if country != "USD":
                continue
            if impact.lower() not in KEEP_IMPACTS:
                continue

            raw_date = _parse_date(date_raw)
            if raw_date is None:
                logger.debug("Skipping event with bad date: %s / %s", title, date_raw)
                continue

            raw_time = _parse_time_utc(time_raw)
            event_date, event_time = _utc_to_et(raw_date, raw_time)

            events.append({
                "event_name": title,
                "event_date": event_date,
                "event_time": event_time,
                "country": "US",
                "actual": None,  # XML feed rarely has actuals
                "forecast": forecast,
                "previous": previous,
                "impact": impact.upper(),  # HIGH, MEDIUM — matches schema/blackout checks
                "source": "forexfactory",
            })

        logger.info("Parsed %d USD high/medium-impact events from XML feed", len(events))

    except httpx.HTTPStatusError as e:
        logger.error("HTTP error fetching FF XML feed: %s", e)
    except ElementTree.ParseError as e:
        logger.error("XML parse error: %s", e)
    except Exception as e:
        logger.error("Unexpected error fetching FF calendar: %s", e)

    return events


async def upsert_events(events: list[dict]) -> int:
    """Insert or update events in database."""
    if not events:
        return 0

    async with get_connection() as conn:
        inserted = 0
        for event in events:
            try:
                await conn.execute(
                    """
                    INSERT INTO economic_event
                        (event_name, event_date, event_time, country,
                         actual, forecast, previous, impact, source)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    ON CONFLICT (event_name, event_date, country)
                    DO UPDATE SET
                        event_time = COALESCE(EXCLUDED.event_time, economic_event.event_time),
                        actual     = COALESCE(EXCLUDED.actual,     economic_event.actual),
                        forecast   = COALESCE(EXCLUDED.forecast,   economic_event.forecast),
                        previous   = COALESCE(EXCLUDED.previous,   economic_event.previous),
                        impact     = COALESCE(EXCLUDED.impact,     economic_event.impact),
                        source     = COALESCE(EXCLUDED.source,     economic_event.source)
                    """,
                    event["event_name"],
                    event["event_date"],
                    event["event_time"],
                    event.get("country", "US"),
                    event.get("actual"),
                    event.get("forecast"),
                    event.get("previous"),
                    event.get("impact", "medium"),
                    "forexfactory",
                )
                inserted += 1
            except Exception as e:
                logger.error("Error inserting event %s: %s", event["event_name"], e)

        return inserted


async def run():
    """Main pipeline entry point."""
    logger.info("Fetching economic calendar from Forex Factory XML feed...")

    events = await fetch_xml_calendar()
    logger.info("Found %d events from XML feed", len(events))

    if not events:
        logger.warning("No events fetched; skipping update")
        return

    inserted = await upsert_events(events)
    logger.info("Upserted %d events", inserted)

    # Log upcoming events
    async with get_connection() as conn:
        upcoming = await conn.fetch("""
            SELECT event_name, event_date, event_time, impact
            FROM economic_event
            WHERE event_date >= CURRENT_DATE
            ORDER BY event_date, event_time NULLS FIRST
            LIMIT 10
        """)
        logger.info("Upcoming events:")
        for e in upcoming:
            t = e["event_time"].strftime("%I:%M%p").lstrip("0") if e["event_time"] else "All Day"
            logger.info(
                "  %s %8s | %-6s | %s",
                e["event_date"].strftime("%Y-%m-%d"),
                t,
                e["impact"] or "n/a",
                e["event_name"],
            )


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    asyncio.run(run())


if __name__ == "__main__":
    main()
