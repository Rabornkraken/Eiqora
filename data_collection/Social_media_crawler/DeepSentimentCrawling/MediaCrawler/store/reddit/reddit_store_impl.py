import asyncio
from typing import Dict

import config
from base.base_crawler import AbstractStore
from db import db_conn_pool_var
from tools import utils

class RedditStore(AbstractStore):
    async def store_content(self, content_item: Dict):
        """
        Store reddit post content
        """
        pool = db_conn_pool_var.get()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                insert_sql = """
                    INSERT INTO reddit_post(
                        post_id, subreddit, title, selftext, author, score, upvote_ratio,
                        num_comments, created_utc, url, create_time, last_modify_ts,
                        source_keyword
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE 
                        score=VALUES(score), 
                        num_comments=VALUES(num_comments),
                        last_modify_ts=VALUES(last_modify_ts)
                """
                
                params = (
                    content_item.get("post_id"),
                    content_item.get("subreddit"),
                    content_item.get("title"),
                    content_item.get("selftext"),
                    content_item.get("author"),
                    content_item.get("score"),
                    content_item.get("upvote_ratio"),
                    content_item.get("num_comments"),
                    content_item.get("created_utc"),
                    content_item.get("url"),
                    content_item.get("create_time"),
                    content_item.get("last_modify_ts"),
                    content_item.get("source_keyword")
                )
                
                await cursor.execute(insert_sql, params)
                await conn.commit()
                utils.logger.info(f"[RedditStore] Insert post: {content_item.get('post_id')}")

    async def store_comment(self, comment_item: Dict):
        """
        Store reddit comment
        """
        pool = db_conn_pool_var.get()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                insert_sql = """
                    INSERT INTO reddit_comment(
                        comment_id, post_id, parent_id, author, body, score, 
                        created_utc, create_time, last_modify_ts
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE 
                        score=VALUES(score), 
                        last_modify_ts=VALUES(last_modify_ts)
                """
                
                params = (
                    comment_item.get("comment_id"),
                    comment_item.get("post_id"),
                    comment_item.get("parent_id"),
                    comment_item.get("author"),
                    comment_item.get("body"),
                    comment_item.get("score"),
                    comment_item.get("created_utc"),
                    comment_item.get("create_time"),
                    comment_item.get("last_modify_ts")
                )
                
                await cursor.execute(insert_sql, params)
                await conn.commit()
                utils.logger.info(f"[RedditStore] Insert comment: {comment_item.get('comment_id')}")

    async def store_creator(self, creator: Dict):
        pass

async def update_reddit_post(post_item: Dict):
    store = RedditStore()
    await store.store_content(post_item)

async def update_reddit_comment(comment_item: Dict):
    store = RedditStore()
    await store.store_comment(comment_item)
