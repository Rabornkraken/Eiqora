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
import os
import random
import re
import time
from asyncio import Task
from typing import Any, Dict, List, Optional, Tuple

import httpx
from playwright.async_api import (
    BrowserContext,
    BrowserType,
    Page,
    Playwright,
    async_playwright,
)
from playwright_stealth import Stealth

import config
from base.base_crawler import AbstractCrawler
from proxy.proxy_ip_pool import IpInfoModel, create_ip_pool
from store import douyin as douyin_store
from tools import utils
from tools.cdp_browser import CDPBrowserManager
from var import crawler_type_var, source_keyword_var

from .client import DouYinClient
from .exception import DataFetchError
from .field import PublishTimeType
from .login import DouYinLogin


class DouYinCrawler(AbstractCrawler):
    context_page: Page
    dy_client: DouYinClient
    browser_context: BrowserContext
    cdp_manager: Optional[CDPBrowserManager]

    def __init__(self) -> None:
        self.index_url = "https://www.douyin.com"
        self.cdp_manager = None

    async def start(self) -> None:
        playwright_proxy_format, httpx_proxy_format = None, None
        if config.ENABLE_IP_PROXY:
            ip_proxy_pool = await create_ip_pool(config.IP_PROXY_POOL_COUNT, enable_validate_ip=True)
            ip_proxy_info: IpInfoModel = await ip_proxy_pool.get_proxy()
            playwright_proxy_format, httpx_proxy_format = utils.format_proxy_info(ip_proxy_info)

        async with async_playwright() as playwright:
            # 根据配置选择启动模式
            if config.ENABLE_CDP_MODE:
                utils.logger.info("[DouYinCrawler] 使用CDP模式启动浏览器")
                self.browser_context = await self.launch_browser_with_cdp(
                    playwright,
                    playwright_proxy_format,
                    None,
                    headless=config.CDP_HEADLESS,
                )
            else:
                utils.logger.info("[DouYinCrawler] 使用标准模式启动浏览器")
                # Launch a browser context.
                chromium = playwright.chromium
                self.browser_context = await self.launch_browser(
                    chromium,
                    playwright_proxy_format,
                    user_agent=None,
                    headless=config.HEADLESS,
                )
            # stealth.min.js is a js script to prevent the website from detecting the crawler.
            # await self.browser_context.add_init_script(path="libs/stealth.min.js")
            stealth = Stealth()
            await stealth.apply_stealth_async(self.browser_context)
            self.context_page = await self.browser_context.new_page()
            await self.context_page.goto(self.index_url, timeout=120000)

            self.dy_client = await self.create_douyin_client(httpx_proxy_format)
            if not await self.dy_client.pong(browser_context=self.browser_context):
                login_obj = DouYinLogin(
                    login_type=config.LOGIN_TYPE,
                    login_phone="",  # you phone number
                    browser_context=self.browser_context,
                    context_page=self.context_page,
                    cookie_str=config.COOKIES,
                )
                await login_obj.begin()
                await self.dy_client.update_cookies(browser_context=self.browser_context)
            crawler_type_var.set(config.CRAWLER_TYPE)
            if config.CRAWLER_TYPE == "search":
                # Search for notes and retrieve their comment information.
                await self.search()
            elif config.CRAWLER_TYPE == "detail":
                # Get the information and comments of the specified post
                await self.get_specified_awemes()
            elif config.CRAWLER_TYPE == "creator":
                # Get the information and comments of the specified creator
                await self.get_creators_and_videos()

            utils.logger.info("[DouYinCrawler.start] Douyin Crawler finished ...")

    async def search(self) -> None:
        utils.logger.info("[DouYinCrawler.search] Begin search douyin keywords")
        dy_limit_count = 10  # douyin limit page fixed value
        if config.CRAWLER_MAX_NOTES_COUNT < dy_limit_count:
            config.CRAWLER_MAX_NOTES_COUNT = dy_limit_count
        start_page = config.START_PAGE  # start page number
        for keyword in config.KEYWORDS.split(","):
            source_keyword_var.set(keyword)
            utils.logger.info(f"[DouYinCrawler.search] Current keyword: {keyword}")
            aweme_list: List[str] = []
            page = 0
            dy_search_id = ""
            while (page - start_page + 1) * dy_limit_count <= config.CRAWLER_MAX_NOTES_COUNT:
                if page < start_page:
                    utils.logger.info(f"[DouYinCrawler.search] Skip {page}")
                    page += 1
                    continue
                try:
                    utils.logger.info(f"[DouYinCrawler.search] search douyin keyword: {keyword}, page: {page}")
                    posts_res = await self.dy_client.search_info_by_keyword(
                        keyword=keyword,
                        offset=page * dy_limit_count - dy_limit_count,
                        publish_time=PublishTimeType(config.PUBLISH_TIME_TYPE),
                        search_id=dy_search_id,
                    )
                    if posts_res.get("data") is None or posts_res.get("data") == []:
                        utils.logger.info(f"[DouYinCrawler.search] search douyin keyword: {keyword}, page: {page} is empty. Response: {posts_res}")
                        break
                except DataFetchError:
                    utils.logger.error(f"[DouYinCrawler.search] search douyin keyword: {keyword} failed")
                    break

                page += 1
                if "data" not in posts_res:
                    utils.logger.error(f"[DouYinCrawler.search] search douyin keyword: {keyword} failed，账号也许被风控了。")
                    break
                dy_search_id = posts_res.get("extra", {}).get("logid", "")
                for post_item in posts_res.get("data"):
                    try:
                        aweme_info: Dict = (post_item.get("aweme_info") or post_item.get("aweme_mix_info", {}).get("mix_items")[0])
                    except TypeError:
                        continue
                    aweme_list.append(aweme_info.get("aweme_id", ""))
                    await douyin_store.update_douyin_aweme(aweme_item=aweme_info)
                    await self.get_aweme_media(aweme_item=aweme_info)
            utils.logger.info(f"[DouYinCrawler.search] keyword:{keyword}, aweme_list:{aweme_list}")
            await self.batch_get_note_comments(aweme_list)

    async def get_specified_awemes(self):
        """Get the information and comments of the specified post"""
        semaphore = asyncio.Semaphore(config.MAX_CONCURRENCY_NUM)
        task_list = [self.get_aweme_detail(aweme_id=aweme_id, semaphore=semaphore) for aweme_id in config.DY_SPECIFIED_ID_LIST]
        aweme_details = await asyncio.gather(*task_list)
        for aweme_detail in aweme_details:
            if aweme_detail is not None:
                await douyin_store.update_douyin_aweme(aweme_item=aweme_detail)
                await self.get_aweme_media(aweme_item=aweme_detail)
        await self.batch_get_note_comments(config.DY_SPECIFIED_ID_LIST)

    async def get_aweme_detail(self, aweme_id: str, semaphore: asyncio.Semaphore) -> Any:
        """Get note detail"""
        async with semaphore:
            try:
                return await self.dy_client.get_video_by_id(aweme_id)
            except DataFetchError as ex:
                utils.logger.error(f"[DouYinCrawler.get_aweme_detail] Get aweme detail error: {ex}")
                return None
            except KeyError as ex:
                utils.logger.error(f"[DouYinCrawler.get_aweme_detail] have not fund note detail aweme_id:{aweme_id}, err: {ex}")
                return None

    async def batch_get_note_comments(self, aweme_list: List[str]) -> None:
        """
        Batch get note comments
        """
        if not config.ENABLE_GET_COMMENTS:
            utils.logger.info(f"[DouYinCrawler.batch_get_note_comments] Crawling comment mode is not enabled")
            return

        task_list: List[Task] = []
        semaphore = asyncio.Semaphore(config.MAX_CONCURRENCY_NUM)
        for aweme_id in aweme_list:
            task = asyncio.create_task(self.get_comments(aweme_id, semaphore), name=aweme_id)
            task_list.append(task)
        if len(task_list) > 0:
            done, pending = await asyncio.wait(task_list)
            for task in done:
                try:
                    task.result()
                except Exception as e:
                    utils.logger.error(f"[DouYinCrawler.batch_get_note_comments] task failed: {task.get_name()}, err: {e}")
            for task in pending:
                task.cancel()

    async def get_comments(self, aweme_id: str, semaphore: asyncio.Semaphore) -> None:
        async with semaphore:
            try:
                # 将关键词列表传递给 get_aweme_all_comments 方法
                await self.dy_client.get_aweme_all_comments(
                    aweme_id=aweme_id,
                    crawl_interval=random.random(),
                    is_fetch_sub_comments=config.ENABLE_GET_SUB_COMMENTS,
                    callback=douyin_store.batch_update_dy_aweme_comments,
                    max_count=config.CRAWLER_MAX_COMMENTS_COUNT_SINGLENOTES,
                )
                utils.logger.info(f"[DouYinCrawler.get_comments] aweme_id: {aweme_id} comments have all been obtained and filtered ...")
            except DataFetchError as e:
                utils.logger.error(f"[DouYinCrawler.get_comments] aweme_id: {aweme_id} get comments failed, error: {e}")
            except Exception as e:
                utils.logger.error(f"[DouYinCrawler.get_comments] aweme_id: {aweme_id} unexpected error: {e}")

    async def get_creators_and_videos(self) -> None:
        """
        Get the information and videos of the specified creator
        """
        utils.logger.info("[DouYinCrawler.get_creators_and_videos] Begin get douyin creators")
        for creator in config.DY_CREATOR_ID_LIST:
            sec_user_id = creator
            
            # Support passing creator share/profile URLs directly (e.g. https://v.douyin.com/xxxx/).
            if isinstance(creator, str) and creator.startswith(("http://", "https://")):
                resolved_uid = await self._resolve_url_to_sec_uid(creator)
                if resolved_uid:
                    utils.logger.info(f"[DouYinCrawler] Resolved {creator} to sec_uid: {resolved_uid}")
                    sec_user_id = resolved_uid
                else:
                    utils.logger.warning(f"[DouYinCrawler] Failed to resolve URL to sec_uid: {creator}, falling back to browser extraction.")
                    await self.fetch_one_video_from_creator_page(creator_page_url=creator)
                    continue

            try:
                creator_info: Dict = await self.dy_client.get_user_info(sec_user_id)
                if creator_info:
                    await douyin_store.save_creator(sec_user_id, creator=creator_info)

                # Get latest videos with one-by-one duplicate checking
                max_count = int(config.CRAWLER_MAX_NOTES_COUNT or 10)
                aweme_list: List[Dict] = []
                max_cursor = ""
                stop_crawling = False
                
                while len(aweme_list) < max_count and not stop_crawling:
                    aweme_post_res = await self.dy_client.get_user_aweme_posts(sec_user_id, max_cursor=max_cursor)
                    batch = aweme_post_res.get("aweme_list", [])
                    if not batch:
                        break
                    
                    # Check duplicates one by one for immediate early stopping
                    for video in batch:
                        aweme_id = video.get('aweme_id')
                        if not aweme_id:
                            continue
                        
                        # Check if this single video exists in DB
                        existing = await douyin_store.get_existing_aweme_ids([aweme_id])
                        
                        if existing:
                            # Found a duplicate! Stop immediately
                            utils.logger.info(f"[DouYinCrawler] Encountered existing video {aweme_id} in DB. Stopping for creator {sec_user_id}")
                            stop_crawling = True
                            break
                        
                        # New video, add to list
                        aweme_list.append(video)
                        utils.logger.info(f"[DouYinCrawler] Found new video {aweme_id} ({len(aweme_list)}/{max_count})")
                        
                        if len(aweme_list) >= max_count:
                            stop_crawling = True
                            break
                    
                    if stop_crawling:
                        break
                        
                    # Continue to next page if needed
                    if not aweme_post_res.get("has_more"):
                        break
                    max_cursor = aweme_post_res.get("max_cursor", "")
                
                if aweme_list:
                    utils.logger.info(f"[DouYinCrawler] Processing {len(aweme_list)} new videos for creator {sec_user_id}")
                    await self.fetch_creator_video_detail(aweme_list)
                else:
                    utils.logger.info(f"[DouYinCrawler] No new videos found for creator {sec_user_id}")

            except DataFetchError as ex:
                # If the JSON API is blocked/empty, fall back to browser-only extraction for 1 video.
                utils.logger.warning(
                    f"[DouYinCrawler.get_creators_and_videos] API blocked for sec_user_id={sec_user_id}, "
                    f"fallback to browser extraction. err={ex}"
                )
                await self.fetch_one_video_from_creator_page(creator_page_url=f"{self.index_url}/user/{sec_user_id}")

    async def _resolve_url_to_sec_uid(self, url: str) -> Optional[str]:
        """
        Resolve a Douyin short link or profile URL to a sec_user_id.
        """
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(url, follow_redirects=True, headers={"User-Agent": utils.get_mobile_user_agent()})
                long_url = str(res.url)
                
                # Check for user profile sec_uid in URL params
                # Example: ...?sec_uid=MS4wLjABAAAA...
                match = re.search(r'sec_uid=([A-Za-z0-9_-]+)', long_url)
                if match:
                    return match.group(1)
                
                # Check if it's a video link, maybe we can get author ID?
                # Usually video links don't have sec_uid in URL, but the page content does.
                # For now, only support profile links that have sec_uid in URL.
                return None
        except Exception as e:
            utils.logger.error(f"[DouYinCrawler] Error resolving URL {url}: {e}")
            return None

    async def fetch_creator_video_detail(self, video_list: List[Dict]):
        """
        Concurrently obtain the specified post list and save the data
        """
        semaphore = asyncio.Semaphore(config.MAX_CONCURRENCY_NUM)
        task_list = [self.get_aweme_detail(post_item.get("aweme_id"), semaphore) for post_item in video_list]

        note_details = await asyncio.gather(*task_list)
        for aweme_item in note_details:
            if aweme_item is not None:
                await douyin_store.update_douyin_aweme(aweme_item=aweme_item)
                await self.get_aweme_media(aweme_item=aweme_item)

    async def fetch_one_video_from_creator_page(self, creator_page_url: str) -> None:
        """
        Browser-only fallback path:
        - open creator share/profile URL
        - extract one aweme_id
        - open video page and capture a playable mp4 URL
        - store minimal metadata + download video (transcription happens in store layer)
        """
        page = await self.browser_context.new_page()
        try:
            await page.goto(creator_page_url, wait_until="domcontentloaded", timeout=60_000)
            await page.wait_for_timeout(1500)

            aweme_id = self._extract_aweme_id_from_url(page.url) or await self._extract_first_aweme_id_from_page(page)
            if not aweme_id:
                utils.logger.error(f"[DouYinCrawler.fetch_one_video_from_creator_page] No aweme_id found from: {creator_page_url}")
                return

            video_page_url = f"{self.index_url}/video/{aweme_id}"
            video_url = await self._capture_first_video_url_from_video_page(page, video_page_url=video_page_url)
            if not video_url:
                utils.logger.error(f"[DouYinCrawler.fetch_one_video_from_creator_page] No video url captured for aweme_id={aweme_id}")
                return

            desc = await self._safe_extract_desc(page) or f"Douyin video {aweme_id}"
            now_ts = int(time.time())
            minimal_aweme_item: Dict[str, Any] = {
                "aweme_id": aweme_id,
                "aweme_type": 0,
                "desc": desc,
                "create_time": now_ts,
                "author": {},
                "statistics": {},
                "video": {
                    "play_addr": {"url_list": [video_url, video_url]},
                },
            }

            await douyin_store.update_douyin_aweme(aweme_item=minimal_aweme_item)
            await self.get_aweme_media(aweme_item=minimal_aweme_item)
        finally:
            await page.close()

    @staticmethod
    def _extract_aweme_id_from_url(url: str) -> Optional[str]:
        for pattern in (r"/video/(\d+)", r"[?&]modal_id=(\d+)", r"[?&]aweme_id=(\d+)"):
            m = re.search(pattern, url)
            if m:
                return m.group(1)
        return None

    async def _extract_first_aweme_id_from_page(self, page: Page) -> Optional[str]:
        # 1) Try DOM links first (works when the creator grid is rendered).
        try:
            aweme_id = await page.evaluate(
                """
                () => {
                  const anchors = Array.from(document.querySelectorAll('a[href*="/video/"]'));
                  for (const a of anchors) {
                    const href = a.getAttribute('href') || '';
                    const m = href.match(/\\/video\\/(\\d+)/);
                    if (m) return m[1];
                  }
                  return null;
                }
                """
            )
            if aweme_id:
                return str(aweme_id)
        except Exception:
            pass

        # 2) Scroll a bit to trigger rendering / lazy loading, then retry.
        for _ in range(3):
            try:
                await page.evaluate("() => window.scrollBy(0, Math.max(800, window.innerHeight))")
                await page.wait_for_timeout(800)
                aweme_id = await page.evaluate(
                    """
                    () => {
                      const anchors = Array.from(document.querySelectorAll('a[href*="/video/"]'));
                      for (const a of anchors) {
                        const href = a.getAttribute('href') || '';
                        const m = href.match(/\\/video\\/(\\d+)/);
                        if (m) return m[1];
                      }
                      return null;
                    }
                    """
                )
                if aweme_id:
                    return str(aweme_id)
            except Exception:
                break

        # 3) Fallback to regex on HTML.
        try:
            html = await page.content()
            for pattern in (r"/video/(\d+)", r"\"aweme_id\"\\s*:\\s*\"?(\\d+)\"?"):
                m = re.search(pattern, html)
                if m:
                    return m.group(1)
        except Exception:
            pass

        return None

    async def _capture_first_video_url_from_video_page(self, page: Page, video_page_url: str) -> Optional[str]:
        loop = asyncio.get_running_loop()
        captured: asyncio.Future = loop.create_future()

        def on_response(resp):
            if captured.done():
                return
            try:
                ct = (resp.headers.get("content-type") or "").lower()
                url = resp.url
                is_video_ct = ct.startswith("video/") or ("video" in ct and "mp4" in ct)
                looks_like_mp4 = ".mp4" in url
                if is_video_ct or looks_like_mp4:
                    captured.set_result(url)
            except Exception:
                return

        page.on("response", on_response)
        await page.goto(video_page_url, wait_until="domcontentloaded", timeout=120000)

        # Some pages only start requesting media after play; attempt to trigger playback.
        try:
            await page.wait_for_timeout(800)
            await page.click("video", timeout=3_000)
        except Exception:
            pass

        try:
            return await asyncio.wait_for(captured, timeout=30)
        except asyncio.TimeoutError:
            # Try DOM-based extraction as a last resort (when video uses direct src).
            try:
                src = await page.eval_on_selector("video", "el => el.currentSrc || el.src")
                if isinstance(src, str) and src.startswith("http"):
                    return src
            except Exception:
                pass
            return None

    async def _safe_extract_desc(self, page: Page) -> str:
        try:
            title = await page.title()
            if title:
                return title.strip()
        except Exception:
            pass
        try:
            og = await page.eval_on_selector('meta[property="og:title"]', "el => el.content")
            if og:
                return str(og).strip()
        except Exception:
            pass
        return ""

    async def create_douyin_client(self, httpx_proxy: Optional[str]) -> DouYinClient:
        """Create douyin client"""
        cookie_str, cookie_dict = utils.convert_cookies(await self.browser_context.cookies())  # type: ignore
        douyin_client = DouYinClient(
            proxy=httpx_proxy,
            headers={
                "User-Agent": await self.context_page.evaluate("() => navigator.userAgent"),
                "Cookie": cookie_str,
                "Host": "www.douyin.com",
                "Origin": "https://www.douyin.com/",
                "Referer": "https://www.douyin.com/",
                "Content-Type": "application/json;charset=UTF-8",
            },
            playwright_page=self.context_page,
            cookie_dict=cookie_dict,
        )
        return douyin_client

    async def launch_browser(
        self,
        chromium: BrowserType,
        playwright_proxy: Optional[Dict],
        user_agent: Optional[str],
        headless: bool = True,
    ) -> BrowserContext:
        """Launch browser and create browser context"""
        if config.SAVE_LOGIN_STATE:
            # Fix: Use path relative to MediaCrawler root, not CWD
            media_crawler_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            user_data_dir = os.path.join(media_crawler_root, "browser_data", config.USER_DATA_DIR % config.PLATFORM)
            
            browser_context = await chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                accept_downloads=True,
                headless=headless,
                proxy=playwright_proxy,  # type: ignore
                viewport={
                    "width": 1920,
                    "height": 1080
                },
                user_agent=user_agent,
            )  # type: ignore
            return browser_context
        else:
            browser = await chromium.launch(headless=headless, proxy=playwright_proxy)  # type: ignore
            browser_context = await browser.new_context(viewport={"width": 1920, "height": 1080}, user_agent=user_agent)
            return browser_context

    async def launch_browser_with_cdp(
        self,
        playwright: Playwright,
        playwright_proxy: Optional[Dict],
        user_agent: Optional[str],
        headless: bool = True,
    ) -> BrowserContext:
        """
        使用CDP模式启动浏览器
        """
        try:
            self.cdp_manager = CDPBrowserManager()
            browser_context = await self.cdp_manager.launch_and_connect(
                playwright=playwright,
                playwright_proxy=playwright_proxy,
                user_agent=user_agent,
                headless=headless,
            )

            # 添加反检测脚本
            await self.cdp_manager.add_stealth_script()

            # 显示浏览器信息
            browser_info = await self.cdp_manager.get_browser_info()
            utils.logger.info(f"[DouYinCrawler] CDP浏览器信息: {browser_info}")

            return browser_context

        except Exception as e:
            utils.logger.error(f"[DouYinCrawler] CDP模式启动失败，回退到标准模式: {e}")
            # 回退到标准模式
            chromium = playwright.chromium
            return await self.launch_browser(chromium, playwright_proxy, user_agent, headless)

    async def close(self) -> None:
        """Close browser context"""
        # 如果使用CDP模式，需要特殊处理
        if self.cdp_manager:
            await self.cdp_manager.cleanup()
            self.cdp_manager = None
        else:
            await self.browser_context.close()
        utils.logger.info("[DouYinCrawler.close] Browser context closed ...")

    async def get_aweme_media(self, aweme_item: Dict):
        """
        获取抖音媒体，自动判断媒体类型是短视频还是帖子图片并下载

        Args:
            aweme_item (Dict): 抖音作品详情
        """
        if not config.ENABLE_GET_MEIDAS:
            utils.logger.info(f"[DouYinCrawler.get_aweme_media] Crawling image mode is not enabled")
            return
        # 笔记 urls 列表，若为短视频类型则返回为空列表
        note_download_url: List[str] = douyin_store._extract_note_image_list(aweme_item)
        # 视频 url，永远存在，但为短视频类型时的文件其实是音频文件
        video_download_url: str = douyin_store._extract_video_download_url(aweme_item)
        # TODO: 抖音并没采用音视频分离的策略，故音频可从原视频中分离，暂不提取
        if note_download_url:
            await self.get_aweme_images(aweme_item)
        else:
            await self.get_aweme_video(aweme_item)

    async def get_aweme_images(self, aweme_item: Dict):
        """
        get aweme images. please use get_aweme_media
        
        Args:
            aweme_item (Dict): 抖音作品详情
        """
        if not config.ENABLE_GET_MEIDAS:
            return
        aweme_id = aweme_item.get("aweme_id")
        # 笔记 urls 列表，若为短视频类型则返回为空列表
        note_download_url: List[str] = douyin_store._extract_note_image_list(aweme_item)

        if not note_download_url:
            return
        picNum = 0
        for url in note_download_url:
            if not url:
                continue
            content = await self.dy_client.get_aweme_media(url)
            await asyncio.sleep(random.random())
            if content is None:
                continue
            extension_file_name = f"{picNum:>03d}.jpeg"
            picNum += 1
            await douyin_store.update_dy_aweme_image(aweme_id, content, extension_file_name)

    async def get_aweme_video(self, aweme_item: Dict):
        """
        get aweme videos. please use get_aweme_media

        Args:
            aweme_item (Dict): 抖音作品详情
        """
        if not config.ENABLE_GET_MEIDAS:
            return
        aweme_id = aweme_item.get("aweme_id")

        # 视频 url，永远存在，但为短视频类型时的文件其实是音频文件
        video_download_url: str = douyin_store._extract_video_download_url(aweme_item)

        if not video_download_url:
            return
        content = await self.dy_client.get_aweme_media(video_download_url)
        await asyncio.sleep(random.random())
        if content is None:
            return
        extension_file_name = f"video.mp4"
        title = aweme_item.get("desc", "")
        await douyin_store.update_dy_aweme_video(aweme_id, content, extension_file_name, title)
