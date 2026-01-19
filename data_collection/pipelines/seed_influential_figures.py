"""
Seed influential figures.
Run manually: python -m data_collection.pipelines.seed_influential_figures
"""

import logging
from data_collection.db.connection import get_connection

logger = logging.getLogger(__name__)

# Key market-moving figures
FIGURES = [
    # Fed / Central Banks
    ("Jerome Powell", "Chairman", "Federal Reserve", "FED", 0.98),
    ("Janet Yellen", "Secretary", "US Treasury", "FED", 0.90),
    ("Christine Lagarde", "President", "European Central Bank", "FED", 0.85),
    
    # CEOs
    ("Elon Musk", "CEO", "Tesla", "CEO", 0.95),
    ("Tim Cook", "CEO", "Apple", "CEO", 0.85),
    ("Jensen Huang", "CEO", "NVIDIA", "CEO", 0.90),
    ("Satya Nadella", "CEO", "Microsoft", "CEO", 0.80),
    ("Jamie Dimon", "CEO", "JPMorgan Chase", "CEO", 0.85),
    
    # Investors
    ("Warren Buffett", "CEO", "Berkshire Hathaway", "INVESTOR", 0.90),
    ("Cathie Wood", "CEO", "ARK Invest", "INVESTOR", 0.85),
    ("Ray Dalio", "Founder", "Bridgewater Associates", "INVESTOR", 0.80),
    ("Bill Ackman", "CEO", "Pershing Square", "INVESTOR", 0.75),
    
    # Media / Analysts
    ("Jim Cramer", "Host", "CNBC Mad Money", "MEDIA", 0.70),
    ("Mohamed El-Erian", "Chief Economist", "Allianz", "ANALYST", 0.75),
    
    # Crypto / Tech Influencers
    ("Justin Sun", "Founder", "TRON", "CRYPTO", 0.80),
    
    # Politicians
    ("Donald Trump", "President", "United States", "POLITICIAN", 0.90),
    ("Elizabeth Warren", "Senator", "US Senate", "POLITICIAN", 0.70),
]


def run():
    """Seed the influential_figures table."""
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    
    conn = get_connection()
    try:
        inserted = 0
        
        for name, title, org, category, score in FIGURES:
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO influential_figures (
                        name, title, organization, category, influence_score
                    ) VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (name) DO NOTHING
                """, (name, title, org, category, score))
                
                if cursor.rowcount > 0:
                    inserted += 1
                    logger.info(f"✓ {name} ({category}) - {org}")
        
        conn.commit()
        logger.info(f"Seeded {inserted} new figures ({len(FIGURES)} total)")
    
    finally:
        conn.close()


if __name__ == '__main__':
    run()
