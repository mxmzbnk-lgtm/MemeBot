"""
Telegram Meme Bot — receives memes from admin, queues them, posts to channel
at fixed times: 08:00, 13:00, 16:00, 19:00 (Europe/Kyiv). Max 4 posts/day.
Uses aiogram 3.x and aiosqlite.
"""

import asyncio
import logging
import os
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

# SQLite database file path (override DATA_DIR env var to point to a persistent volume):
DB_PATH = Path(os.getenv("DATA_DIR", str(Path(__file__).parent))) / "meme_bot.db"

# Scheduler: check every N minutes:
SCHEDULER_INTERVAL_MINUTES = 5

# Timezone for scheduling:
TIMEZONE = pytz.timezone("Europe/Kyiv")

# Fixed posting times (hour, minute) in Europe/Kyiv:
POST_TIMES = [(8, 0), (13, 0), (16, 0), (19, 0)]

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
    """Append one item to the queue."""
    now = datetime.utcnow().isoformat() + "Z"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO queue (file_id, caption, message_type, created_at) VALUES (?, ?, ?, ?)",
            (file_id or "", caption or "", message_type, now),
        )
        await db.commit()
    logger.info("Queued 1 item: type=%s", message_type)


async def queue_get_next():
    """Get the next queue item (FIFO) and remove it atomically. Returns (id, file_id, caption, message_type) or None."""
    async with aiosqlite.connect(DB_PATH) as db:
        # DELETE ... RETURNING is atomic — no other process can get the same row
        async with db.execute("""
            DELETE FROM queue WHERE id = (
                SELECT id FROM queue ORDER BY id ASC LIMIT 1
            ) RETURNING id, file_id, caption, message_type
        """) as cur:
            row = await cur.fetchone()
        await db.commit()
    if row is None:
        return None
    return (row[0], row[1], row[2], row[3])


async def queue_count() -> int:
    """Return number of items in the queue."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM queue") as cur:
            (n,) = await cur.fetchone()
    return n


async def queue_list():
    """Return all queued items in order."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, file_id, caption, message_type FROM queue ORDER BY id ASC"
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def queue_delete(item_id: int) -> bool:
    """Delete item by id. Returns True if found and deleted."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("DELETE FROM queue WHERE id = ?", (item_id,))
        await db.commit()
        return cursor.rowcount > 0


async def queue_clear():
    """Remove all items from the queue."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM queue")
        await db.commit()
    logger.info("Queue cleared.")


async def history_add_post():
    """Record that a post was sent now (for daily scheduling)."""
    now = datetime.utcnow().isoformat() + "Z"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO history (posted_at) VALUES (?)", (now,))
        await db.commit()


def _today_start_end_utc():
    """Return (start, end) of 'today' in Europe/Kyiv as UTC datetimes."""
    now_kyiv = datetime.now(TIMEZONE)
    start_kyiv = now_kyiv.replace(hour=0, minute=0, second=0, microsecond=0)
    end_kyiv = start_kyiv + timedelta(days=1)
    return start_kyiv.astimezone(pytz.UTC), end_kyiv.astimezone(pytz.UTC)


async def history_count_today() -> int:
    """Count how many posts were sent today (Europe/Kyiv date)."""
    start_utc, end_utc = _today_start_end_utc()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM history WHERE posted_at >= ? AND posted_at < ?",
            (start_utc.isoformat(), end_utc.isoformat()),
        ) as cur:
            (n,) = await cur.fetchone()
    return n


async def history_get_today_kyiv_times():
    """Return list of Kyiv-timezone datetimes for all posts made today."""
    start_utc, end_utc = _today_start_end_utc()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT posted_at FROM history WHERE posted_at >= ? AND posted_at < ?",
            (start_utc.isoformat(), end_utc.isoformat()),
        ) as cur:
            rows = await cur.fetchall()
    return [
        datetime.fromisoformat(row[0].replace("Z", "+00:00")).astimezone(TIMEZONE)
        for row in rows
    ]


# =============================================================================
# Admin check
# =============================================================================
def is_admin(message: Message) -> bool:
    return message.from_user is not None and message.from_user.id == ADMIN_ID


# =============================================================================
# Handlers
# =============================================================================
router = Router()
_post_lock = asyncio.Lock()


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    if not is_admin(message):
        return
    q = await queue_count()
    today = await history_count_today()
    times_str = ", ".join(f"{h:02d}:{m:02d}" for h, m in POST_TIMES)
    await message.reply(f"Queue: {q} items.\nPosted today: {today}/{len(POST_TIMES)}.\nSchedule: {times_str} (Kyiv).")


