import asyncio
import os
import random
from typing import Dict, List, Optional

from playwright.async_api import BrowserContext, BrowserType, Page, async_playwright
from playwright_stealth import Stealth

import config
from base.base_crawler import AbstractCrawler
from proxy.proxy_ip_pool import create_ip_pool, IpInfoModel
from store import xueqiu as xueqiu_store
from tools import utils
from tools.cdp_browser import CDPBrowserManager
from var import crawler_type_var

from .client import XueqiuClient
from .login import XueqiuLogin
from .exception import DataFetchError

class XueqiuCrawler(AbstractCrawler):
    context_page: Page
    xq_client: XueqiuClient
    browser_context: BrowserContext
    
    # Statistics
    total_notes_crawled = 0
    total_comments_crawled = 0

    def __init__(self):
        self.index_url = "https://xueqiu.com"
        self.user_agent = utils.get_user_agent()

    async def start(self):
        playwright_proxy, httpx_proxy = None, None
        if config.ENABLE_IP_PROXY:
            ip_proxy_pool = await create_ip_pool(config.IP_PROXY_POOL_COUNT, enable_validate_ip=True)
            ip_proxy_info = await ip_proxy_pool.get_proxy()
            playwright_proxy, httpx_proxy = utils.format_proxy_info(ip_proxy_info)

        async with async_playwright() as playwright:
            # Launch browser
            chromium = playwright.chromium
            self.browser_context = await self.launch_browser(
                chromium, 
                None, 
                self.user_agent, 
                headless=config.HEADLESS
            )
            
            self.context_page = await self.browser_context.new_page()
            stealth = Stealth()
            await stealth.apply_stealth_async(self.context_page)
            await self.context_page.goto(self.index_url)

            # Init Client
            self.xq_client = await self.create_xueqiu_client(httpx_proxy)
            
            # Login check
            login_obj = XueqiuLogin(
                login_type=config.LOGIN_TYPE,
                browser_context=self.browser_context,
                context_page=self.context_page,
                cookie_str=config.COOKIES
            )
            await login_obj.begin()
            
            # Update cookies after login
            await self.xq_client.update_cookies(self.browser_context)

            # Start Crawling
            crawler_type_var.set(config.CRAWLER_TYPE)
            if config.CRAWLER_TYPE == "search":
                await self.search()
            
            utils.logger.info("[XueqiuCrawler] Crawler finished ...")
            
            # Print standardized summary for parent process to parse
            print(f"[Summary] Notes: {self.total_notes_crawled}, Comments: {self.total_comments_crawled}")

    async def search(self):
        utils.logger.info("[XueqiuCrawler.search] Begin search keywords")
        for keyword in config.KEYWORDS.split(","):
            utils.logger.info(f"[XueqiuCrawler.search] Current keyword: {keyword}")
            try:
                # Page 1
                search_res = await self.xq_client.get_note_by_keyword(keyword, page=1)
                
                # Check for list
                notes = search_res.get("list", [])
                if not notes:
                    utils.logger.info(f"[XueqiuCrawler.search] No results for {keyword}")
                    continue
                
                for note in notes:
                    note_id = str(note.get("id"))
                    utils.logger.info(f"[XueqiuCrawler] Found note: {note_id} - {note.get('title', 'No Title')}")
                    
                    # Store note
                    await xueqiu_store.update_xueqiu_note(note)
                    self.total_notes_crawled += 1
                    
                    # Get comments
                    if config.ENABLE_GET_COMMENTS:
                        await self.get_note_comments(note_id)
                        
            except Exception as e:
                utils.logger.error(f"[XueqiuCrawler.search] Error: {e}")

    async def get_note_comments(self, note_id: str):
        try:
            comments_res = await self.xq_client.get_note_comments(note_id)
            comments = comments_res.get("comments", [])
            utils.logger.info(f"[XueqiuCrawler] Found {len(comments)} comments for note {note_id}")
            
            # Store comments
            await xueqiu_store.batch_update_xueqiu_note_comments(note_id, comments)
            self.total_comments_crawled += len(comments)
            
        except Exception as e:
            utils.logger.error(f"[XueqiuCrawler] Get comments error: {e}")

    async def create_xueqiu_client(self, httpx_proxy: Optional[str]) -> XueqiuClient:
        cookie_str, cookie_dict = utils.convert_cookies(await self.browser_context.cookies())
        return XueqiuClient(
            proxy=httpx_proxy,
            headers={
                "User-Agent": self.user_agent,
                "Cookie": cookie_str,
                "Origin": self.index_url,
                "Referer": self.index_url
            },
            playwright_page=self.context_page,
            cookie_dict=cookie_dict,
        )

    async def launch_browser(self, chromium, proxy, user_agent, headless=True):
        if config.SAVE_LOGIN_STATE:
            user_data_dir = os.path.join(os.getcwd(), "browser_data", config.USER_DATA_DIR % "xueqiu")
            return await chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                accept_downloads=True,
                headless=headless,
                proxy=proxy,
                user_agent=user_agent,
                viewport={"width": 1920, "height": 1080}
            )
        else:
            browser = await chromium.launch(headless=headless, proxy=proxy)
            return await browser.new_context(user_agent=user_agent)