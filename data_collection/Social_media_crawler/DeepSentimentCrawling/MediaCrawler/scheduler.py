
import asyncio
import os
import time
from typing import List

from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

import config
import db
from main import CrawlerFactory
from tools import utils
from cache.local_cache import shutdown_all_local_caches

# 定时任务配置
SLEEP_INTERVAL = int(os.getenv("SCHEDULE_INTERVAL", 3600))  # 默认 1 小时
PLATFORMS_TO_CRAWL = ["dy", "yt"]

async def run_crawler_task(platform: str):
    """运行单个平台的爬取任务"""
    utils.logger.info(f"[{platform}] >>> Starting periodic crawl task")
    
    # 临时备份原始配置（如果以后有需要恢复的话）
    orig_platform = config.PLATFORM
    orig_crawler_type = config.CRAWLER_TYPE
    crawler = None  # Initialize to None so it's accessible in finally
    
    try:
        # 设置平台特定的配置
        config.PLATFORM = platform
        config.CRAWLER_TYPE = "creator"  # 默认使用创作者模式进行定期更新
        
        # 初始化数据库连接
        if config.SAVE_DATA_OPTION in ["db", "sqlite"]:
            utils.logger.info(f"[{platform}] Initializing database connection...")
            await db.init_db()
            
        # 创建并启动爬虫
        utils.logger.info(f"[{platform}] Creating crawler instance...")
        crawler = CrawlerFactory.create_crawler(platform)
        
        utils.logger.info(f"[{platform}] Starting crawl...")
        await crawler.start()
        
        utils.logger.info(f"[{platform}] Crawl task finished successfully")
            
    except Exception as e:
        utils.logger.error(f"[{platform}] Crawl task failed: {e}")
        import traceback
        utils.logger.error(traceback.format_exc())
    finally:
        # 清理爬虫资源（浏览器等）
        if crawler and hasattr(crawler, "close"):
            try:
                utils.logger.info(f"[{platform}] Closing crawler...")
                await crawler.close()
                utils.logger.info(f"[{platform}] Crawler closed successfully")
            except Exception as e:
                utils.logger.warning(f"[{platform}] Error closing crawler: {e}")
        
        # 关闭数据库连接
        if config.SAVE_DATA_OPTION in ["db", "sqlite"]:
            try:
                await db.close()
                utils.logger.info(f"[{platform}] Database connection closed")
            except Exception as e:
                utils.logger.warning(f"[{platform}] Error closing database: {e}")
        
        # 清理缓存
        try:
            await shutdown_all_local_caches()
        except:
            pass
            
        # 恢复基础配置
        config.PLATFORM = orig_platform
        config.CRAWLER_TYPE = orig_crawler_type
        
        utils.logger.info(f"[{platform}] <<< Finished periodic crawl task")

async def main():
    """主循环"""
    utils.logger.info("==================================================")
    utils.logger.info("  MediaCrawler Periodic Scheduler Server Started  ")
    utils.logger.info(f"  Interval: {SLEEP_INTERVAL} seconds")
    utils.logger.info(f"  Platforms: {PLATFORMS_TO_CRAWL}")
    utils.logger.info("==================================================")
    
    while True:
        start_time = time.time()
        
        # 依次运行各个平台的任务
        for platform in PLATFORMS_TO_CRAWL:
            try:
                await run_crawler_task(platform)
            except Exception as e:
                utils.logger.error(f"Critical error in scheduler loop for {platform}: {e}")
            
            # 平台任务之间稍微停顿一下，避免过于密集
            await asyncio.sleep(5)
            
        elapsed = time.time() - start_time
        sleep_time = max(0, SLEEP_INTERVAL - elapsed)
        
        utils.logger.info(f"Full cycle complete in {elapsed:.2f}s. Next cycle in {sleep_time:.2f}s...")
        await asyncio.sleep(sleep_time)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        utils.logger.info("Scheduler stopped by user")
