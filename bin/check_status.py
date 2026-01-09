#!/usr/bin/env python
"""Quick check of system state."""

import sys
import os
# Add parent directory to path so we can import data_collection
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_collection.db.connection import get_connection

conn = get_connection()
cur = conn.cursor()

print("\n" + "="*60)
print("EIQORA SYSTEM STATUS")
print("="*60 + "\n")

# Check news
cur.execute("SELECT COUNT(*) FROM yfinance_news WHERE published_at >= NOW() - interval '24 hours'")
news_24h = cur.fetchone()[0]
print(f"News (24h): {news_24h} articles")

# Check watchlist
cur.execute("SELECT COUNT(*) FROM watchlist WHERE scan_date = CURRENT_DATE")
watchlist_count = cur.fetchone()[0]
print(f"Watchlist: {watchlist_count} candidates")

if watchlist_count > 0:
    cur.execute("""
        SELECT symbol, total_score 
        FROM watchlist 
        WHERE scan_date = CURRENT_DATE 
        ORDER BY total_score DESC 
        LIMIT 5
    """)
    print("\nTop 5:")
    for row in cur.fetchall():
        score = row[1] if row[1] is not None else 0.0
        print(f"  {row[0]}: {score:.2f}")

# Check signals
cur.execute("SELECT COUNT(*) FROM signal WHERE created_at >= NOW() - interval '24 hours'")
signals_24h = cur.fetchone()[0]
print(f"\nSignals (24h): {signals_24h}")

conn.close()

print("\n" + "="*60)
print("\nNext steps:")
print("1. If watchlist = 0: Need to build watchlist first")
print("2. If watchlist > 0: Scan for triggers")
print("3. If triggers found: Run LLM to generate signals")
print()
