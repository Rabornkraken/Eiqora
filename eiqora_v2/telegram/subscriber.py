"""Subscriber CRUD operations."""

import logging
from eiqora_v2.telegram.db import get_connection

_logger = logging.getLogger(__name__)


async def add_subscriber(chat_id: int, username: str | None = None, first_name: str | None = None) -> bool:
    """Add or re-activate a subscriber. Returns True if new."""
    async with get_connection() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO telegram_subscriber (chat_id, username, first_name)
            VALUES ($1, $2, $3)
            ON CONFLICT (chat_id) DO UPDATE SET
                is_active = TRUE,
                username = COALESCE(EXCLUDED.username, telegram_subscriber.username),
                first_name = COALESCE(EXCLUDED.first_name, telegram_subscriber.first_name),
                unsubscribed_at = NULL
            RETURNING (xmax = 0) AS is_new
            """,
            chat_id, username, first_name,
        )
        is_new = row["is_new"] if row else False
        _logger.info("Subscriber %s: chat_id=%d (new=%s)", username or "unknown", chat_id, is_new)
        return is_new


async def remove_subscriber(chat_id: int) -> bool:
    """Deactivate a subscriber. Returns True if was active."""
    async with get_connection() as conn:
        result = await conn.execute(
            """
            UPDATE telegram_subscriber
            SET is_active = FALSE, unsubscribed_at = NOW()
            WHERE chat_id = $1 AND is_active = TRUE
            """,
            chat_id,
        )
        removed = result == "UPDATE 1"
        _logger.info("Unsubscribe chat_id=%d (was_active=%s)", chat_id, removed)
        return removed


async def get_active_subscribers() -> list[int]:
    """Return list of active subscriber chat_ids, filtered by whitelist."""
    from eiqora_v2.telegram.config import settings

    allowed_raw = settings.allowed_usernames.strip()
    allowed = {u.strip().lower().lstrip("@") for u in allowed_raw.split(",") if u.strip()} if allowed_raw else set()

    async with get_connection() as conn:
        if allowed:
            rows = await conn.fetch(
                "SELECT chat_id FROM telegram_subscriber WHERE is_active = TRUE AND LOWER(username) = ANY($1)",
                list(allowed),
            )
        else:
            rows = await conn.fetch(
                "SELECT chat_id FROM telegram_subscriber WHERE is_active = TRUE"
            )
        return [row["chat_id"] for row in rows]


async def get_subscriber_count() -> int:
    """Return count of active subscribers."""
    async with get_connection() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM telegram_subscriber WHERE is_active = TRUE"
        ) or 0
