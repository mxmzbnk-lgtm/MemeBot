"""
Telegram Meme Bot — receives memes from admin, queues them, drip-feeds to channel (max 3/day).
Uses aiogram 3.x and aiosqlite.
"""

import asyncio
import logging
import os
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import aiosqlite
from dotenv import load_dotenv

load_dotenv()
import pytz
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import Message

# =============================================================================
# CONFIGURATION — values are loaded from .env (BOT_TOKEN, ADMIN_ID, CHANNEL_ID)
# =============================================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
CHANNEL_ID = os.getenv("CHANNEL_ID", "").strip()  # @channel or -1001234567890

# SQLite database file path:
DB_PATH = Path(__file__).parent / "meme_bot.db"

# Scheduler: check every N minutes whether to post next item:
SCHEDULER_INTERVAL_MINUTES = 5

# Timezone for "today" and working hours:
TIMEZONE = pytz.timezone("Europe/Kyiv")
WORK_START_HOUR, WORK_START_MINUTE = 9, 0   # 09:00
WORK_END_HOUR, WORK_END_MINUTE = 22, 0     # 22:00

# Minimum gap between two posts (hours). Random extra 0–2h for natural spread (total 3–5h):
MIN_GAP_HOURS = 3
MAX_EXTRA_GAP_HOURS = 2

# =============================================================================
# Logging
# =============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# =============================================================================
# Database
# =============================================================================
async def init_db():
    """Create queue and history tables if they do not exist."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_id TEXT,
                caption TEXT,
                message_type TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                posted_at TEXT NOT NULL
            )
        """)
        await db.commit()
    logger.info("Database initialized.")


async def queue_add(file_id: Optional[str], caption: Optional[str], message_type: str):
    """Append one item to the queue (one row per photo/video/text — ungrouped)."""
    now = datetime.utcnow().isoformat() + "Z"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO queue (file_id, caption, message_type, created_at) VALUES (?, ?, ?, ?)",
            (file_id or "", caption or "", message_type, now),
        )
        await db.commit()
    logger.info("Queued 1 item: type=%s", message_type)


async def queue_get_next():
    """Get the next queue item (FIFO) and remove it. Returns (id, file_id, caption, message_type) or None."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, file_id, caption, message_type FROM queue ORDER BY id ASC LIMIT 1"
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        await db.execute("DELETE FROM queue WHERE id = ?", (row["id"],))
        await db.commit()
        return (row["id"], row["file_id"], row["caption"], row["message_type"])


async def queue_count() -> int:
    """Return number of items in the queue."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM queue") as cur:
            (n,) = await cur.fetchone()
    return n


async def history_add_post():
    """Record that a post was sent now (for daily limit)."""
    now = datetime.utcnow().isoformat() + "Z"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO history (posted_at) VALUES (?)", (now,))
        await db.commit()


def _today_start_end_utc():
    """Return (start, end) of 'today' in Europe/Kyiv as UTC datetimes."""
    now_kyiv = datetime.now(TIMEZONE)
    start_kyiv = now_kyiv.replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    end_kyiv = start_kyiv + timedelta(days=1)
    return start_kyiv.astimezone(pytz.UTC), end_kyiv.astimezone(pytz.UTC)


async def history_count_today() -> int:
    """Count how many posts were sent today (Europe/Kyiv date)."""
    start_utc, end_utc = _today_start_end_utc()
    start_ts = start_utc.isoformat()
    end_ts = end_utc.isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM history WHERE posted_at >= ? AND posted_at < ?",
            (start_ts, end_ts),
        ) as cur:
            (n,) = await cur.fetchone()
    return n


async def history_last_posted_at() -> Optional[datetime]:
    """Return UTC datetime of the last post, or None if no posts."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT posted_at FROM history ORDER BY id DESC LIMIT 1"
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        return None
    return datetime.fromisoformat(row[0].replace("Z", "+00:00"))


async def queue_clear():
    """Remove all items from the queue."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM queue")
        await db.commit()
    logger.info("Queue cleared.")


# =============================================================================
# Admin check
# =============================================================================
def is_admin(message: Message) -> bool:
    return message.from_user is not None and message.from_user.id == ADMIN_ID


# =============================================================================
# Handlers — COMMANDS FIRST (so they are not caught by generic text handler)
# =============================================================================
router = Router()


# ---------- Commands (registered first so they match before generic F.text) ----------
@router.message(Command("stats"))
async def cmd_stats(message: Message):
    print("cmd_stats: message.from_user.id =", message.from_user.id if message.from_user else None)
    if not is_admin(message):
        return
    q = await queue_count()
    today = await history_count_today()
    await message.reply(f"Queue: {q} items. Posted today: {today}/3.")


