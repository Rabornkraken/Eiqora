"""
Corporate Actions Crawler using Playwright (async).
Follows MediaCrawler patterns for StockAnalysis scraping.
"""

import asyncio
import logging
import os
import random
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from playwright.async_api import async_playwright, Page, BrowserContext

import psycopg

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]


def get_db_url() -> str:
    """Get database URL from environment."""
    url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/finance")
    return url.replace("postgresql+psycopg://", "postgresql://")


@dataclass
class CorporateAction:
    ticker: str
    action_type: str  # dividend, split, spinoff
    ex_date: date | None
    pay_date: date | None
    ratio: float | None
    cash_amount: float | None
    description: str | None
    source: str


async def create_browser_context(headless: bool = True) -> tuple:
    """Create Playwright browser context with stealth settings."""
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(headless=headless)
    context = await browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        viewport={"width": 1920, "height": 1080},
        locale="en-US",
    )
    return playwright, browser, context


async def fetch_page(context: BrowserContext, url: str, wait_for: str = "table") -> str:
    """Fetch page content using Playwright with stealth mode."""
    page = await context.new_page()
    try:
        await asyncio.sleep(random.uniform(1.0, 3.0))
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        
        # Try to wait for selector, but don't fail if not found
        try:
            await page.wait_for_selector(wait_for, timeout=5000)
        except Exception:
            pass  # Continue even if selector not found
        
        content = await page.content()
        return content
    except Exception as e:
        logger.warning(f"Error fetching {url}: {e}")
        return ""
    finally:
        await page.close()


def parse_date(value: str | None) -> date | None:
    """Parse date from various formats."""
    if not value:
        return None
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%b %d, %Y", "%B %d, %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def parse_amount(value: str | None) -> float | None:
    """Parse cash amount from string."""
    if not value:
        return None
    stripped = value.strip().replace("$", "").replace(",", "")
    try:
        return float(stripped)
    except ValueError:
        return None


def parse_ratio(value: str | None) -> float | None:
    """Parse split ratio from string like '10:1' or '10 for 1'."""
    if not value:
        return None
    raw = value.strip().lower().replace("for", ":").replace(" ", "")
    if ":" in raw:
        parts = raw.split(":", 1)
        try:
            return float(parts[0]) / float(parts[1])
        except ValueError:
            return None
    try:
        return float(raw)
    except ValueError:
        return None


async def scrape_dividends(context: BrowserContext, ticker: str) -> list[CorporateAction]:
    """Scrape dividend history from StockAnalysis."""
    from bs4 import BeautifulSoup
    
    url = f"https://stockanalysis.com/stocks/{ticker.lower()}/dividend/"
    html = await fetch_page(context, url)
    if not html:
        return []
    
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        return []
    
    actions = []
    rows = table.find_all("tr")[1:]
    for row in rows:
        cols = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
        if len(cols) < 4:
            continue
        
        ex_date = parse_date(cols[0])
        pay_date = parse_date(cols[2]) if len(cols) > 2 else None
        amount = parse_amount(cols[3]) if len(cols) > 3 else None
        
        if ex_date:
            actions.append(CorporateAction(
                ticker=ticker,
                action_type="dividend",
                ex_date=ex_date,
                pay_date=pay_date,
                ratio=None,
                cash_amount=amount,
                description=None,
                source="stockanalysis",
            ))
    
    logger.info(f"{ticker} dividends: {len(actions)}")
    return actions


