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
import csv
import os
import pathlib
from typing import Dict

from base.base_crawler import AbstractStore
from tools import utils


def _calculate_number_of_files(file_store_path: str) -> int:
    if not os.path.exists(file_store_path):
        return 1
    try:
        return max([int(file_name.split("_")[0]) for file_name in os.listdir(file_store_path)]) + 1
    except ValueError:
        return 1


class YouTubeCsvStoreImplement(AbstractStore):
    csv_store_path: str = "data/youtube"
    file_count: int = _calculate_number_of_files(csv_store_path)

    def make_save_file_name(self, store_type: str) -> str:
        return f"{self.csv_store_path}/{self.file_count}_youtube_{store_type}_{utils.get_current_date()}.csv"

    def _save_data_to_csv_sync(self, save_item: Dict, store_type: str) -> None:
        pathlib.Path(self.csv_store_path).mkdir(parents=True, exist_ok=True)
        save_file_name = self.make_save_file_name(store_type=store_type)
        with open(save_file_name, mode="a+", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            if f.tell() == 0:
                writer.writerow(save_item.keys())
            writer.writerow(save_item.values())

    async def _save_data_to_csv(self, save_item: Dict, store_type: str) -> None:
        await asyncio.to_thread(self._save_data_to_csv_sync, save_item, store_type)

    async def store_content(self, content_item: Dict):
        await self._save_data_to_csv(save_item=content_item, store_type="videos")

    async def store_comment(self, comment_item: Dict):
        await self._save_data_to_csv(save_item=comment_item, store_type="comments")

    async def store_creator(self, creator: Dict):
        await self._save_data_to_csv(save_item=creator, store_type="creator")


class YouTubeDbStoreImplement(AbstractStore):
    async def store_content(self, content_item: Dict):
        from .youtube_store_sql import add_new_video, query_video_by_video_id, update_video_by_video_id

        video_id = content_item.get("video_id")
        if not video_id:
            return
        video_detail = await query_video_by_video_id(video_id)
        if not video_detail:
            content_item["add_ts"] = utils.get_current_timestamp()
            await add_new_video(content_item)
        else:
            await update_video_by_video_id(video_id, content_item)

    async def store_comment(self, comment_item: Dict):
        # TODO: implement if needed
        return

    async def store_creator(self, creator: Dict):
        # TODO: implement if needed
        return


class YouTubeSqliteStoreImplement(YouTubeDbStoreImplement):
    pass
