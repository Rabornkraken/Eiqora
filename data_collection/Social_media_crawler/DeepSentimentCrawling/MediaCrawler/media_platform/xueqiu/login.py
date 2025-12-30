import asyncio
import sys
from typing import Optional

from playwright.async_api import BrowserContext, Page
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_result

import config
from base.base_crawler import AbstractLogin
from tools import utils

class XueqiuLogin(AbstractLogin):
    def __init__(self,
                 login_type: str,
                 browser_context: BrowserContext,
                 context_page: Page,
                 login_phone: Optional[str] = "",
                 cookie_str: str = ""
                 ):
        self.login_type = login_type
        self.browser_context = browser_context
        self.context_page = context_page
        self.login_phone = login_phone
        self.cookie_str = cookie_str
        self.login_url = "https://xueqiu.com/"

    async def begin(self):
        """Start login xueqiu"""
        utils.logger.info("[XueqiuLogin.begin] Begin login xueqiu ...")
        if self.login_type == "qrcode":
            await self.login_by_qrcode()
        elif self.login_type == "cookie":
            await self.login_by_cookies()
        else:
            raise ValueError("[XueqiuLogin.begin] Invalid Login Type")

    async def login_by_qrcode(self):
        """Login by scanning QR code"""
        utils.logger.info("[XueqiuLogin.login_by_qrcode] Opening Xueqiu homepage...")
        await self.context_page.goto(self.login_url)
        
        # Wait for user to interact
        utils.logger.info("=" * 50)
        utils.logger.info("PLEASE LOGIN MANUALLY IN THE BROWSER WINDOW")
        utils.logger.info("Scan the QR code or enter your username/password.")
        utils.logger.info("The script will wait until it detects the User Avatar/Profile in the navbar.")
        utils.logger.info("=" * 50)
        
        # Poll for login status every 2 seconds
        await self.check_login_state()

    @retry(stop=stop_after_attempt(300), wait=wait_fixed(2))
    async def check_login_state(self):
        """
        Check if login is successful.
        We check for the presence of the user avatar/profile element in the nav bar,
        which indicates a real user is logged in (vs just a guest with cookies).
        """
        try:
            # Check for user avatar or profile link
            # Xueqiu usually has a class like 'nav__user-avatar' or similar for logged in users
            # We can also check if the "Login" button is gone, but checking for Avatar is safer.
            
            # Selector for the user profile link/avatar in the top navigation
            # This selector might need adjustment if Xueqiu changes their UI, 
            # but usually it's inside the nav bar.
            # Using a generic selector that typically appears for logged-in users
            
            # Attempt to find the user dropdown/avatar
            is_logged_in = await self.context_page.evaluate("""() => {
                // Look for the user profile container in the nav bar
                const userNav = document.querySelector('.nav__user');
                const avatar = document.querySelector('.nav__user-avatar');
                const profileLink = document.querySelector('a[href^="/u/"]');
                // Also check if there's a logout button, which only appears when logged in
                const logout = document.querySelector('.dropdown-menu a[href*="logout"]');
                
                return !!(userNav || avatar || profileLink || logout);
            }""")
            
            if is_logged_in:
                # Double check cookies just to be safe
                cookies = await self.browser_context.cookies()
                cookie_dict = {c['name']: c['value'] for c in cookies}
                if 'xq_a_token' in cookie_dict:
                    utils.logger.info("[XueqiuLogin.check_login_state] Login successful! Detected User UI and cookies.")
                    return True
        
        except Exception as e:
            # Ignore errors during check (e.g. element not found)
            pass
        
        utils.logger.info("[XueqiuLogin.check_login_state] Waiting for you to scan QR code/Login...")
        raise Exception("Not logged in yet")

    async def login_by_cookies(self):
        """Login by setting cookies directly"""
        utils.logger.info("[XueqiuLogin.login_by_cookies] Setting cookies...")
        for key, value in utils.convert_str_cookie_to_dict(self.cookie_str).items():
            await self.browser_context.add_cookies([{
                'name': key,
                'value': value,
                'domain': ".xueqiu.com",
                'path': "/"
            }])
