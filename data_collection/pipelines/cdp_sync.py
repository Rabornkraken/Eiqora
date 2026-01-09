"""
CDP Browser with sync wrapper for YFinance news pipeline
Includes progressive retry logic for bot detection
"""
import asyncio
import logging
import time
import random
from datetime import datetime, timedelta

from data_collection.pipelines.cdp_browser import CDPBrowser

_cdp_browser_instance = None
_logger = None

def _get_logger():
    """Get logger instance."""
    global _logger
    if _logger is None:
        import logging
        _logger = logging.getLogger(__name__)
    return _logger


def _is_bot_blocked(html: str) -> bool:
    """
    Detect if page shows bot detection/captcha (actual block pages, not CDN references).
    
    Common indicators:
    - Very short HTML (< 500 chars) = likely captcha page
    - Specific blocking phrases
    """
    if not html or len(html) < 500:
        return True
    
    html_lower = html.lower()
    
    # STRICT blocking indicators (must be very specific to avoid false positives)
    blocking_phrases = [
        'please complete the security check',
        'checking your browser',
        'enable cookies and reload',
        'access denied',
        'are you a robot',
        'verify you are human',
        'cloudflare ray id',  # Cloudflare actual block page, not just CDN reference
        'enable javascript and cookies',
        'please wait while we verify',
        'captcha verification',
    ]
    
    for phrase in blocking_phrases:
        if phrase in html_lower:
            _get_logger().warning(f"Detected bot blocking phrase: '{phrase}'")
            return True
    
    return False


def get_cdp_browser(headless: bool = True) -> CDPBrowser:
    """Get or create CDP browser instance."""
    global _cdp_browser_instance
    
    if _cdp_browser_instance is None:
        _cdp_browser_instance = CDPBrowser(debug_port=9222, headless=headless)
        # Start in async context
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_cdp_browser_instance.start())
    
    return _cdp_browser_instance


def fetch_with_cdp(url: str) -> tuple[str, str | None]:
    """
    Fetch with CDP browser (single attempt).
    Includes auto-restart logic if browser is disconnected.
    """
    logger = _get_logger()
    global _cdp_browser_instance
    
    # Try fetching
    try:
        browser = get_cdp_browser()
        loop = asyncio.get_event_loop()
        
        # Add timeout protection
        html = loop.run_until_complete(asyncio.wait_for(browser.fetch_page(url), timeout=45))
        
        # Check for bot detection blocking
        if _is_bot_blocked(html):
            logger.debug(f"Bot detection detected for {url} - skipping")
            return "", "bot_blocked"
            
        return html, None
        
    except (RuntimeError, asyncio.TimeoutError, Exception) as e:
        error_msg = str(e)
        logger.warning(f"Fetch failed for {url}: {error_msg}")
        
        # If connection closed/crashed or NOT STARTED, force restart
        critical_errors = [
            "Connection closed", 
            "Target closed", 
            "Session closed",
            "Browser not started"
        ]
        
        if any(err in error_msg for err in critical_errors):
            logger.warning(f"Critical browser error ({error_msg})! Restarting browser...")
            cleanup_cdp_browser()
            return "", f"browser_crashed: {error_msg}"
            
        return "", error_msg


_requests_count = 0

def get_cdp_browser(headless: bool = True) -> CDPBrowser:
    """Get or create CDP browser instance with periodic restart."""
    global _cdp_browser_instance
    global _requests_count
    
    # Restart every 20 requests to prevent memory leaks/instability
    if _cdp_browser_instance and _requests_count > 20:
        _get_logger().info("Periodic browser restart (20 requests)...")
        cleanup_cdp_browser()
        _requests_count = 0
    
    if _cdp_browser_instance is None:
        try:
            # Use 0 for dynamic port assignment (prevents conflicts)
            instance = CDPBrowser(debug_port=0, headless=headless)
            # Start in async context
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(instance.start())
            
            # Only assign global if start successful
            _cdp_browser_instance = instance
            _requests_count = 0
            
        except Exception as e:
            _get_logger().error(f"Failed to start browser: {e}")
            # Ensure we don't return a broken instance or leave global in bad state
            _cdp_browser_instance = None
            raise
    
    _requests_count += 1
    return _cdp_browser_instance

def fetch_with_cdp(url: str) -> tuple[str, str | None]:
    """
    Fetch with CDP browser (single attempt).
    Includes auto-restart logic if browser is disconnected.
    """
    logger = _get_logger()
    global _cdp_browser_instance
    
    # Try fetching
    try:
        browser = get_cdp_browser()
        loop = asyncio.get_event_loop()
        
        # Add timeout protection
        html = loop.run_until_complete(asyncio.wait_for(browser.fetch_page(url), timeout=45))
        
        # Check for bot detection blocking
        if _is_bot_blocked(html):
            logger.debug(f"Bot detection detected for {url} - skipping")
            return "", "bot_blocked"
            
        return html, None
        
    except (RuntimeError, asyncio.TimeoutError, Exception) as e:
        error_msg = str(e)
        logger.warning(f"Fetch failed for {url}: {error_msg}")
        
        # If connection closed/crashed, force restart specific for this case
        if "Connection closed" in error_msg or "Target closed" in error_msg or "Session closed" in error_msg:
            logger.warning("Browser connection lost! Restarting browser...")
            cleanup_cdp_browser()
            # We don't retry immediately to avoid infinite loops, but next call will get new browser
            return "", f"browser_crashed: {error_msg}"
            
        return "", error_msg


_requests_count = 0

def get_cdp_browser(headless: bool = True) -> CDPBrowser:
    """Get or create CDP browser instance with periodic restart."""
    global _cdp_browser_instance
    global _requests_count
    
    # Restart every 20 requests to prevent memory leaks/instability
    if _cdp_browser_instance and _requests_count > 20:
        _get_logger().info("Periodic browser restart (20 requests)...")
        cleanup_cdp_browser()
        _requests_count = 0
    
    if _cdp_browser_instance is None:
        _cdp_browser_instance = CDPBrowser(debug_port=9222, headless=headless)
        # Start in async context
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_cdp_browser_instance.start())
        _requests_count = 0
    
    _requests_count += 1
    return _cdp_browser_instance

def cleanup_cdp_browser():
    """Cleanup CDP browser instance."""
    global _cdp_browser_instance
    
    if _cdp_browser_instance:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(_cdp_browser_instance.close())
            else:
                loop.run_until_complete(_cdp_browser_instance.close())
        except Exception:
            pass
        finally:
            _cdp_browser_instance = None