@router.message(Command("force"))
async def cmd_force(message: Message):
    print("cmd_force: message.from_user.id =", message.from_user.id if message.from_user else None)
    if not is_admin(message):
        return
    posted, reason = await try_post_one(message.bot)
    if posted:
        await message.reply("Posted one item to the channel.")
    else:
        await message.reply(f"Could not post now: {reason}")


@router.message(Command("clear"))
async def cmd_clear(message: Message):
    print("cmd_clear: message.from_user.id =", message.from_user.id if message.from_user else None)
    if not is_admin(message):
        return
    await queue_clear()
    await message.reply("Queue cleared.")


# ---------- Meme content (photo, video, text) — after commands ----------
@router.message(F.photo)
async def on_photo(message: Message):
    if not is_admin(message):
        return
    # Albums: each photo is a separate update; we save each as its own row (ungrouping).
    photo = message.photo[-1]
    await queue_add(photo.file_id, message.caption or None, "photo")
    await message.reply("Photo added to queue.")


@router.message(F.video)
async def on_video(message: Message):
    if not is_admin(message):
        return
    video = message.video
    if video is None:
        return
    await queue_add(video.file_id, message.caption or None, "video")
    await message.reply("Video added to queue.")


# Text memes only; exclude commands by filter so /stats, /force, /clear are not queued.
@router.message(F.text, ~F.text.startswith("/"))
async def on_text(message: Message):
    if not is_admin(message):
        return
    await queue_add(None, message.text or "", "text")
    await message.reply("Text added to queue.")


# =============================================================================
# Scheduler: drip-feed queue to channel (max 3/day, working hours, 3–5h gap)
# =============================================================================
def _is_working_hours(now_kyiv: datetime) -> bool:
    start = now_kyiv.replace(hour=WORK_START_HOUR, minute=WORK_START_MINUTE, second=0, microsecond=0)
    end = now_kyiv.replace(hour=WORK_END_HOUR, minute=WORK_END_MINUTE, second=0, microsecond=0)
    return start <= now_kyiv <= end


async def try_post_one(bot: Bot):
    """
    Try to post one item from the queue to the channel.
    Returns (True, None) if posted, or (False, reason_string) if not.
    """
    now_kyiv = datetime.now(TIMEZONE)
    if not _is_working_hours(now_kyiv):
        return (False, "outside working hours (09:00–22:00 Europe/Kyiv)")

    posts_today = await history_count_today()
    if posts_today >= 3:
        return (False, "daily limit reached (3 posts today)")

    last = await history_last_posted_at()
    if last is not None:
        gap_hours = MIN_GAP_HOURS + random.uniform(0, MAX_EXTRA_GAP_HOURS)
        next_ok = last + timedelta(hours=gap_hours)
        now_utc = datetime.now(pytz.UTC)
        if now_utc < next_ok:
            return (False, "too soon since last post (3–5h gap required)")

    item = await queue_get_next()
    if item is None:
        return (False, "queue is empty")

    _id, file_id, caption, message_type = item
    caption = caption or None

    try:
        if message_type == "photo":
            await bot.send_photo(CHANNEL_ID, photo=file_id, caption=caption)
        elif message_type == "video":
            await bot.send_video(CHANNEL_ID, video=file_id, caption=caption)
        else:
            await bot.send_message(CHANNEL_ID, text=caption or "(no text)")
        await history_add_post()
        logger.info("Posted to channel: type=%s", message_type)
        return (True, None)
    except Exception as e:
        logger.exception("Failed to post to channel: %s", e)
        await queue_add(file_id or None, caption, message_type)
        return (False, f"send failed: {e}")


async def scheduler_loop(bot: Bot):
    """Background task: every few minutes, try to post one item if conditions are met."""
    while True:
        try:
            await try_post_one(bot)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("Scheduler error: %s", e)
        await asyncio.sleep(SCHEDULER_INTERVAL_MINUTES * 60)


# =============================================================================
# Entry point
# =============================================================================
async def main():
    # Put your Bot Token here or in env; this placeholder will fail at runtime as a reminder:
    if not BOT_TOKEN:
        raise ValueError("Set BOT_TOKEN in .env. Get the token from @BotFather.")
    if not CHANNEL_ID:
        raise ValueError("Set CHANNEL_ID in .env (e.g. @channel or -1001234567890).")

    await init_db()

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    # Run scheduler in background
    scheduler = asyncio.create_task(scheduler_loop(bot))

    try:
        logger.info("Bot starting.")
        await dp.start_polling(bot)
    finally:
        scheduler.cancel()
        try:
            await scheduler
        except asyncio.CancelledError:
            pass
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
