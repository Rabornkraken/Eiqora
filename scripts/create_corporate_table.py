import logging
from data_collection.db.connection import get_connection

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS corporate_action (
    action_id BIGSERIAL PRIMARY KEY,
    cik VARCHAR(10),
    ticker TEXT,
    action_type TEXT,
    ex_date DATE,
    pay_date DATE,
    ratio NUMERIC,
    cash_amount NUMERIC,
    currency TEXT,
    source TEXT,
    source_ref TEXT
);
"""

def main():
    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                logger.info("Creating corporate_action table...")
                cursor.execute(CREATE_TABLE_SQL)
                logger.info("Table created successfully.")
            conn.commit()
    except Exception as e:
        logger.error(f"Error creating table: {e}")

if __name__ == "__main__":
    main()
