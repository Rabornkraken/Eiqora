"""
Economic Calendar Pipeline.
Fetches upcoming economic events from Investing.com economic calendar.
"""

import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import Any

import requests
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


async def fetch_forexfactory_calendar() -> list[dict[str, Any]]:
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
    url = "https://www.forexfactory.com/calendar"
    
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
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
        current_date = datetime.now().date()
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
                            current_date = parsed.date()
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


def get_fomc_dates() -> list[dict]:
    """Return known FOMC meeting dates for 2024-2025."""
    # FOMC schedule (official)
    fomc_dates = [
        # 2024 (for historical context)
        ('2024-01-31', 'FOMC Rate Decision'),
        ('2024-03-20', 'FOMC Rate Decision'),
        ('2024-05-01', 'FOMC Rate Decision'),
        ('2024-06-12', 'FOMC Rate Decision'),
        ('2024-07-31', 'FOMC Rate Decision'),
        ('2024-09-18', 'FOMC Rate Decision'),
        ('2024-11-07', 'FOMC Rate Decision'),
        ('2024-12-18', 'FOMC Rate Decision'),
        # 2025
        ('2025-01-29', 'FOMC Rate Decision'),
        ('2025-03-19', 'FOMC Rate Decision'),
        ('2025-05-07', 'FOMC Rate Decision'),
        ('2025-06-18', 'FOMC Rate Decision'),
        ('2025-07-30', 'FOMC Rate Decision'),
        ('2025-09-17', 'FOMC Rate Decision'),
        ('2025-11-05', 'FOMC Rate Decision'),
        ('2025-12-17', 'FOMC Rate Decision'),
    ]
    
    events = []
    for date_str, name in fomc_dates:
        dt = datetime.strptime(date_str, '%Y-%m-%d')
        events.append({
            'event_name': name,
            'event_date': dt.replace(hour=14, minute=0),  # 2 PM ET
            'country': 'US',
            'impact': 'high',
            'source': 'federal_reserve',
        })
    return events