async def scrape_splits(context: BrowserContext, ticker: str) -> list[CorporateAction]:
    """Scrape stock splits - use yfinance since StockAnalysis page may not have table."""
    # Try yfinance first (most reliable)
    actions = await fetch_yfinance_splits(ticker)
    
    if not actions:
        # Fallback to StockAnalysis
        from bs4 import BeautifulSoup
        url = f"https://stockanalysis.com/stocks/{ticker.lower()}/split/"
        html = await fetch_page(context, url, wait_for="body")
        
        if html:
            soup = BeautifulSoup(html, "html.parser")
            table = soup.find("table")
            if table:
                rows = table.find_all("tr")[1:]
                for row in rows:
                    cols = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
                    if len(cols) >= 2:
                        ex_date = parse_date(cols[0])
                        ratio = parse_ratio(cols[1])
                        if ex_date and ratio:
                            actions.append(CorporateAction(
                                ticker=ticker,
                                action_type="split",
                                ex_date=ex_date,
                                pay_date=None,
                                ratio=ratio,
                                cash_amount=None,
                                description=cols[1],
                                source="stockanalysis",
                            ))
    
    logger.info(f"{ticker} splits: {len(actions)}")
    return actions


async def fetch_yfinance_splits(ticker: str) -> list[CorporateAction]:
    """Fallback to yfinance for splits."""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        splits = t.splits
        if splits is None or splits.empty:
            return []
        
        actions = []
        for idx, ratio_val in splits.items():
            ex_date = idx.date() if hasattr(idx, 'date') else None
            if ex_date and ratio_val:
                actions.append(CorporateAction(
                    ticker=ticker,
                    action_type="split",
                    ex_date=ex_date,
                    pay_date=None,
                    ratio=float(ratio_val),
                    cash_amount=None,
                    description=f"{ratio_val}:1",
                    source="yfinance",
                ))
        return actions
    except Exception as e:
        logger.warning(f"yfinance splits error for {ticker}: {e}")
        return []


def upsert_actions(actions: list[CorporateAction]) -> int:
    """Insert or update corporate actions in database (sync)."""
    if not actions:
        return 0
    
    db_url = get_db_url()
    with psycopg.connect(db_url) as conn:
        inserted = 0
        with conn.cursor() as cursor:
            for action in actions:
                try:
                    cursor.execute("""
                        INSERT INTO corporate_action 
                            (ticker, action_type, ex_date, pay_date, ratio, cash_amount, source)
                        SELECT %s, %s, %s, %s, %s, %s, %s
                        WHERE NOT EXISTS (
                            SELECT 1 FROM corporate_action
                            WHERE ticker = %s AND action_type = %s AND ex_date IS NOT DISTINCT FROM %s
                        )
                    """, (
                        action.ticker, action.action_type, action.ex_date, action.pay_date,
                        action.ratio, action.cash_amount, action.source,
                        action.ticker, action.action_type, action.ex_date,
                    ))
                    if cursor.rowcount:
                        inserted += 1
                except Exception as e:
                    logger.error(f"Error inserting {action.ticker} {action.action_type}: {e}")
        conn.commit()
        return inserted


async def run(tickers: list[str] | None = None):
    """Main pipeline entry point."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    
    # Get tickers from security table if not provided
    if not tickers:
        db_url = get_db_url()
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT DISTINCT ticker FROM security LIMIT 50")
                tickers = [row[0] for row in cursor.fetchall()]
    
    logger.info(f"Processing {len(tickers)} tickers with Playwright...")
    
    playwright, browser, context = await create_browser_context()
    
    try:
        total_inserted = 0
        for ticker in tickers:
            # Scrape all action types
            dividends = await scrape_dividends(context, ticker)
            splits = await scrape_splits(context, ticker)
            
            all_actions = dividends + splits
            inserted = upsert_actions(all_actions)  # Sync call
            total_inserted += inserted
            
            logger.info(f"{ticker}: {len(all_actions)} actions, {inserted} inserted")
            
            # Delay between tickers
            await asyncio.sleep(random.uniform(2.0, 5.0))
        
        logger.info(f"Total inserted: {total_inserted}")
        
    finally:
        await context.close()
        await browser.close()
        await playwright.stop()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Corporate Actions Playwright Crawler")
    parser.add_argument("--tickers", nargs="+", help="Specific tickers to process")
    args = parser.parse_args()
    
    asyncio.run(run(tickers=args.tickers))


if __name__ == "__main__":
    main()
