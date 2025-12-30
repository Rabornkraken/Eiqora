# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。

from typing import Dict, List, Union

from async_db import AsyncMysqlDB
from async_sqlite_db import AsyncSqliteDB
from var import media_crawler_db_var


async def query_video_by_video_id(video_id: str) -> Dict:
    async_db_conn: Union[AsyncMysqlDB, AsyncSqliteDB] = media_crawler_db_var.get()
    sql = f"select * from youtube_video where video_id = '{video_id}'"
    rows: List[Dict] = await async_db_conn.query(sql)
    if rows:
        return rows[0]
    return {}


async def add_new_video(video_item: Dict) -> int:
    async_db_conn: Union[AsyncMysqlDB, AsyncSqliteDB] = media_crawler_db_var.get()
    return await async_db_conn.item_to_table("youtube_video", video_item)


async def update_video_by_video_id(video_id: str, video_item: Dict) -> int:
    async_db_conn: Union[AsyncMysqlDB, AsyncSqliteDB] = media_crawler_db_var.get()
    return await async_db_conn.update_table("youtube_video", video_item, "video_id", video_id)


async def get_existing_video_ids(video_ids: List[str]) -> List[str]:
    """
    Check which video_ids already exist in the database
    """
    if not video_ids:
        return []

    async_db_conn = media_crawler_db_var.get()

    # Handle parameter placeholder
    placeholder = "%s" if isinstance(async_db_conn, AsyncMysqlDB) else "?"
    placeholders = ",".join([placeholder] * len(video_ids))

    sql = f"SELECT video_id FROM youtube_video WHERE video_id IN ({placeholders})"

    # Pass video_ids as positional arguments to query
    rows = await async_db_conn.query(sql, *video_ids)

    return [row['video_id'] for row in rows]
