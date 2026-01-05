"""
CDP Browser Manager for YFinance News Fetching
Robust implementation with dynamic ports and modern headless mode
"""
import os
import socket
import asyncio
import httpx
import subprocess
import platform
import shutil
import tempfile
from typing import Optional
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

import logging
_logger = logging.getLogger(__name__)

# Path to stealth script from MediaCrawler
STEALTH_JS_PATH = "/Users/pan/Documents/Github/Eiqora/data_collection/Social_media_crawler/DeepSentimentCrawling/MediaCrawler/libs/stealth.min.js"


class CDPBrowser:
    """
    CDP-based browser manager for reliable article fetching.
    Launches a persistent Chrome/Edge instance and connects via CDP.
    Uses dynamic ports and modern headless mode for stability.
    """
    
    def __init__(self, debug_port: int = 0, headless: bool = True):
        self.debug_port = debug_port  # 0 means dynamic assignment
        self.headless = headless
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.browser_process = None
        self._playwright = None
        self.user_data_dir = None
        
    def _find_browser_path(self) -> str:
        """Find Chrome or Edge browser on system."""
        system = platform.system()
        
        if system == "Darwin":  # macOS
            paths = [
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            ]
        elif system == "Linux":
            paths = [
                "/usr/bin/google-chrome",
                "/usr/bin/google-chrome-stable",
                "/usr/bin/chromium-browser",
            ]
        else:  # Windows
            paths = [
                os.path.expandvars(r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe"),
                os.path.expandvars(r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe"),
            ]
        
        for path in paths:
            if os.path.isfile(path) and os.access(path, os.X_OK):
                return path
        
        raise RuntimeError("No Chrome/Edge browser found on system")
    
    def _get_free_port(self) -> int:
        """Get a free ephemeral port."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('', 0))
            return s.getsockname()[1]
    
    def _is_port_open(self, port: int) -> bool:
        """Check if port is accepting connections."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                return s.connect_ex(('localhost', port)) == 0
        except:
            return False
    
    async def _launch_browser_process(self):
        """Launch browser with CDP enabled."""
        browser_path = self._find_browser_path()
        
        # Assign dynamic port if needed
        if self.debug_port == 0:
            self.debug_port = self._get_free_port()
            
        # Create temp user data dir for session persistence/isolation
        self.user_data_dir = tempfile.mkdtemp(prefix="cdp_chrome_")
        
        # Launch browser with CDP
        args = [
            browser_path,
            f"--remote-debugging-port={self.debug_port}",
            "--remote-debugging-address=127.0.0.1", # More secure than 0.0.0.0
            f"--user-data-dir={self.user_data_dir}", # Critical for isolation
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--disable-background-timer-throttling",
            "--disable-dev-shm-usage",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-blink-features=AutomationControlled", # Stealth
            "--disable-features=IsolateOrigins,site-per-process",
            "--disable-web-security",
            "--window-size=1920,1080",
        ]
        
        if self.headless:
            # Use modern headless mode (undetectable)
            args.extend(["--headless=new", "--disable-gpu"])
        
        _logger.info(f"Launching browser on port {self.debug_port} (headless={self.headless})...")
        
        # Start new session to allow clean group kill
        if platform.system() == "Windows":
             creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
             preexec_fn = None
        else:
             creationflags = 0
             preexec_fn = os.setsid
             
        self.browser_process = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            preexec_fn=preexec_fn
        )
        
        # Wait for browser to be ready
        for i in range(40):
            if self._is_port_open(self.debug_port):
                _logger.info(f"Browser ready on port {self.debug_port}")
                await asyncio.sleep(1)  # Stabilize
                return
            await asyncio.sleep(0.5)
        
        self.close_process()
        raise RuntimeError(f"Browser failed to start on port {self.debug_port} within 20s")
    
    async def _get_ws_url(self) -> str:
        """Get WebSocket URL from CDP."""
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"http://localhost:{self.debug_port}/json/version",
                    timeout=10
                )
                if response.status_code == 200:
                    data = response.json()
                    ws_url = data.get("webSocketDebuggerUrl")
                    if not ws_url:
                        raise RuntimeError("No webSocketDebuggerUrl in response")
                    return ws_url
            except Exception as e:
                raise RuntimeError(f"Failed to get WS URL: {e}")
            raise RuntimeError(f"HTTP {response.status_code} from CDP")
    
    async def start(self):
        """Initialize browser and connect via CDP."""
        try:
            # Launch browser process
            await self._launch_browser_process()
            
            # Get WebSocket URL
            ws_url = await self._get_ws_url()
            
            # Connect via Playwright
            self._playwright = await async_playwright().start()
            self.browser = await self._playwright.chromium.connect_over_cdp(ws_url)
            
            # Get or create context
            contexts = self.browser.contexts
            if contexts:
                self.context = contexts[0]
            else:
                self.context = await self.browser.new_context(
                    viewport={"width": 1920, "height": 1080},
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    locale="en-US",
                    timezone_id="America/New_York"
                )
                
                # Inject stealth script if available
                if os.path.exists(STEALTH_JS_PATH):
                    try:
                        await self.context.add_init_script(path=STEALTH_JS_PATH)
                    except Exception as e:
                        _logger.warning(f"Failed to inject stealth script: {e}")
            
            _logger.info("CDP browser fully connected")
            
        except Exception as e:
            _logger.error(f"Failed to start CDP browser: {e}")
            await self.close()
            raise
    
    async def fetch_page(self, url: str, timeout: int = 45000) -> str:
        """Fetch a page and return HTML."""
        if not self.context:
            raise RuntimeError("Browser not started")
        
        page = await self.context.new_page()
        try:
            # Human-like timeout and waiting
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            
            # Random sleep to look human (1-2s)
            await asyncio.sleep(1.5) 
            
            html = await page.content()
            return html
        except Exception as e:
            raise e
        finally:
            await page.close()
    
    def close_process(self):
        """Force kill the browser process group."""
        if self.browser_process:
            try:
                if platform.system() == "Windows":
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(self.browser_process.pid)],
                        capture_output=True
                    )
                else:
                    os.killpg(os.getpgid(self.browser_process.pid), 9)
            except Exception:
                pass
            self.browser_process = None
            
        # Clean up temp dir
        if self.user_data_dir and os.path.exists(self.user_data_dir):
            try:
                shutil.rmtree(self.user_data_dir, ignore_errors=True)
            except:
                pass

    async def close(self):
        """Clean up resources."""
        try:
            if self.context:
                await self.context.close()
            if self.browser:
                await self.browser.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass
        finally:
            self.close_process()
