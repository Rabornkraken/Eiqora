import asyncio
from typing import Dict

import config
from base.base_crawler import AbstractStore
from db import db_conn_pool_var
from tools import utils

class XueqiuStore(AbstractStore):
    async def store_content(self, content_item: Dict):
        """
        Store xueqiu note content
        """
        pool = db_conn_pool_var.get()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                insert_sql = """
                    INSERT INTO xueqiu_note(
                        note_id, user_id, nickname, title, `desc`, liked_count, collected_count, 
                        comment_count, share_count, posted_time, url, create_time, last_modify_ts
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE 
                        liked_count=VALUES(liked_count), 
                        collected_count=VALUES(collected_count), 
                        comment_count=VALUES(comment_count), 
                        share_count=VALUES(share_count), 
                        last_modify_ts=VALUES(last_modify_ts)
                """
                
                # Parse fields
                note_id = str(content_item.get("id"))
                user_info = content_item.get("user", {})
                user_id = str(user_info.get("id", ""))
                nickname = user_info.get("screen_name", "")
                title = content_item.get("title", "")
                desc = content_item.get("text", "")
                liked_count = str(content_item.get("like_count", 0))
                collected_count = str(content_item.get("fav_count", 0))
                comment_count = str(content_item.get("comment_count", 0))
                share_count = str(content_item.get("retweet_count", 0))
                posted_time = str(content_item.get("created_at", ""))
                url = f"https://xueqiu.com/{user_id}/{note_id}" if user_id else f"https://xueqiu.com/s/{note_id}"
                create_time = utils.get_current_timestamp()
                
                params = (
                    note_id, user_id, nickname, title, desc, liked_count, collected_count,
                    comment_count, share_count, posted_time, url, create_time, create_time
                )
                
                await cursor.execute(insert_sql, params)
                await conn.commit()
                utils.logger.info(f"[XueqiuStore.store_content] Insert note: {note_id}")

    async def store_comment(self, comment_item: Dict):
        """
        Store xueqiu note comment
        """
        pool = db_conn_pool_var.get()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                insert_sql = """
                    INSERT INTO xueqiu_note_comment(
                        comment_id, note_id, user_id, nickname, content, liked_count, 
                        create_time, last_modify_ts
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE 
                        liked_count=VALUES(liked_count), 
                        last_modify_ts=VALUES(last_modify_ts)
                """
                
                comment_id = str(comment_item.get("id"))
                # note_id is passed via context or need to be extracted. 
                # Xueqiu comment object usually contains status_id (note_id)
                note_id = str(comment_item.get("status_id", "")) 
                
                user_info = comment_item.get("user", {})
                user_id = str(user_info.get("id", ""))
                nickname = user_info.get("screen_name", "")
                content = comment_item.get("text", "")
                liked_count = str(comment_item.get("like_count", 0))
                create_time = utils.get_current_timestamp()
                
                params = (
                    comment_id, note_id, user_id, nickname, content, liked_count,
                    create_time, create_time
                )
                
                await cursor.execute(insert_sql, params)
                await conn.commit()
                utils.logger.info(f"[XueqiuStore.store_comment] Insert comment: {comment_id}")

    async def store_creator(self, creator: Dict):
        pass

async def update_xueqiu_note(note_item: Dict):
    store = XueqiuStore()
    await store.store_content(note_item)

async def batch_update_xueqiu_note_comments(note_id: str, comments: list):
    if not comments:
        return
    store = XueqiuStore()
    for comment in comments:
        # Ensure note_id is present
        if "status_id" not in comment:
            comment["status_id"] = note_id
        await store.store_comment(comment)
