#!/usr/bin/env python3
"""
YFinance News Collection + Sentiment Analysis Script
Collects news for all watchlist tickers and scores with FinBERT
"""
import argparse
import logging
import sys
from datetime import datetime

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('/tmp/yfinance_news_collection.log')
    ]
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description='Collect YFinance news and run sentiment analysis')
    parser.add_argument('--days', type=int, default=10, help='Lookback days (default: 10)')
    parser.add_argument('--limit-symbols', type=int, help='Limit number of symbols (default: all)')
    parser.add_argument('--skip-collection', action='store_true', help='Skip collection, only run scoring')
    parser.add_argument('--skip-scoring', action='store_true', help='Skip scoring, only collect')
    args = parser.parse_args()
    
    logger.info("="*70)
    logger.info(f"YFinance News Pipeline - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    logger.info(f"Lookback: {args.days} days")
    logger.info("="*70)
    
    from data_collection.pipelines.yfinance_news import collect_news, score_news
    from data_collection.db.connection import get_connection
    
    # Show current stats
    conn = get_connection()
    cur = conn.cursor()
    cur.execute('SELECT COUNT(*) FROM yfinance_news')
    existing = cur.fetchone()[0]
    logger.info(f"Existing articles in DB: {existing}")
    conn.close()
    
    # Step 1: Collect news
    if not args.skip_collection:
        logger.info("")
        logger.info("="*70)
        logger.info("STEP 1: Collecting news articles")
        logger.info("="*70)
        
        collect_news(
            limit_symbols=args.limit_symbols,
            lookback_hours=args.days * 24
        )
    
    # Step 2: Score with FinBERT
    if not args.skip_scoring:
        logger.info("")
        logger.info("="*70)
        logger.info("STEP 2: Running FinBERT sentiment analysis")
        logger.info("="*70)
        
        score_news(limit=None)  # Score all unscored
    
    # Final stats
    logger.info("")
    logger.info("="*70)
    logger.info("FINAL RESULTS")
    logger.info("="*70)
    
    conn = get_connection()
    cur = conn.cursor()
    
    # Total articles
    cur.execute('SELECT COUNT(*), COUNT(DISTINCT ticker) FROM yfinance_news')
    total, tickers = cur.fetchone()
    logger.info(f"Total articles: {total} across {tickers} tickers")
    
    # With scores
    cur.execute('SELECT COUNT(*) FROM yfinance_news_relevance')
    scored = cur.fetchone()[0]
    logger.info(f"Articles with scores: {scored}")
    
    # Score distribution
    cur.execute('''
        SELECT 
            CASE 
                WHEN score > 5 THEN 'Very Positive (>5)'
                WHEN score > 2 THEN 'Positive (2-5)'
                WHEN score > -2 THEN 'Neutral (-2 to 2)'
                WHEN score > -5 THEN 'Negative (-5 to -2)'
                ELSE 'Very Negative (<-5)'
            END as category,
            COUNT(*)
        FROM yfinance_news_relevance
        GROUP BY category
        ORDER BY MIN(score) DESC
    ''')
    
    logger.info("")
    logger.info("Score Distribution:")
    for cat, count in cur.fetchall():
        pct = count / scored * 100 if scored > 0 else 0
        logger.info(f"  {cat}: {count} ({pct:.1f}%)")
    
    # Top positive articles
    cur.execute('''
        SELECT yn.ticker, yn.title, nr.score
        FROM yfinance_news yn
        JOIN yfinance_news_relevance nr ON yn.doc_id = nr.doc_id
        ORDER BY nr.score DESC
        LIMIT 3
    ''')
    logger.info("")
    logger.info("Most Positive:")
    for ticker, title, score in cur.fetchall():
        logger.info(f"  [{ticker}] {score:+.1f}: {title[:60]}...")
    
    # Top negative articles
    cur.execute('''
        SELECT yn.ticker, yn.title, nr.score
        FROM yfinance_news yn
        JOIN yfinance_news_relevance nr ON yn.doc_id = nr.doc_id
        ORDER BY nr.score ASC
        LIMIT 3
    ''')
    logger.info("")
    logger.info("Most Negative:")
    for ticker, title, score in cur.fetchall():
        logger.info(f"  [{ticker}] {score:+.1f}: {title[:60]}...")
    
    conn.close()
    logger.info("")
    logger.info("="*70)
    logger.info("Complete!")
    logger.info("="*70)


if __name__ == "__main__":
    main()