def get_macro_release_dates() -> list[dict]:
    """
    Return scheduled release dates for major economic indicators.
    Generates dates dynamically for the next 12 months.
    """
    from datetime import date
    events = []
    
    # Get current year and next year
    today = date.today()
    current_year = today.year
    next_year = current_year + 1
    
    # Jobs Report (Non-Farm Payrolls) - First Friday of each month, 8:30 AM ET
    # Approximate dates for 2026
    nfp_dates_2026 = [
        '2026-01-09', '2026-02-06', '2026-03-06', '2026-04-03', 
        '2026-05-08', '2026-06-05', '2026-07-02', '2026-08-07',
        '2026-09-04', '2026-10-02', '2026-11-06', '2026-12-04',
    ]
    for date_str in nfp_dates_2026:
        dt = datetime.strptime(date_str, '%Y-%m-%d')
        events.append({
            'event_name': 'Non-Farm Payrolls',
            'event_date': dt.replace(hour=8, minute=30),
            'country': 'US',
            'impact': 'high',
            'source': 'bls',
        })
        events.append({
            'event_name': 'Unemployment Rate',
            'event_date': dt.replace(hour=8, minute=30),
            'country': 'US',
            'impact': 'high',
            'source': 'bls',
        })
    
    # CPI Report - Mid-month, 8:30 AM ET (2026)
    cpi_dates_2026 = [
        '2026-01-14', '2026-02-11', '2026-03-11', '2026-04-14',
        '2026-05-12', '2026-06-10', '2026-07-14', '2026-08-12',
        '2026-09-11', '2026-10-13', '2026-11-12', '2026-12-11',
    ]
    for date_str in cpi_dates_2026:
        dt = datetime.strptime(date_str, '%Y-%m-%d')
        events.append({
            'event_name': 'CPI (Consumer Price Index)',
            'event_date': dt.replace(hour=8, minute=30),
            'country': 'US',
            'impact': 'high',
            'source': 'bls',
        })
    
    # FOMC 2026 (estimated based on typical schedule)
    fomc_2026 = [
        ('2026-01-28', 'FOMC Rate Decision'),
        ('2026-03-18', 'FOMC Rate Decision'),
        ('2026-05-06', 'FOMC Rate Decision'),
        ('2026-06-17', 'FOMC Rate Decision'),
        ('2026-07-29', 'FOMC Rate Decision'),
        ('2026-09-16', 'FOMC Rate Decision'),
        ('2026-11-04', 'FOMC Rate Decision'),
        ('2026-12-16', 'FOMC Rate Decision'),
    ]
    for date_str, name in fomc_2026:
        dt = datetime.strptime(date_str, '%Y-%m-%d')
        events.append({
            'event_name': name,
            'event_date': dt.replace(hour=14, minute=0),
            'country': 'US',
            'impact': 'high',
            'source': 'federal_reserve',
        })
    
    # GDP 2026 (Quarterly reports)
    gdp_dates_2026 = [
        ('2026-01-29', 'GDP (Q4 2025 Advance)'),
        ('2026-02-26', 'GDP (Q4 2025 Second)'),
        ('2026-03-26', 'GDP (Q4 2025 Third)'),
        ('2026-04-29', 'GDP (Q1 2026 Advance)'),
        ('2026-05-28', 'GDP (Q1 2026 Second)'),
        ('2026-06-25', 'GDP (Q1 2026 Third)'),
        ('2026-07-29', 'GDP (Q2 2026 Advance)'),
        ('2026-08-27', 'GDP (Q2 2026 Second)'),
        ('2026-09-24', 'GDP (Q2 2026 Third)'),
        ('2026-10-29', 'GDP (Q3 2026 Advance)'),
    ]
    for date_str, name in gdp_dates_2026:
        dt = datetime.strptime(date_str, '%Y-%m-%d')
        events.append({
            'event_name': name,
            'event_date': dt.replace(hour=8, minute=30),
            'country': 'US',
            'impact': 'high',
            'source': 'bea',
        })
    
    # PCE 2026 (End of month)
    pce_dates_2026 = [
        '2026-01-30', '2026-02-27', '2026-03-27', '2026-04-30',
        '2026-05-29', '2026-06-26', '2026-07-31', '2026-08-28',
        '2026-09-25', '2026-10-30', '2026-11-25', '2026-12-23',
    ]
    for date_str in pce_dates_2026:
        dt = datetime.strptime(date_str, '%Y-%m-%d')
        events.append({
            'event_name': 'PCE Price Index',
            'event_date': dt.replace(hour=8, minute=30),
            'country': 'US',
            'impact': 'high',
            'source': 'bea',
        })
    
    # Initial Jobless Claims - Every Thursday for next 52 weeks
    # Find next Thursday from today
    days_until_thursday = (3 - today.weekday()) % 7
    if days_until_thursday == 0:
        days_until_thursday = 7  # If today is Thursday, start next week
    next_thursday = today + timedelta(days=days_until_thursday)
    
    for week in range(52):
        claim_date = next_thursday + timedelta(days=7 * week)
        dt = datetime.combine(claim_date, datetime.min.time())
        events.append({
            'event_name': 'Initial Jobless Claims',
            'event_date': dt.replace(hour=8, minute=30),
            'country': 'US',
            'impact': 'medium',
            'source': 'dol',
        })
    
    return events


async def upsert_events(events: list[dict]) -> int:
    """Insert or update events in database."""
    if not events:
        return 0
    
    async with get_connection() as conn:
        inserted = 0
        for event in events:
            try:
                await conn.execute("""
                    INSERT INTO economic_event (event_name, event_date, country, actual, forecast, previous, impact, source)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    ON CONFLICT (event_name, event_date, country) 
                    DO UPDATE SET actual = EXCLUDED.actual, forecast = EXCLUDED.forecast, 
                                  previous = EXCLUDED.previous, impact = EXCLUDED.impact
                """,
                    event['event_name'],
                    event.get('event_date', datetime.now()),
                    event.get('country', 'US'),
                    event.get('actual'),
                    event.get('forecast'),
                    event.get('previous'),
                    event.get('impact', 'medium'),
                    event.get('source', 'unknown'),
                )
                inserted += 1
            except Exception as e:
                logger.error(f"Error inserting event {event['event_name']}: {e}")
        
        return inserted


async def run():
    """Main pipeline entry point."""
    logger.info("Fetching economic calendar from Forex Factory...")
    
    # Fetch live data from Forex Factory (high + medium impact USD events only)
    events = await fetch_forexfactory_calendar()
    logger.info(f"Found {len(events)} high/medium impact USD events from Forex Factory")
    
    if not events:
        logger.warning("No events fetched from Forex Factory")
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
