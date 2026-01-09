"""
Economic Calendar Pipeline.
Fetches economic events from Forex Factory.
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta, date
from typing import Any

from bs4 import BeautifulSoup

from eiqora_v2.tools.db import get_connection

logger = logging.getLogger(__name__)

# High-impact events we care about
HIGH_IMPACT_EVENTS = {
    'interest rate',
    'fed interest rate decision',
    'fomc',
    'fomc statement',
    'fomc minutes',
    'fomc press conference',
    'non-farm payrolls',
    'nonfarm payrolls',
    'unemployment rate',
    'cpi',
    'core cpi',
    'pce price index',
    'core pce',
    'gdp',
    'retail sales',
    'ism manufacturing',
    'ism services',
    'consumer confidence',
    'michigan consumer sentiment',
    'initial jobless claims',
    'housing starts',
    'existing home sales',
    'durable goods',
    'ppi',
}

FOREXFACTORY_URL = "https://www.forexfactory.com/calendar"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def _week_start(value: date) -> date:
    days_since_sunday = (value.weekday() + 1) % 7
    return value - timedelta(days=days_since_sunday)


def _week_key(value: date) -> str:
    return f"{value.strftime('%b').lower()}{value.day}.{value.year}"


def _weeks_in_range(start_date: date, end_date: date) -> list[date]:
    current = _week_start(start_date)
    end_week = _week_start(end_date)
    weeks: list[date] = []
    while current <= end_week:
        weeks.append(current)
        current += timedelta(days=7)
    return weeks


async def fetch_forexfactory_calendar(
    week: str | None = None,
    week_start: date | None = None,
) -> list[dict[str, Any]]:
    """
    Fetch economic calendar from Forex Factory using Playwright with stealth mode.
    Returns list of events with actual/forecast/previous values.
    """
    events = []
    
    try:
        from playwright.async_api import async_playwright
        from playwright_stealth import Stealth
    except ImportError as e:
        logger.warning(f"Playwright or playwright-stealth not available: {e}")
        return events
    
    # Forex Factory calendar URL - shows current week
    url = FOREXFACTORY_URL if not week else f"{FOREXFACTORY_URL}?week={week}"
    if week_start is None:
        week_start = _week_start(datetime.now().date())
    
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            user_agent = os.getenv("DATA_COLLECTION_USER_AGENT", DEFAULT_USER_AGENT)
            context = await browser.new_context(
                user_agent=user_agent,
                viewport={'width': 1920, 'height': 1080},
            )
            page = await context.new_page()
            
            # Apply stealth mode
            stealth = Stealth(navigator_webdriver=True)
            await stealth.apply_stealth_async(page)
            
            logger.info("Fetching Forex Factory calendar with Playwright stealth...")
            await page.goto(url, timeout=60000, wait_until='domcontentloaded')
            
            # Wait for calendar rows to load (use 'attached' state, not visible)
            try:
                await page.wait_for_selector('tr.calendar__row[data-event-id]', timeout=15000, state='attached')
            except:
                pass  # Continue even if timeout - page may already be loaded
            
            # Give extra time for content
            await page.wait_for_timeout(2000)
            
            # Scroll to load all content
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1000)
            
            # Parse the page content
            content = await page.content()
            soup = BeautifulSoup(content, 'html.parser')
            
            await browser.close()
        
        # Track current date from date cells
        current_date = week_start or datetime.now().date()
        year = current_date.year
        
        # Parse the calendar table
        for row in soup.select('tr.calendar__row[data-event-id]'):
            try:
                # Check for date cell (only on first event of each day)
                date_cell = row.select_one('td.calendar__date')
                if date_cell:
                    date_text = date_cell.get_text(strip=True)
                    if date_text:
                        try:
                            # Format: "Mon Jan 6" or similar
                            from dateutil import parser
                            parsed = parser.parse(f"{date_text} {year}")
                            parsed_date = parsed.date()
                            if week_start:
                                if parsed_date < week_start - timedelta(days=3):
                                    parsed_date = parsed_date.replace(year=parsed_date.year + 1)
                                elif parsed_date > week_start + timedelta(days=7):
                                    parsed_date = parsed_date.replace(year=parsed_date.year - 1)
                            current_date = parsed_date
                        except:
                            pass
                
                # Get event details
                event_cell = row.select_one('td.calendar__event')
                currency_cell = row.select_one('td.calendar__currency')
                impact_cell = row.select_one('td.calendar__impact span')
                actual_cell = row.select_one('td.calendar__actual')
                forecast_cell = row.select_one('td.calendar__forecast')
                previous_cell = row.select_one('td.calendar__previous')
                
                if not event_cell:
                    continue
                
                event_name = event_cell.get_text(strip=True)
                
                # Filter for USD events only
                currency = currency_cell.get_text(strip=True) if currency_cell else ''
                if currency != 'USD':
                    continue
                
                # Get impact level from icon class
                impact = 'medium'
                if impact_cell:
                    impact_classes = ' '.join(impact_cell.get('class', []))
                    if 'icon--ff-impact-red' in impact_classes:
                        impact = 'high'
                    elif 'icon--ff-impact-ora' in impact_classes:
                        impact = 'medium'
                    elif 'icon--ff-impact-yel' in impact_classes:
                        continue  # Skip low impact events
                    elif 'icon--ff-impact-gra' in impact_classes:
                        continue  # Skip non-economic/holiday events
                
                # Parse actual/forecast/previous values
                def parse_value(cell):
                    if not cell:
                        return None
                    text = cell.get_text(strip=True)
                    if not text or text in ['-', '--', '']:
                        return None
                    # Remove % and other symbols, handle K/M/B
                    text = text.replace('%', '').strip()
                    multiplier = 1
                    if text.endswith('K'):
                        text = text[:-1]
                        multiplier = 1000
                    elif text.endswith('M'):
                        text = text[:-1]
                        multiplier = 1000000
                    elif text.endswith('B'):
                        text = text[:-1]
                        multiplier = 1000000000
                    try:
                        return float(text.replace(',', '')) * multiplier
                    except ValueError:
                        return None
                
                events.append({
                    'event_name': event_name,
                    'event_date': datetime.combine(current_date, datetime.min.time()),
                    'country': 'US',
                    'actual': parse_value(actual_cell),
                    'forecast': parse_value(forecast_cell),
                    'previous': parse_value(previous_cell),
                    'impact': impact,
                    'source': 'forexfactory',
                })
                
            except Exception as e:
                logger.debug(f"Error parsing row: {e}")
                continue
        
        logger.info(f"Parsed {len(events)} USD events from Forex Factory")
                
    except Exception as e:
        logger.error(f"Error fetching Forex Factory calendar: {e}")
    
    return events


async def fetch_forexfactory_months_ahead(months_ahead: int) -> list[dict[str, Any]]:
    from dateutil.relativedelta import relativedelta

    today = datetime.now().date()
    end_date = today + relativedelta(months=months_ahead)
    weeks = _weeks_in_range(today, end_date)

    events: list[dict[str, Any]] = []
    seen: set[tuple[str, date, str]] = set()
    for week_start in weeks:
        week_key = _week_key(week_start)
        week_events = await fetch_forexfactory_calendar(week=week_key, week_start=week_start)
        for event in week_events:
            key = (event.get("event_name", ""), event.get("event_date").date(), event.get("country", "US"))
            if key in seen:
                continue
            seen.add(key)
            events.append(event)
    return events




async def upsert_events(events: list[dict]) -> int:
    """Insert or update events in database."""
    if not events:
        return 0
    
    async with get_connection() as conn:
        inserted = 0
        for event in events:
            try:
                event_dt = event.get("event_date") or datetime.now()
                event_date = event_dt.date() if isinstance(event_dt, datetime) else event_dt
                event_time = event_dt.time() if isinstance(event_dt, datetime) else None
                await conn.execute("""
                    INSERT INTO economic_event (event_name, event_date, event_time, country, actual, forecast, previous, impact, source)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    ON CONFLICT (event_name, event_date, country)
                    DO UPDATE SET event_time = COALESCE(EXCLUDED.event_time, economic_event.event_time),
                                  actual = COALESCE(EXCLUDED.actual, economic_event.actual),
                                  forecast = COALESCE(EXCLUDED.forecast, economic_event.forecast),
                                  previous = COALESCE(EXCLUDED.previous, economic_event.previous),
                                  impact = COALESCE(EXCLUDED.impact, economic_event.impact),
                                  source = COALESCE(EXCLUDED.source, economic_event.source)
                """,
                    event['event_name'],
                    event_date,
                    event_time,
                    event.get('country', 'US'),
                    event.get('actual'),
                    event.get('forecast'),
                    event.get('previous'),
                    event.get('impact', 'medium'),
                    'forexfactory',
                )
                inserted += 1
            except Exception as e:
                logger.error(f"Error inserting event {event['event_name']}: {e}")
        
        return inserted


async def run():
    """Main pipeline entry point."""
    logger.info("Fetching economic calendar from Forex Factory...")

    months_ahead = int(os.getenv("FOREXFACTORY_MONTHS_AHEAD", "1") or 0)
    if months_ahead > 0:
        logger.info("Fetching Forex Factory calendar for next %s months", months_ahead)
        events = await fetch_forexfactory_months_ahead(months_ahead)
    else:
        events = await fetch_forexfactory_calendar()
    logger.info(f"Found {len(events)} high/medium impact USD events from Forex Factory")

    if not events:
        logger.warning("No events fetched from Forex Factory; skipping update")
        return

    # Insert events
    inserted = await upsert_events(events)
    logger.info(f"Inserted {inserted} events")
    
    # Log upcoming events
    async with get_connection() as conn:
        upcoming = await conn.fetch("""
            SELECT event_name, event_date, impact, actual, forecast, previous
            FROM economic_event
            WHERE event_date >= NOW()
            ORDER BY event_date
            LIMIT 10
        """)
        logger.info("Upcoming events:")
        for e in upcoming:
            impact = e['impact'] or 'n/a'
            logger.info(f"  {e['event_date'].strftime('%Y-%m-%d')}: {e['event_name']} ({impact})")


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    asyncio.run(run())


if __name__ == '__main__':
    main()
