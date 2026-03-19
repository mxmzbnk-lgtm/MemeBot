"""Vercel serverless function — Telegram webhook + cron trigger."""

import json
import logging
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from typing import Optional
from urllib.parse import urlparse, parse_qs

import pytz

sys.path.insert(0, __import__("os").path.join(__import__("os").path.dirname(__file__), ".."))

from lib.config import ADMIN_ID, CHANNEL_ID, CRON_SECRET, TZ_NAME, WEBHOOK_SECRET
from lib import storage, telegram
from lib.scheduler import find_next_slot

logger = logging.getLogger(__name__)
TZ = pytz.timezone(TZ_NAME)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_admin(message: dict) -> bool:
    user = message.get("from")
    return user is not None and user.get("id") == ADMIN_ID


def _format_ts(ts: int) -> str:
    dt = datetime.fromtimestamp(ts, tz=TZ)
    return dt.strftime("%d.%m о %H:%M")


# ---------------------------------------------------------------------------
# Webhook: save meme to Redis (NO posting to channel here)
# ---------------------------------------------------------------------------

def _enqueue_meme(message: dict, msg_type: str, file_id: Optional[str], caption: Optional[str]):
    chat_id = message["chat"]["id"]
    message_id = message["message_id"]

    if not storage.acquire_lock():
        telegram.reply(chat_id, message_id, "Зачекайте секунду і спробуйте знову.")
        return

    try:
        future_slots = storage.get_future_slots()
        slot_ts = find_next_slot(future_slots)

        storage.enqueue(float(slot_ts), msg_type, file_id, caption)

        type_label = {"photo": "Фото", "video": "Відео", "text": "Текст"}[msg_type]
        telegram.reply(
            chat_id,
            message_id,
            f"{type_label} додано. Заплановано на {_format_ts(slot_ts)}.",
        )
    except Exception as e:
        logger.exception("Failed to enqueue: %s", e)
        telegram.reply(chat_id, message_id, f"Помилка: {e}")
    finally:
        storage.release_lock()


def _handle_stats(message: dict):
    chat_id = message["chat"]["id"]
    message_id = message["message_id"]

    items = storage.get_all_scheduled_formatted()
    if not items:
        telegram.reply(chat_id, message_id, "Немає запланованих постів.")
        return

    lines = [f"Заплановано: {len(items)} пост(ів)."]
    for i, s in enumerate(items, 1):
        lines.append(f"  {i}. {s}")
    telegram.reply(chat_id, message_id, "\n".join(lines))


def _handle_clear(message: dict):
    chat_id = message["chat"]["id"]
    message_id = message["message_id"]
    storage.clear_all()
    telegram.reply(chat_id, message_id, "Чергу очищено.")


def _process_update(update: dict):
    message = update.get("message")
    if not message:
        return

    if not _is_admin(message):
        return

    text = message.get("text")

    if text and text.startswith("/stats"):
        _handle_stats(message)
        return
    if text and text.startswith("/clear"):
        _handle_clear(message)
        return
    if text and text.startswith("/"):
        return

    photos = message.get("photo")
    if photos:
        _enqueue_meme(message, "photo", photos[-1]["file_id"], message.get("caption"))
        return

    video = message.get("video")
    if video:
        _enqueue_meme(message, "video", video["file_id"], message.get("caption"))
        return

    if text:
        _enqueue_meme(message, "text", None, text)


# ---------------------------------------------------------------------------
# Cron: check Redis, post items whose time has come
# ---------------------------------------------------------------------------

def _check_and_post():
    """Post all due items from the queue to the channel."""
    due = storage.get_due_items()
    posted = 0
    for raw_member, data in due:
        try:
            msg_type = data["type"]
            file_id = data.get("file_id") or None
            caption = data.get("caption") or None

            if msg_type == "photo" and file_id:
                telegram.send_photo(CHANNEL_ID, file_id, caption)
            elif msg_type == "video" and file_id:
                telegram.send_video(CHANNEL_ID, file_id, caption)
            else:
                telegram.send_message(CHANNEL_ID, caption or "(без тексту)")

            storage.remove_raw(raw_member)
            posted += 1
        except Exception:
            logger.exception("Failed to post item: %s", data)

    return posted


# ---------------------------------------------------------------------------
# Vercel handler
# ---------------------------------------------------------------------------

class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        """Telegram webhook — save meme to queue, never post to channel."""
        token = self.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if WEBHOOK_SECRET and token != WEBHOOK_SECRET:
            self.send_response(403)
            self.end_headers()
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            update = json.loads(body)
            _process_update(update)
        except Exception:
            logger.exception("Error processing update")

        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        """Cron trigger — GET /api?key=CRON_SECRET posts due items."""
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        key = params.get("key", [""])[0]

        if not CRON_SECRET or key != CRON_SECRET:
            self.send_response(403)
            self.end_headers()
            self.wfile.write(b"Forbidden")
            return

        try:
            posted = _check_and_post()
            body = f"OK. Posted: {posted}".encode()
        except Exception:
            logger.exception("Cron error")
            body = b"Error"

        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass
