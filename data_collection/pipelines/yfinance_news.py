"""
YFinance News Pipeline - Fetches clean, structured news for tickers
Replaces GDELT garbage extraction with editor-curated summaries
"""
import logging
import asyncio
from datetime import datetime, timezone
import yfinance as yf

from data_collection.db.connection import get_connection

# Import FinBERT scoring and text extraction from gdelt pipeline
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))
from gdelt import _get_sentiment_pipeline, _chunk_text, _extract_text
from cdp_sync import fetch_with_cdp, cleanup_cdp_browser

_logger = logging.getLogger(__name__)


def _get_active_symbols(conn, limit: int | None = None) -> list[str]:
    """Get active symbols from watchlist."""
    with conn.cursor() as cursor:
        cursor.execute("""
            SELECT DISTINCT symbol 
            FROM watchlist 
            ORDER BY symbol
        """)
        symbols = [row[0] for row in cursor.fetchall()]
        return symbols[:limit] if limit else symbols


def _score_news(title: str, text: str) -> float:
    """
    Score news sentiment using FinBERT on full article text.
    
    Args:
        title: News title
        text: Full article text (extracted from HTML)
    
    Returns:
        float: Sentiment score (-10 to +10)
    """
    try:
        pipe = _get_sentiment_pipeline()
        
        # Combine title and full text
        full_text = f"{title}. {text or ''}"
        
        # Chunk the full text
        chunks = _chunk_text(full_text)[:5]
        
        if not chunks:
            return 0.0
        
        # Run FinBERT
        results = pipe(chunks)
        
        # Aggregate sentiment
        total_sentiment = 0.0
        for res in results:
            lbl = res['label'].lower()
            conf = res['score']
            
            if lbl == 'positive':
                total_sentiment += conf * 10
            elif lbl == 'negative':
                total_sentiment -= conf * 10
            # neutral adds 0
        
        # Average across chunks
        avg_sentiment = total_sentiment / len(chunks)
        return avg_sentiment
        
    except Exception as e:
        _logger.warning(f"FinBERT scoring failed: {e}")
        return 0.0


def collect_news(limit_symbols: int | None = None, lookback_hours: int = 48):
    """
    Fetch and store YFinance news (WITHOUT scoring).
    Scoring is done separately in score_news().
    
    Args:
        limit_symbols: Max number of symbols to process (None = all)
        lookback_hours: Skip news older than this (avoid re-ingesting)
    """
    conn = get_connection()
    
    try:
        symbols = _get_active_symbols(conn, limit_symbols)
        _logger.info(f"Fetching news for {len(symbols)} symbols")
        
        total_new = 0
        total_skipped = 0
        
        for symbol in symbols:
            try:
                # Fetch news via yfinance
                ticker = yf.Ticker(symbol)
                news_items = ticker.news
                
                if not news_items:
                    _logger.debug(f"{symbol}: No news found")
                    continue
                
                _logger.info(f"{symbol}: Found {len(news_items)} news items")
                
                for item in news_items:
                    try:
                        # Extract fields
                        content = item.get('content', {})
                        news_id = content.get('id')
                        title = content.get('title', '')
                        pub_date_str = content.get('pubDate')
                        provider = content.get('provider', {}).get('displayName', 'Unknown')
                        url = content.get('canonicalUrl', {}).get('url', '')
                        
                        if not news_id or not title:
                            _logger.debug(f"{symbol}: Skipping item without id/title")
                            continue
                        
                        # Parse pub date
                        published_at = None
                        if pub_date_str:
                            try:
                                published_at = datetime.fromisoformat(pub_date_str.replace('Z', '+00:00'))
                            except Exception:
                                pass
                        
                        # Check if already exists
                        with conn.cursor() as cur:
                            cur.execute("SELECT doc_id FROM yfinance_news WHERE news_id = %s", (news_id,))
                            existing = cur.fetchone()
                            
                            if existing:
                                total_skipped += 1
                                continue
                        
                        # SKIP SUBSCRIPTION SITES & BAD PROVIDERS
                        # These sites require paywalls or logins that we cannot bypass
                        # Or they are consistently low quality / empty text
                        
                        subscription_domains = [
                            'barrons.com', 
                            'wsj.com', 
                            'marketwatch.com', 
                            'marketbeat.com', 
                            'mtnewswires.com',
                            'investors.com', # IBD
                            'bloomberg.com',
                            'seekingalpha.com',
                            'thestreet.com',   # 100% fail rate
                            'telegraph.co.uk',
                            'qz.com', # Quartz
                        ]
                        
                        blocked_providers = [
                            'MT Newswires',
                            'Barrons.com',
                            'The Wall Street Journal', 
                            'MarketWatch',
                            'TheStreet',
                            'Bloomberg',
                            'MarketBeat'
                        ]
                        
                        should_skip = False
                        
                        # Check URL domains
                        if url:
                            for domain in subscription_domains:
                                if domain in url.lower():
                                    should_skip = True
                                    _logger.info(f"{symbol}: Skipping domain: {domain}")
                                    break
                        
                        # Check Provider Name (for syndicated content on Yahoo)
                        if not should_skip and provider:
                            if provider in blocked_providers:
                                should_skip = True
                                _logger.info(f"{symbol}: Skipping provider: {provider}")
                        
                        if should_skip:
                             # Skip CDP fetch, insert blank or skip entirely?
                             # Let's skip entirely to avoid DB clutter
                             total_skipped += 1
                             continue

                        # Fetch full article text using CDP browser
                        article_text = ""
                        if url:
                            html, error = fetch_with_cdp(url)
                            
                            if error:
                                _logger.warning(f"{symbol}: CDP fetch failed for {url}: {error}")
                            elif html:
                                article_text = _extract_text(html)
                                _logger.debug(f"{symbol}: Extracted {len(article_text)} chars from {url}")
                        
                        # Store article WITHOUT scoring (scoring is separate step)
                        with conn.cursor() as cur:
                            cur.execute("""
                                INSERT INTO yfinance_news 
                                (news_id, ticker, title, text, published_at, provider, url)
                                VALUES (%s, %s, %s, %s, %s, %s, %s)
                                RETURNING doc_id
                            """, (news_id, symbol, title, article_text, published_at, provider, url))
                            
                            doc_id = cur.fetchone()[0]
                            conn.commit()
                            total_new += 1
                            
                            _logger.info(f"{symbol}: Saved '{title[:50]}...' (doc_id: {doc_id})")
                    
                    except Exception as e:
                        _logger.warning(f"{symbol}: Failed to process news item: {e}")
                        continue
            
            except Exception as e:
                _logger.error(f"{symbol}: Failed to fetch news: {e}")
                continue
        
        _logger.info(f"Collection complete: {total_new} new, {total_skipped} skipped")
        
        # Cleanup CDP browser
        cleanup_cdp_browser()
    
    finally:
        conn.close()


def score_news(limit: int | None = None):
    """
    Score sentiment for unscored YFinance news articles.
    Runs FinBERT on articles that don't have scores yet.
    
    Args:
        limit: Max articles to score in this run (None = all unscored)
    """
    conn = get_connection()
    
    try:
        # Get unscored articles
        with conn.cursor() as cur:
            cur.execute("""
                SELECT yn.doc_id, yn.title, yn.text
                FROM yfinance_news yn
                LEFT JOIN yfinance_news_relevance nr ON yn.doc_id = nr.doc_id
                WHERE nr.doc_id IS NULL
                ORDER BY yn.created_at DESC
                LIMIT %s
            """, (limit or 999999,))
            
            articles = cur.fetchall()
        
        if not articles:
            _logger.info("No unscored articles found")
            return
        
        _logger.info(f"Scoring {len(articles)} articles with FinBERT...")
        
        scored = 0
        for doc_id, title, text in articles:
            try:
                # Score with FinBERT
                score = _score_news(title, text or "")
                
                # Insert score
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO yfinance_news_relevance (doc_id, score, model_version, scored_at)
                        VALUES (%s, %s, %s, NOW())
                        ON CONFLICT (doc_id) DO UPDATE
                        SET score = EXCLUDED.score, scored_at = NOW()
                    """, (doc_id, score, 'finbert_v1'))
                    conn.commit()
                
                scored += 1
                _logger.info(f"Scored doc_id {doc_id}: {score:.2f} - '{title[:50]}...'")
                
            except Exception as e:
                _logger.error(f"Failed to score doc_id {doc_id}: {e}")
        
        _logger.info(f"Scoring complete: {scored}/{len(articles)} articles")
    
    finally:
        conn.close()


def run(limit_symbols: int | None = None):
    """
    Combined run: collect news then score them.
    """
    collect_news(limit_symbols)
    score_news()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s'
    )
    
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, help='Max symbols to process')
    parser.add_argument('--lookback-hours', type=int, default=48, help='Lookback window')
    args = parser.parse_args()
    
    run(limit_symbols=args.limit, lookback_hours=args.lookback_hours)
