import asyncio
from typing import Dict, List, Optional
import asyncpraw
from asyncpraw.models import Submission

import config
from base.base_crawler import AbstractApiClient
from tools import utils

class RedditClient(AbstractApiClient):
    def __init__(self):
        self.reddit = None
        
    async def init_client(self):
        """Initialize AsyncPRAW client"""
        if not config.REDDIT_CLIENT_ID or config.REDDIT_CLIENT_ID == "YOUR_CLIENT_ID":
            utils.logger.error("[RedditClient] Missing API Credentials! Please set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET in config/reddit_config.py")
            raise ValueError("Missing Reddit API Credentials")
            
        self.reddit = asyncpraw.Reddit(
            client_id=config.REDDIT_CLIENT_ID,
            client_secret=config.REDDIT_CLIENT_SECRET,
            user_agent=config.REDDIT_USER_AGENT,
            username=config.REDDIT_USERNAME or None,
            password=config.REDDIT_PASSWORD or None
        )
        # Verify read-only mode
        utils.logger.info(f"[RedditClient] Initialized (Read-Only: {self.reddit.read_only})")

    async def close(self):
        if self.reddit:
            await self.reddit.close()

    async def request(self, method, url, **kwargs):
        pass # Not used directly

    async def update_cookies(self, browser_context):
        pass

    async def search_posts(self, keyword: str, limit: int = 10) -> List[Submission]:
        """
        Search for posts on Reddit
        """
        if not self.reddit:
            await self.init_client()
            
        utils.logger.info(f"[RedditClient] Searching for: {keyword}")
        posts = []
        try:
            # search_type='link' means posts
            async for post in self.reddit.subreddit("all").search(keyword, limit=limit, sort="relevance"):
                posts.append(post)
        except Exception as e:
            utils.logger.error(f"[RedditClient] Search error: {e}")
            
        return posts

    async def get_post_comments(self, post_id: str, limit: int = 20):
        """
        Get comments for a post
        """
        if not self.reddit:
            await self.init_client()
            
        try:
            submission = await self.reddit.submission(post_id)
            # Fetch comments
            # replace_more(limit=0) removes "load more comments" buttons to flatten
            await submission.load()
            await submission.comments.replace_more(limit=0)
            return submission.comments.list()[:limit]
        except Exception as e:
            utils.logger.error(f"[RedditClient] Get comments error: {e}")
            return []
