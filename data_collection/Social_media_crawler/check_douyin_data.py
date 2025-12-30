import asyncio
import sys
from pathlib import Path
from datetime import datetime

# Add project root to sys.path
project_root = Path(__file__).parent
sys.path.append(str(project_root))
sys.path.append(str(project_root / "DeepSentimentCrawling/MediaCrawler"))

from DeepSentimentCrawling.MediaCrawler.db import db_conn_pool_var
from DeepSentimentCrawling.MediaCrawler.async_db import AsyncMysqlDB
import aiomysql
import config

async def main():
    print("Connecting to database...")
    
    # Manually init pool since we are outside the crawler flow
    pool = await aiomysql.create_pool(
        host=config.DB_HOST,
        port=config.DB_PORT,
        user=config.DB_USER,
        password=config.DB_PASSWORD,
        db=config.DB_NAME,
        autocommit=True
    )
    
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            # Query latest 5 videos
            sql = """
                SELECT aweme_id, title, create_time, add_ts, transcription 
                FROM douyin_aweme 
                ORDER BY create_time DESC 
                LIMIT 5
            """
            await cur.execute(sql)
            rows = await cur.fetchall()
            
            print(f"\n{'='*80}")
            print(f"{ 'Title':<30} | {'Uploaded':<20} | {'Crawled':<20} | {'Transcript?'}")
            print(f"{'-'*80}")
            
            for row in rows:
                title = row[1] or "No Title"
                title = (title[:27] + '...') if len(title) > 27 else title
                
                # Convert timestamps (handle milliseconds if > 10000000000)
                ts_create = row[2]
                if ts_create > 10000000000: ts_create /= 1000
                upload_dt = datetime.fromtimestamp(ts_create).strftime('%Y-%m-%d %H:%M:%S')
                
                ts_add = row[3]
                if ts_add > 10000000000: ts_add /= 1000
                crawl_dt = datetime.fromtimestamp(ts_add).strftime('%Y-%m-%d %H:%M:%S')
                
                has_transcript = "Yes" if row[4] else "No"
                
                print(f"{title:<30} | {upload_dt:<20} | {crawl_dt:<20} | {has_transcript}")
            
            print(f"{ '='*80}\n")

    pool.close()
    await pool.wait_closed()

if __name__ == "__main__":
    asyncio.run(main())
