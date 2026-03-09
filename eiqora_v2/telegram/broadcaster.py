"""Rate-limited broadcast sender."""

import asyncio
import logging

from telegram import Bot
from telegram.error import Forbidden, RetryAfter, TimedOut

from eiqora_v2.telegram.subscriber import get_active_subscribers, remove_subscriber

_logger = logging.getLogger(__name__)


class Broadcaster:
    """Sends messages and documents to all active subscribers with rate limiting."""

    def __init__(self, bot: Bot, rate_limit: float = 25.0):
        self.bot = bot
        self._interval = 1.0 / rate_limit

    async def broadcast_message(self, text: str) -> tuple[int, int]:
        """Send a text message to all subscribers. Returns (success, failures)."""
        chat_ids = await get_active_subscribers()
        if not chat_ids:
            _logger.info("No active subscribers")
            return 0, 0

        success, failures = 0, 0

        for chat_id in chat_ids:
            try:
                await self.bot.send_message(
                    chat_id=chat_id, text=text,
                    parse_mode="HTML", disable_web_page_preview=True,
                )
                success += 1
            except Forbidden:
                _logger.warning("User %d blocked bot — deactivating", chat_id)
                await remove_subscriber(chat_id)
                failures += 1
            except RetryAfter as e:
                await asyncio.sleep(e.retry_after)
                try:
                    await self.bot.send_message(
                        chat_id=chat_id, text=text,
                        parse_mode="HTML", disable_web_page_preview=True,
                    )
                    success += 1
                except Exception:
                    failures += 1
            except (TimedOut, Exception) as exc:
                _logger.error("Failed to send to %d: %s", chat_id, exc)
                failures += 1

            await asyncio.sleep(self._interval)

        _logger.info("Message broadcast: sent=%d failed=%d", success, failures)
        return success, failures

    async def broadcast_document(
        self, file_path: str, caption: str = "",
    ) -> tuple[int, int]:
        """Send a document (PDF) to all subscribers. Returns (success, failures)."""
        chat_ids = await get_active_subscribers()
        if not chat_ids:
            _logger.info("No active subscribers")
            return 0, 0

        success, failures = 0, 0

        for chat_id in chat_ids:
            try:
                with open(file_path, "rb") as f:
                    await self.bot.send_document(
                        chat_id=chat_id, document=f,
                        caption=caption, parse_mode="HTML",
                    )
                success += 1
            except Forbidden:
                _logger.warning("User %d blocked bot — deactivating", chat_id)
                await remove_subscriber(chat_id)
                failures += 1
            except RetryAfter as e:
                await asyncio.sleep(e.retry_after)
                try:
                    with open(file_path, "rb") as f:
                        await self.bot.send_document(
                            chat_id=chat_id, document=f,
                            caption=caption, parse_mode="HTML",
                        )
                    success += 1
                except Exception:
                    failures += 1
            except (TimedOut, Exception) as exc:
                _logger.error("Failed to send doc to %d: %s", chat_id, exc)
                failures += 1

            await asyncio.sleep(self._interval)

        _logger.info("Document broadcast: sent=%d failed=%d", success, failures)
        return success, failures
