# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。

from typing import Dict

import config
from base.base_crawler import AbstractStore
from tools import utils

from .youtube_store_impl import YouTubeCsvStoreImplement, YouTubeDbStoreImplement, YouTubeSqliteStoreImplement
from .youtube_store_sql import get_existing_video_ids


class YouTubeStoreFactory:
    STORES = {
        "csv": YouTubeCsvStoreImplement,
        "db": YouTubeDbStoreImplement,
        "sqlite": YouTubeSqliteStoreImplement,
    }

    @staticmethod
    def create_store() -> AbstractStore:
        store_class = YouTubeStoreFactory.STORES.get(config.SAVE_DATA_OPTION)
        if not store_class:
            raise ValueError("[YouTubeStoreFactory.create_store] Invalid save option only supported csv or db or sqlite ...")
        return store_class()


async def upsert_youtube_video(video_item: Dict) -> None:
    utils.logger.info(f"[store.youtube.upsert_youtube_video] video_id:{video_item.get('video_id')}, title:{video_item.get('title')}")
    await YouTubeStoreFactory.create_store().store_content(video_item)

