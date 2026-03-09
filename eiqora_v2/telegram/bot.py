"""
Telegram PDF Broadcast Bot

Subscribers /start to receive scheduled PDF reports.
Drop PDF files into the pdf directory and they'll be broadcast
on schedule or on-demand via /send.

Run with: python -m eiqora_v2.telegram.bot
"""

import logging
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from eiqora_v2.telegram.broadcaster import Broadcaster
from eiqora_v2.telegram.config import settings
from eiqora_v2.telegram.subscriber import (
    add_subscriber,
    get_subscriber_count,
    remove_subscriber,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
_logger = logging.getLogger(__name__)


def _get_pdf_dir() -> Path:
    """Resolve the PDF directory."""
    if settings.pdf_dir:
        return Path(settings.pdf_dir)
    return Path(__file__).parent


def _find_latest_pdf() -> Path | None:
    """Find the latest PDF matching the configured pattern."""
    pdf_dir = _get_pdf_dir()
    pdfs = sorted(pdf_dir.glob(settings.pdf_pattern))
    return pdfs[-1] if pdfs else None


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _get_allowed_usernames() -> set[str]:
    """Parse the whitelist from config. Returns empty set if no restriction."""
    raw = settings.allowed_usernames.strip()
    if not raw:
        return set()
    return {u.strip().lower().lstrip("@") for u in raw.split(",") if u.strip()}


def _is_authorized(user) -> bool:
    """Check if a user is on the whitelist. Allow all if whitelist is empty."""
    allowed = _get_allowed_usernames()
    if not allowed:
        return True
    username = (user.username or "").lower() if user else ""
    return username in allowed


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start — subscribe."""
    user = update.effective_user
    chat_id = update.effective_chat.id

    if not _is_authorized(user):
        await update.message.reply_html(
            "\U0001F6AB <b>Access denied.</b>\n\n"
            "You are not authorized to use this bot. "
            "Contact your administrator to get access."
        )
        _logger.warning("Unauthorized access attempt: @%s (chat_id=%d)", user.username if user else "?", chat_id)
        return

    is_new = await add_subscriber(
        chat_id=chat_id,
        username=user.username if user else None,
        first_name=user.first_name if user else None,
    )

    if is_new:
        await update.message.reply_html(
            "\u2705 <b>Subscribed!</b>\n\n"
            "You'll receive scheduled PDF reports daily at 10:00 PM SGT (Mon-Fri).\n\n"
            "Commands:\n"
            "/status \u2014 Subscription info\n"
            "/send \u2014 Get the latest report now\n"
            "/stop \u2014 Unsubscribe"
        )
    else:
        await update.message.reply_html(
            "\U0001F44B <b>Welcome back!</b> You're already subscribed."
        )


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /stop — unsubscribe."""
    if not _is_authorized(update.effective_user):
        return
    chat_id = update.effective_chat.id
    was_active = await remove_subscriber(chat_id)

    if was_active:
        await update.message.reply_html(
            "\U0001F6D1 <b>Unsubscribed.</b> Send /start to re-subscribe."
        )
    else:
        await update.message.reply_html("You weren't subscribed.")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /status — subscription info."""
    if not _is_authorized(update.effective_user):
        return
    subs = await get_subscriber_count()
    latest = _find_latest_pdf()
    latest_name = latest.name if latest else "None"

    await update.message.reply_html(
        "\U0001F4CA <b>Bot Status</b>\n\n"
        f"Subscribers: <code>{subs}</code>\n"
        f"Latest PDF: <code>{latest_name}</code>\n"
        f"Schedule: <code>{settings.summary_hour:02d}:{settings.summary_minute:02d} "
        f"{settings.summary_timezone} Mon-Fri</code>"
    )


async def cmd_send(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /send — send the latest PDF to the requesting user."""
    if not _is_authorized(update.effective_user):
        return
    latest = _find_latest_pdf()
    if not latest:
        await update.message.reply_text("No PDF reports available yet.")
        return

    date_str = latest.stem.split("_")[-1] if "_" in latest.stem else ""
    try:
        caption_date = datetime.strptime(date_str, "%Y%m%d").strftime("%b %d, %Y")
    except ValueError:
        caption_date = latest.stem

    with open(latest, "rb") as f:
        await update.message.reply_document(
            document=f,
            caption=f"\U0001F4CB <b>{caption_date}</b>",
            parse_mode="HTML",
        )


# ---------------------------------------------------------------------------
# Scheduled broadcast
# ---------------------------------------------------------------------------

async def broadcast_latest_pdf(broadcaster: Broadcaster) -> None:
    """Find the latest PDF and broadcast to all subscribers."""
    _logger.info("Running scheduled PDF broadcast...")
    latest = _find_latest_pdf()

    if not latest:
        _logger.warning("No PDF found in %s (pattern: %s)", _get_pdf_dir(), settings.pdf_pattern)
        return

    _logger.info("Broadcasting: %s", latest.name)

    date_str = latest.stem.split("_")[-1] if "_" in latest.stem else ""
    try:
        caption_date = datetime.strptime(date_str, "%Y%m%d").strftime("%b %d, %Y")
    except ValueError:
        caption_date = latest.stem

    caption = f"\U0001F4CB <b>{caption_date}</b>"
    sent, failed = await broadcaster.broadcast_document(str(latest), caption=caption)
    _logger.info("Broadcast done: sent=%d failed=%d", sent, failed)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    token = settings.telegram_bot_token
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set")

    app = Application.builder().token(token).build()

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("send", cmd_send))

    # Broadcaster
    broadcaster = Broadcaster(app.bot, rate_limit=settings.broadcast_rate_limit)

    # Schedule PDF broadcast
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        broadcast_latest_pdf,
        CronTrigger(
            day_of_week="mon-fri",
            hour=settings.summary_hour,
            minute=settings.summary_minute,
            timezone=settings.summary_timezone,
        ),
        args=[broadcaster],
        id="pdf_broadcast",
    )

    async def post_init(application: Application) -> None:
        scheduler.start()
        _logger.info(
            "Scheduled PDF broadcast at %02d:%02d %s Mon-Fri",
            settings.summary_hour, settings.summary_minute, settings.summary_timezone,
        )

    app.post_init = post_init

    _logger.info("Starting Telegram PDF Broadcast Bot...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
