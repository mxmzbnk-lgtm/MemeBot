"""Vercel serverless function — Telegram webhook handler."""

import json
import logging
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from typing import Optional

import pytz

# Ensure project root is on the path so `lib` is importable.
sys.path.insert(0, __import__("os").path.join(__import__("os").path.dirname(__file__), ".."))

from lib.config import ADMIN_ID, CHANNEL_ID, TZ_NAME, WEBHOOK_SECRET
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


def _text(message: dict) -> Optional[str]:
    return message.get("text")


def _format_slot(ts: int) -> str:
    dt = datetime.fromtimestamp(ts, tz=TZ)
    return dt.strftime("%d.%m о %H:%M")


def _schedule_and_send(message: dict, msg_type: str, file_id: Optional[str], caption: Optional[str]):
    """Calculate slot, send to channel with schedule_date, reply to admin."""
    chat_id = message["chat"]["id"]
    message_id = message["message_id"]

    if not storage.acquire_lock():
        telegram.reply(chat_id, message_id, "Зачекайте секунду і спробуйте знову.")
        return

    try:
        future_slots = storage.get_future_slots()
        slot_ts = find_next_slot(future_slots)

        if msg_type == "photo":
            telegram.send_photo(CHANNEL_ID, file_id, caption, schedule_date=slot_ts)
        elif msg_type == "video":
            telegram.send_video(CHANNEL_ID, file_id, caption, schedule_date=slot_ts)
        else:
            telegram.send_message(CHANNEL_ID, caption or "(без тексту)", schedule_date=slot_ts)

        storage.add_slot(float(slot_ts))

        type_label = {"photo": "Фото", "video": "Відео", "text": "Текст"}[msg_type]
        telegram.reply(
            chat_id,
            message_id,
            f"{type_label} додано. Заплановано на {_format_slot(slot_ts)}.",
        )
    except Exception as e:
        logger.exception("Failed to schedule post: %s", e)
        telegram.reply(chat_id, message_id, f"Помилка: {e}")
    finally:
        storage.release_lock()


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _handle_stats(message: dict):
    chat_id = message["chat"]["id"]
    message_id = message["message_id"]

    slots = storage.get_all_slots_formatted()
    if not slots:
        telegram.reply(chat_id, message_id, "Немає запланованих постів.")
        return

    lines = [f"Заплановано: {len(slots)} пост(ів)."]
    for i, s in enumerate(slots, 1):
        lines.append(f"  {i}. {s}")
    telegram.reply(chat_id, message_id, "\n".join(lines))


def _handle_clear(message: dict):
    chat_id = message["chat"]["id"]
    message_id = message["message_id"]

    storage.clear_all()
    telegram.reply(
        chat_id,
        message_id,
        "Слоти очищено.\nУвага: вже відправлені в Telegram пости все одно вийдуть.",
    )


# ---------------------------------------------------------------------------
# Main router
# ---------------------------------------------------------------------------

def _process_update(update: dict):
    message = update.get("message")
    if not message:
        return

    if not _is_admin(message):
        return

    text = _text(message)

    # Commands
    if text and text.startswith("/stats"):
        _handle_stats(message)
        return
    if text and text.startswith("/clear"):
        _handle_clear(message)
        return
    if text and text.startswith("/"):
        # Ignore unknown commands
        return

    # Photo
    photos = message.get("photo")
    if photos:
        file_id = photos[-1]["file_id"]  # highest resolution
        _schedule_and_send(message, "photo", file_id, message.get("caption"))
        return

    # Video
    video = message.get("video")
    if video:
        _schedule_and_send(message, "video", video["file_id"], message.get("caption"))
        return

    # Text
    if text:
        _schedule_and_send(message, "text", None, text)


# ---------------------------------------------------------------------------
# Vercel handler
# ---------------------------------------------------------------------------

class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        # Verify webhook secret
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
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"MemeBot webhook is running.")

    def log_message(self, format, *args):
        # Suppress default stderr logging in serverless
        pass
