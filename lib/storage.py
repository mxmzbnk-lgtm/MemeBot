"""Vercel KV (Upstash Redis) storage for scheduled meme posts."""

import json
import time
from datetime import datetime

import pytz

from upstash_redis import Redis

from lib.config import KV_REST_API_URL, KV_REST_API_TOKEN, TZ_NAME

_QUEUE_KEY = "meme_queue"
_LOCK_KEY = "slot_lock"
_LOCK_TTL = 5  # seconds

TZ = pytz.timezone(TZ_NAME)


def _redis() -> Redis:
    return Redis(url=KV_REST_API_URL, token=KV_REST_API_TOKEN)


# ---------------------------------------------------------------------------
# Distributed lock (protects against concurrent album uploads)
# ---------------------------------------------------------------------------

def acquire_lock() -> bool:
    r = _redis()
    result = r.set(_LOCK_KEY, "1", nx=True, ex=_LOCK_TTL)
    return result is not None


def release_lock():
    _redis().delete(_LOCK_KEY)


# ---------------------------------------------------------------------------
# Queue: sorted set, score = publish Unix timestamp,
#         member = JSON {"type", "file_id", "caption", "uid"}
# uid makes each member unique even if file_id/caption repeat.
# ---------------------------------------------------------------------------

def enqueue(publish_ts: float, msg_type: str, file_id: str | None, caption: str | None):
    """Add a meme to the queue, scheduled for publish_ts."""
    uid = f"{time.time_ns()}"
    member = json.dumps({
        "type": msg_type,
        "file_id": file_id or "",
        "caption": caption or "",
        "uid": uid,
    })
    _redis().zadd(_QUEUE_KEY, {member: publish_ts})


def get_due_items() -> list[tuple[str, dict]]:
    """Return items whose publish time has passed (score <= now).

    Returns list of (raw_member, data_dict). Items are NOT removed —
    call remove_raw() after successful posting.
    """
    r = _redis()
    now = time.time()
    results = r.zrangebyscore(_QUEUE_KEY, "-inf", now)
    items = []
    for member in results:
        raw = member if isinstance(member, str) else str(member)
        data = json.loads(raw)
        items.append((raw, data))
    return items


def remove_raw(raw_member: str):
    """Remove a specific member string from the queue."""
    _redis().zrem(_QUEUE_KEY, raw_member)


def get_future_slots() -> list[float]:
    """Return publish timestamps of all future items (for slot calculation)."""
    r = _redis()
    now = time.time()
    members = r.zrangebyscore(_QUEUE_KEY, now, "+inf")
    slots = []
    for member in members:
        raw = member if isinstance(member, str) else str(member)
        score = r.zscore(_QUEUE_KEY, raw)
        if score is not None:
            slots.append(float(score))
    return sorted(slots)


def get_all_scheduled_formatted() -> list[str]:
    """Return all future items as formatted strings for /stats."""
    r = _redis()
    now = time.time()
    members = r.zrangebyscore(_QUEUE_KEY, now, "+inf")
    items = []
    for member in members:
        raw = member if isinstance(member, str) else str(member)
        data = json.loads(raw)
        score = r.zscore(_QUEUE_KEY, raw)
        if score is not None:
            items.append((float(score), data))
    items.sort(key=lambda x: x[0])
    result = []
    for ts, data in items:
        dt = datetime.fromtimestamp(ts, tz=TZ)
        label = {"photo": "Фото", "video": "Відео", "text": "Текст"}.get(data["type"], "?")
        result.append(f"{dt.strftime('%d.%m о %H:%M')} — {label}")
    return result


def clear_all():
    """Remove all scheduled items."""
    _redis().delete(_QUEUE_KEY)
