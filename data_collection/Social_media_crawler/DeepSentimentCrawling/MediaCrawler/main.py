# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。


import asyncio
import sys
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

import cmd_arg
import config
import db
from base.base_crawler import AbstractCrawler
from cache.local_cache import shutdown_all_local_caches
from media_platform.bilibili import BilibiliCrawler
from media_platform.douyin import DouYinCrawler
from media_platform.kuaishou import KuaishouCrawler
from media_platform.tieba import TieBaCrawler
from media_platform.weibo import WeiboCrawler
from media_platform.xhs import XiaoHongShuCrawler
from media_platform.zhihu import ZhihuCrawler
from media_platform.xueqiu import XueqiuCrawler
from media_platform.reddit import RedditCrawler
from media_platform.youtube import YouTubeCrawler


class CrawlerFactory:
    CRAWLERS = {
        "xhs": XiaoHongShuCrawler,
        "dy": DouYinCrawler,
        "ks": KuaishouCrawler,
        "bili": BilibiliCrawler,
        "wb": WeiboCrawler,
        "tieba": TieBaCrawler,
        "zhihu": ZhihuCrawler,
        "xueqiu": XueqiuCrawler,
        "reddit": RedditCrawler,
        "yt": YouTubeCrawler,
    }

    @staticmethod
    def create_crawler(platform: str) -> AbstractCrawler:
        crawler_class = CrawlerFactory.CRAWLERS.get(platform)
        if not crawler_class:
            raise ValueError(
                "Invalid Media Platform Currently only supported xhs or dy or ks or bili ..."
            )
        return crawler_class()


crawler: Optional[AbstractCrawler] = None


async def main():
    # Init crawler
    global crawler

    # parse cmd
    await cmd_arg.parse_cmd()

    # init db
    if config.SAVE_DATA_OPTION in ["db", "sqlite"]:
        await db.init_db()

    crawler = CrawlerFactory.create_crawler(platform=config.PLATFORM)
    await crawler.start()


async def async_cleanup() -> None:
    if crawler and hasattr(crawler, "close"):
        try:
            await crawler.close()
        except Exception:
            pass
    if config.SAVE_DATA_OPTION in ["db", "sqlite"]:
        try:
            await db.close()
        except Exception:
            pass
    try:
        await shutdown_all_local_caches()
    except Exception:
        pass


if __name__ == "__main__":
    async def _entry():
        try:
            await main()
        finally:
            await async_cleanup()

    asyncio.run(_entry())
