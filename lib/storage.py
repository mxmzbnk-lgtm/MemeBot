"""Vercel KV (Upstash Redis) storage for scheduled post slots."""

import time

from upstash_redis import Redis

from lib.config import KV_REST_API_URL, KV_REST_API_TOKEN

_KEY = "scheduled_slots"
_LOCK_KEY = "slot_lock"
_LOCK_TTL = 5  # seconds


def _redis() -> Redis:
    return Redis(url=KV_REST_API_URL, token=KV_REST_API_TOKEN)


def acquire_lock() -> bool:
    """Try to acquire a distributed lock. Returns True if acquired."""
    r = _redis()
    result = r.set(_LOCK_KEY, "1", nx=True, ex=_LOCK_TTL)
    return result is not None


def release_lock():
    """Release the distributed lock."""
    _redis().delete(_LOCK_KEY)


def get_future_slots() -> list[float]:
    """Return all scheduled slots that are still in the future, sorted ascending."""
    r = _redis()
    now = time.time()
    # Clean up past slots
    r.zremrangebyscore(_KEY, "-inf", now)
    # Get remaining future slots
    results = r.zrange(_KEY, now, "+inf", byscore=True)
    return [float(ts) for ts in results]


def add_slot(timestamp: float):
    """Add a scheduled slot timestamp."""
    _redis().zadd(_KEY, {str(timestamp): timestamp})


def count_future() -> int:
    """Count the number of future scheduled slots."""
    now = time.time()
    r = _redis()
    r.zremrangebyscore(_KEY, "-inf", now)
    return r.zcount(_KEY, now, "+inf")


def clear_all():
    """Remove all scheduled slots."""
    _redis().delete(_KEY)


def get_all_slots_formatted() -> list[str]:
    """Return all future slots as formatted date strings (DD.MM о HH:MM)."""
    import pytz
    from datetime import datetime
    from lib.config import TZ_NAME

    tz = pytz.timezone(TZ_NAME)
    slots = get_future_slots()
    result = []
    for ts in sorted(slots):
        dt = datetime.fromtimestamp(ts, tz=tz)
        result.append(dt.strftime("%d.%m о %H:%M"))
    return result
