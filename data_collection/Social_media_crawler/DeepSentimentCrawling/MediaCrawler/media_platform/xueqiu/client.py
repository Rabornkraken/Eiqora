import asyncio
import json
import random
from typing import Any, Callable, Dict, List, Optional, Union
from urllib.parse import urlencode

from playwright.async_api import BrowserContext, Page, Response

from tools import utils
from .exception import DataFetchError

class XueqiuClient:
    def __init__(
        self,
        timeout=30,
        proxy=None,
        *,
        headers: Dict[str, str],
        playwright_page: Page,
        cookie_dict: Dict[str, str],
    ):
        self.proxy = proxy
        self.timeout = timeout
        self.headers = headers
        self._host = "https://xueqiu.com"
        self.playwright_page = playwright_page
        self.cookie_dict = cookie_dict

    async def update_cookies(self, browser_context: BrowserContext):
        cookie_str, cookie_dict = utils.convert_cookies(await browser_context.cookies())
        self.headers["Cookie"] = cookie_str
        self.cookie_dict = cookie_dict

    async def get_note_by_keyword(
        self,
        keyword: str,
        page: int = 1,
        count: int = 10,
        sort: str = "time"
    ) -> Dict:
        """
        Search by navigating to the search page and intercepting the natural API call.
        """
        utils.logger.info(f"[XueqiuClient] Navigating to search page for: {keyword}")
        
        # User-facing search URL
        search_page_url = f"{self._host}/k?q={keyword}"
        
        # The internal API URL we want to capture
        # Xueqiu's frontend calls this to populate the list
        api_pattern = "/query/v1/search/status"

        async def trigger_action():
            await self.playwright_page.goto(search_page_url, wait_until="domcontentloaded")
            # Simulate human behavior: Scroll down to ensure data loads
            await self.playwright_page.evaluate("window.scrollTo(0, 300)")
            await asyncio.sleep(random.uniform(1, 2))

        return await self._intercept_response(api_pattern, trigger_action)

    async def get_note_info_by_id(self, note_id: str) -> Dict:
        """
        Get post details by navigating to the post page and intercepting.
        """
        # Post URL: https://xueqiu.com/{user_id}/{note_id}
        # Since we might not have user_id, we can try the short link or API
        # But direct API fetch is risky.
        # Let's try to fetch via evaluate for specific ID if we don't navigate
        
        uri = "/statuses/show.json"
        params = {"id": note_id}
        url = f"{self._host}{uri}?{urlencode(params)}"
        return await self._request_via_evaluate(url)

    async def get_note_comments(self, note_id: str, page: int = 1, count: int = 20) -> Dict:
        """
        Get comments.
        Navigate to the post detail page if possible to trigger it naturally, 
        or use _request_via_evaluate if we are already on the page.
        """
        uri = "/statuses/comments.json"
        params = {
            "id": note_id,
            "page": page,
            "count": count,
            "reply": "true",
            "asc": "false"
        }
        url = f"{self._host}{uri}?{urlencode(params)}"
        return await self._request_via_evaluate(url)

    async def _intercept_response(self, url_substring: str, trigger_action: Callable) -> Dict:
        """
        Intersects a network response matching the url_substring while performing trigger_action.
        Retries up to 3 times on failure.
        """
        # Define the predicate to match the response
        def response_predicate(response: Response) -> bool:
            return url_substring in response.url and response.status == 200

        max_retries = 3
        for attempt in range(max_retries):
            try:
                # Setup the listener BEFORE the action
                async with self.playwright_page.expect_response(response_predicate, timeout=self.timeout * 1000) as response_info:
                    await trigger_action()
                
                response = await response_info.value
                text = await response.text()
                
                if not text:
                    raise DataFetchError("Empty response body")
                    
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    if "aliyun_waf" in text:
                        raise DataFetchError("Triggered Aliyun WAF")
                    raise DataFetchError(f"JSON decode error. Content preview: {text[:100]}")
                
            except Exception as e:
                utils.logger.warning(f"[XueqiuClient] Interception attempt {attempt+1}/{max_retries} failed: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(random.uniform(2, 4))
                    # Refresh page or take a step back? trigger_action usually includes navigation, so retrying it is enough.
                else:
                    utils.logger.error(f"[XueqiuClient] All interception attempts failed for {url_substring}")
                    raise DataFetchError(f"Failed to capture data after {max_retries} attempts: {e}")

    async def _request_via_evaluate(self, url: str) -> Dict:
        """
        Fallback: Execute fetch() inside the browser context.
        Less safe than interception but useful for sub-requests (like comments) 
        once the page is loaded and WAF trust is established.
        """
        # Add random delay before request
        await asyncio.sleep(random.uniform(0.5, 1.5))
        
        utils.logger.info(f"[XueqiuClient] Fetching via browser: {url}")
        result = await self.playwright_page.evaluate(
            """
            async (url) => {
                try {
                    const response = await fetch(url);
                    const text = await response.text();
                    return { status: response.status, text: text };
                } catch (e) {
                    return { status: 0, text: e.toString() };
                }
            }
            """,
            url
        )
        
        if result['status'] != 200:
             raise DataFetchError(f"Browser fetch failed: {result['text'][:100]}")
             
        try:
            return json.loads(result['text'])
        except:
            if "aliyun_waf" in result['text']:
                raise DataFetchError("Triggered WAF")
            raise DataFetchError("JSON decode error")
