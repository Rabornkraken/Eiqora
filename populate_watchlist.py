from data_collection.db.connection import get_connection
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def populate_watchlist():
    # Read symbols
    with open('/Users/pan/Documents/Github/Eiqora/data_collection/config/symbols.txt', 'r') as f:
        symbols = [line.strip() for line in f if line.strip()]
    
    logger.info(f"Found {len(symbols)} symbols in config file")
    
    conn = get_connection()
    cur = conn.cursor()
    
    added = 0
    existing_count = 0
    
    for symbol in symbols:
        # Check if exists
        cur.execute("SELECT symbol FROM watchlist WHERE symbol = %s", (symbol,))
        if cur.fetchone():
            existing_count += 1
        else:
            # Insert with default values
            cur.execute("""
                INSERT INTO watchlist (symbol, created_at, scan_date) 
                VALUES (%s, NOW(), NOW())
            """, (symbol,))
            added += 1
            
    conn.commit()
    conn.close()
    
    logger.info(f"Watchlist update: {added} added, {existing_count} already existed")

if __name__ == "__main__":
    populate_watchlist()
