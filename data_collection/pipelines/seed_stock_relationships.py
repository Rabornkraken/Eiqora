"""
Seed known stock relationships.
Run manually: python -m data_collection.pipelines.seed_stock_relationships
"""

import logging
from data_collection.db.connection import get_connection

logger = logging.getLogger(__name__)

# Known supply chain and competitive relationships
RELATIONSHIPS = [
    # Suppliers
    ("NVDA", "TSM", "SUPPLIER", 0.90, "TSM manufactures NVDA's GPU chips"),
    ("AAPL", "TSM", "SUPPLIER", 0.85, "TSM manufactures Apple's A-series chips"),
    ("AAPL", "QCOM", "SUPPLIER", 0.60, "Qualcomm modems in iPhones"),
    ("TSLA", "LG", "SUPPLIER", 0.70, "LG supplies EV battery cells"),
    ("F", "APTV", "SUPPLIER", 0.65, "Aptiv supplies auto components"),
    
    # Competitors
    ("NVDA", "AMD", "COMPETITOR", 0.80, "GPU and datacenter chip rivals"),
    ("AAPL", "MSFT", "COMPETITOR", 0.70, "Consumer devices and cloud services"),
    ("TSLA", "F", "COMPETITOR", 0.60, "EV market competition"),
    ("TSLA", "GM", "COMPETITOR", 0.60, "EV market competition"),
    ("JPM", "BAC", "COMPETITOR", 0.75, "Investment banking rivals"),
    ("JPM", "GS", "COMPETITOR", 0.80, "Investment banking rivals"),
    ("JPM", "MS", "COMPETITOR", 0.75, "Investment banking rivals"),
    ("GOOGL", "META", "COMPETITOR", 0.70, "Digital advertising"),
    ("KO", "PEP", "COMPETITOR", 0.80, "Beverage market"),
    
    # Related (same sector/theme)
    ("GOOGL", "GOOG", "RELATED", 1.00, "Same company, different share class"),
    ("BRK.A", "BRK.B", "RELATED", 1.00, "Same company, different share class"),
    ("XOM", "CVX", "PEER", 0.75, "Oil & gas majors"),
    ("WMT", "TGT", "PEER", 0.70, "Retail chains"),
]


def run():
    """Seed the stock_relationships table with known relationships."""
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    
    conn = get_connection()
    try:
        inserted = 0
        
        for from_sym, to_sym, rel_type, strength, desc in RELATIONSHIPS:
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO stock_relationships (
                        symbol_from, symbol_to, relationship_type, strength, description
                    ) VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (symbol_from, symbol_to, relationship_type) DO NOTHING
                """, (from_sym, to_sym, rel_type, strength, desc))
                
                if cursor.rowcount > 0:
                    inserted += 1
                    logger.info(f"✓ {from_sym} -> {to_sym} ({rel_type}): {desc}")
        
        conn.commit()
        logger.info(f"Seeded {inserted} new relationships ({len(RELATIONSHIPS)} total)")
    
    finally:
        conn.close()


if __name__ == '__main__':
    run()