@router.message(Command("queue"))
async def cmd_queue(message: Message):
    if not is_admin(message):
        return
    items = await queue_list()
    if not items:
        await message.reply("Queue is empty.")
        return

    lines = [f"Queue ({len(items)} items):"]
    for item in items[:50]:
        content = item["caption"] or ""
        preview = (content[:40] + "…") if len(content) > 40 else content
        preview = preview or "(no caption)"
        lines.append(f"#{item['id']} [{item['message_type']}] {preview}")

    if len(items) > 50:
        lines.append(f"… and {len(items) - 50} more")

    lines.append("\nUse /delete <id> to remove an item.")
    await message.reply("\n".join(lines))


@router.message(Command("delete"))
async def cmd_delete(message: Message):
    if not is_admin(message):
        return
    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2 or not args[1].strip().isdigit():
        await message.reply("Usage: /delete <id>")
        return
    item_id = int(args[1].strip())
    deleted = await queue_delete(item_id)
    if deleted:
        await message.reply(f"Item #{item_id} deleted from queue.")
    else:
        await message.reply(f"Item #{item_id} not found in queue.")


@router.message(Command("force"))
async def cmd_force(message: Message):
    if not is_admin(message):
        return
    posted, reason = await try_post_one(message.bot, force=True)
    if posted:
        await message.reply("Posted one item to the channel.")
    else:
        await message.reply(f"Could not post now: {reason}")


@router.message(Command("clear"))
async def cmd_clear(message: Message):
    if not is_admin(message):
        return
    await queue_clear()
    await message.reply("Queue cleared.")


# ---------- Meme content (photo, video, text) — after commands ----------

@router.message(F.photo)
async def on_photo(message: Message):
    if not is_admin(message):
        return
    photo = message.photo[-1]
    # Don't copy caption from forwarded messages
    caption = None if message.forward_origin else (message.caption or None)
    await queue_add(photo.file_id, caption, "photo")
    await message.reply("Photo added to queue.")


@router.message(F.video)
async def on_video(message: Message):
    if not is_admin(message):
        return
    video = message.video
    if video is None:
        return
    # Don't copy caption from forwarded messages
    caption = None if message.forward_origin else (message.caption or None)
    await queue_add(video.file_id, caption, "video")
    await message.reply("Video added to queue.")


# Text memes only; exclude commands so /stats, /queue, etc. are not queued.
@router.message(F.text, ~F.text.startswith("/"))
async def on_text(message: Message):
    if not is_admin(message):
        return
    # Forwarded text messages carry someone else's text — skip them
    if message.forward_origin:
        await message.reply("Forwarded text messages are not queued (no content to post).")
        return
    await queue_add(None, message.text or "", "text")
    await message.reply("Text added to queue.")


# =============================================================================
# Scheduler: post at fixed times 08:00, 13:00, 16:00, 19:00 (Europe/Kyiv)
# =============================================================================
async def _is_slot_due() -> bool:
    """
    Return True only if we are currently inside a scheduled slot's window
    AND no post has been made in that window yet.
    Missed/past slots are ignored to avoid catching up with old posts.
    """
    now_kyiv = datetime.now(TIMEZONE)
    today_start = now_kyiv.replace(hour=0, minute=0, second=0, microsecond=0)
    posted_times = await history_get_today_kyiv_times()

    for i, (hour, minute) in enumerate(POST_TIMES):
        slot_start = today_start.replace(hour=hour, minute=minute)

        # Window ends at the next slot's start time (or midnight for the last slot)
        if i + 1 < len(POST_TIMES):
            next_hour, next_minute = POST_TIMES[i + 1]
            slot_end = today_start.replace(hour=next_hour, minute=next_minute)
        else:
            slot_end = today_start + timedelta(days=1)

        # Only act on the slot whose window we are currently inside
        if not (slot_start <= now_kyiv < slot_end):
            continue

        already_posted = any(slot_start <= t < slot_end for t in posted_times)
        return not already_posted

    return False


async def try_post_one(bot: Bot, force: bool = False):
    """
    Try to post one item from the queue to the channel.
    Returns (True, None) if posted, or (False, reason_string) if not.
    If force=True, skips schedule checks.
    """
    async with _post_lock:
        if not force:
            if not await _is_slot_due():
                return (False, "no scheduled slot is due right now")

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
    """Background task: every few minutes, try to post if a slot is due."""
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
    if not BOT_TOKEN:
        raise ValueError("Set BOT_TOKEN in .env. Get the token from @BotFather.")
    if not CHANNEL_ID:
        raise ValueError("Set CHANNEL_ID in .env (e.g. @channel or -1001234567890).")

    await init_db()

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

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
