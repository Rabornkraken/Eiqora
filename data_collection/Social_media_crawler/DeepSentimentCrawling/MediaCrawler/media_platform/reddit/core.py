import asyncio
from typing import Dict, List, Optional

from playwright.async_api import BrowserContext, BrowserType

import config
from base.base_crawler import AbstractCrawler
from store import reddit as reddit_store
from tools import utils
from var import crawler_type_var

from .client import RedditClient

class RedditCrawler(AbstractCrawler):
    def __init__(self):
        self.client = RedditClient()
        self.total_posts = 0
        self.total_comments = 0

    async def start(self):
        await self.client.init_client()
        
        crawler_type_var.set(config.CRAWLER_TYPE)
        if config.CRAWLER_TYPE == "search":
            await self.search()
        
        await self.client.close()
        utils.logger.info("[RedditCrawler] Crawler finished ...")
        print(f"[Summary] Notes: {self.total_posts}, Comments: {self.total_comments}")

    async def search(self):
        utils.logger.info("[RedditCrawler.search] Begin search")
        
        for keyword in config.KEYWORDS.split(","):
            utils.logger.info(f"[RedditCrawler] Searching: {keyword}")
            
            # 1. Search Posts
            posts = await self.client.search_posts(keyword, limit=config.CRAWLER_MAX_NOTES_COUNT)
            
            if not posts:
                utils.logger.info(f"[RedditCrawler] No posts found for {keyword}")
                continue
                
            for post in posts:
                # Need to load attributes if lazy
                # AsyncPRAW usually loads basic attributes on search
                
                # 2. Prepare Post Data
                post_item = {
                    "post_id": post.id,
                    "subreddit": post.subreddit.display_name,
                    "title": post.title,
                    "selftext": post.selftext,
                    "author": str(post.author) if post.author else "[deleted]",
                    "score": post.score,
                    "upvote_ratio": post.upvote_ratio,
                    "num_comments": post.num_comments,
                    "created_utc": int(post.created_utc),
                    "url": post.url,
                    "create_time": utils.get_current_timestamp(),
                    "last_modify_ts": utils.get_current_timestamp(),
                    "source_keyword": keyword
                }
                
                utils.logger.info(f"[RedditCrawler] Found post: {post.id} - {post.title[:30]}...")
                
                # 3. Store Post
                await reddit_store.update_reddit_post(post_item)
                self.total_posts += 1
                
                # 4. Get Comments
                if config.ENABLE_GET_COMMENTS:
                    comments = await self.client.get_post_comments(post.id, limit=config.CRAWLER_MAX_COMMENTS_COUNT_SINGLENOTES)
                    for comment in comments:
                        comment_item = {
                            "comment_id": comment.id,
                            "post_id": post.id,
                            "parent_id": comment.parent_id,
                            "author": str(comment.author) if comment.author else "[deleted]",
                            "body": comment.body,
                            "score": comment.score,
                            "created_utc": int(comment.created_utc),
                            "create_time": utils.get_current_timestamp(),
                            "last_modify_ts": utils.get_current_timestamp()
                        }
                        await reddit_store.update_reddit_comment(comment_item)
                        self.total_comments += 1

    async def launch_browser(self, chromium: BrowserType, playwright_proxy: Optional[Dict], user_agent: Optional[str], headless: bool = True) -> BrowserContext:
        return None # Not used
