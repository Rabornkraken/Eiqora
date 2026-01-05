"""
SEC XBRL Revenue Extraction Pipeline.
Extracts revenue from 10-Q/10-K XBRL filings and updates earnings_event table.
"""

import asyncio
import logging
import requests
from datetime import datetime
from xml.etree import ElementTree
from typing import Any

from eiqora_v2.tools.db import get_connection

logger = logging.getLogger(__name__)

# XBRL namespaces
XBRL_NS = {
    'xbrli': 'http://www.xbrl.org/2003/instance',
    'us-gaap': 'http://fasb.org/us-gaap/2024',
}

# Revenue tags to look for (in order of preference)
REVENUE_TAGS = [
    'Revenues',
    'RevenueFromContractWithCustomerExcludingAssessedTax',
    'SalesRevenueNet',
    'NetRevenues',
    'TotalRevenue',
]


def _fetch_sec_submissions(cik: str) -> list[dict]:
    """Fetch all recent filings from SEC submissions API."""
    padded_cik = str(cik).zfill(10)
    url = f'https://data.sec.gov/submissions/CIK{padded_cik}.json'
    resp = requests.get(url, headers={'User-Agent': 'Eiqora Research hello@example.com'}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    
    # Parse recent filings
    recent = data.get('filings', {}).get('recent', {})
    accessions = recent.get('accessionNumber', [])
    forms = recent.get('form', [])
    filing_dates = recent.get('filingDate', [])
    report_dates = recent.get('reportDate', [])
    
    filings = []
    for i, form in enumerate(forms):
        if form in ('10-Q', '10-K'):
            filings.append({
                'accession': accessions[i] if i < len(accessions) else None,
                'form': form,
                'filed_at': filing_dates[i] if i < len(filing_dates) else None,
                'report_period': report_dates[i] if i < len(report_dates) else None,
            })
    
    return filings


def _fetch_xbrl_index(cik: str, accession: str) -> list[dict]:
    """Fetch filing index to find XBRL files."""
    acc_no_dash = accession.replace('-', '')
    url = f'https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_no_dash}/index.json'
    resp = requests.get(url, headers={'User-Agent': 'Eiqora Research hello@example.com'}, timeout=30)
    resp.raise_for_status()
    return resp.json().get('directory', {}).get('item', [])


def _find_xbrl_file(items: list[dict]) -> str | None:
    """Find the _htm.xml XBRL file in the filing."""
    for item in items:
        name = item.get('name', '')
        if name.endswith('_htm.xml'):
            return name
    return None


def _fetch_xbrl_content(cik: str, accession: str, filename: str) -> bytes:
    """Fetch XBRL file content."""
    acc_no_dash = accession.replace('-', '')
    url = f'https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_no_dash}/{filename}'
    resp = requests.get(url, headers={'User-Agent': 'Eiqora Research hello@example.com'}, timeout=60)
    resp.raise_for_status()
    return resp.content


def _parse_contexts(root: ElementTree.Element) -> dict[str, dict]:
    """Parse XBRL context definitions to get period information."""
    contexts = {}
    for ctx in root.findall('.//xbrli:context', XBRL_NS):
        ctx_id = ctx.get('id')
        if not ctx_id:
            continue
        
        period = ctx.find('.//xbrli:period', XBRL_NS)
        if period is None:
            continue
            
        start = period.find('xbrli:startDate', XBRL_NS)
        end = period.find('xbrli:endDate', XBRL_NS)
        instant = period.find('xbrli:instant', XBRL_NS)
        
        # Check if this is a segment (dimension) context - skip those for now
        segment = ctx.find('.//xbrli:segment', XBRL_NS)
        if segment is not None:
            continue
        
        if start is not None and end is not None:
            start_date = datetime.strptime(start.text, '%Y-%m-%d').date()
            end_date = datetime.strptime(end.text, '%Y-%m-%d').date()
            duration = (end_date - start_date).days
            
            contexts[ctx_id] = {
                'start': start_date,
                'end': end_date,
                'duration_days': duration,
                'is_quarterly': 80 <= duration <= 100,  # ~3 months
                'is_annual': 350 <= duration <= 380,    # ~12 months
            }
    
    return contexts


def _extract_revenues(root: ElementTree.Element, contexts: dict) -> list[dict]:
    """Extract quarterly revenue values from XBRL."""
    revenues = []
    
    for elem in root.iter():
        tag_name = elem.tag.split('}')[-1] if '}' in elem.tag else elem.tag
        
        if tag_name in REVENUE_TAGS:
            ctx_ref = elem.get('contextRef')
            value = elem.text
            
            if not ctx_ref or not value:
                continue
                
            ctx = contexts.get(ctx_ref)
            if not ctx or not ctx.get('is_quarterly'):
                continue
            
            try:
                revenue = float(value)
            except ValueError:
                continue
            
            revenues.append({
                'period_end': ctx['end'],
                'period_start': ctx['start'],
                'revenue': revenue,
                'tag': tag_name,
            })
    
    return revenues


def extract_revenue_from_filing(cik: str, accession: str) -> list[dict]:
    """
    Extract quarterly revenue from a single SEC filing.
    Returns list of {period_end, revenue} dicts.
    """
    try:
        # 1. Get filing index
        items = _fetch_xbrl_index(cik, accession)
        
        # 2. Find XBRL file
        xbrl_file = _find_xbrl_file(items)
        if not xbrl_file:
            logger.warning(f"No XBRL file found for {accession}")
            return []
        
        # 3. Fetch and parse XBRL
        content = _fetch_xbrl_content(cik, accession, xbrl_file)
        root = ElementTree.fromstring(content)
        
        # 4. Parse contexts
        contexts = _parse_contexts(root)
        
        # 5. Extract revenues
        revenues = _extract_revenues(root, contexts)
        
        return revenues
        
    except Exception as e:
        logger.error(f"Error extracting revenue from {accession}: {e}")
        return []


async def update_earnings_with_revenue(symbol: str, max_filings: int = 20) -> int:
    """
    Update earnings_event table with revenue data from SEC filings.
    Fetches filings directly from SEC API for complete history.
    Returns number of records updated.
    """
    import time
    updated = 0
    
    async with get_connection() as conn:
        # Get CIK for symbol
        cik_row = await conn.fetchrow(
            "SELECT cik FROM security WHERE ticker = $1 AND is_primary = TRUE LIMIT 1",
            symbol
        )
        if not cik_row:
            logger.error(f"No CIK found for {symbol}")
            return 0
        
        cik = cik_row['cik']
        
        # Fetch filings directly from SEC API (more complete than local DB)
        try:
            filings = _fetch_sec_submissions(cik)
            filings = filings[:max_filings]  # Limit to avoid too many requests
        except Exception as e:
            logger.error(f"Failed to fetch SEC submissions for {symbol}: {e}")
            return 0
        
        logger.info(f"Processing {len(filings)} 10-Q/10-K filings for {symbol} from SEC API")
        
        for filing in filings:
            accession = filing['accession']
            if not accession:
                continue
            
            # Rate limit to respect SEC API limits (10 req/sec max)
            time.sleep(0.2)
            
            # Extract revenue
            revenues = extract_revenue_from_filing(cik, accession)
            
            for rev in revenues:
                period_end = rev['period_end']
                revenue = rev['revenue']
                
                # Find matching earnings record (within 30 days of period end)
                result = await conn.execute("""
                    UPDATE earnings_event
                    SET revenue_actual = $1, updated_at = NOW()
                    WHERE symbol = $2
                      AND earnings_date BETWEEN ($3::date - interval '30 days')::date 
                                            AND ($3::date + interval '60 days')::date
                      AND revenue_actual IS NULL
                """, revenue, symbol, period_end)
                
                if result and 'UPDATE' in result:
                    count = int(result.split()[-1])
                    if count > 0:
                        logger.info(f"Updated {symbol} {period_end}: revenue={revenue/1e9:.2f}B")
                        updated += count
        
        # Recalculate YoY for ALL records
        await conn.execute("""
            UPDATE earnings_event e
            SET revenue_growth_yoy = (
                (e.revenue_actual - prev.revenue_actual) / NULLIF(ABS(prev.revenue_actual), 0) * 100
            )
            FROM earnings_event prev
            WHERE e.symbol = prev.symbol
              AND e.symbol = $1
              AND prev.earnings_date < e.earnings_date - interval '10 months'
              AND prev.earnings_date > e.earnings_date - interval '14 months'
              AND e.revenue_actual IS NOT NULL
              AND prev.revenue_actual IS NOT NULL
        """, symbol)
        
    return updated


async def main():
    """Run extraction for NVDA as test."""
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    
    updated = await update_earnings_with_revenue('NVDA')
    print(f"\nUpdated {updated} earnings records")


if __name__ == '__main__':
    asyncio.run(main())
