"""Calculate the next available posting slot respecting working hours, daily limits, and gaps."""

import random
from datetime import datetime, timedelta

import pytz

from lib.config import (
    MAX_DAILY_POSTS,
    MAX_GAP_HOURS,
    MIN_GAP_HOURS,
    TZ_NAME,
    WORK_END_HOUR,
    WORK_START_HOUR,
)

TZ = pytz.timezone(TZ_NAME)

# Telegram requires schedule_date to be at least this many seconds in the future.
_MIN_FUTURE_SECONDS = 100


def _slots_on_date(slots: list[float], date) -> int:
    """Count how many slots fall on a given Kyiv calendar date."""
    count = 0
    for ts in slots:
        dt = datetime.fromtimestamp(ts, tz=TZ)
        if dt.date() == date:
            count += 1
    return count


def _clamp_to_working_hours(dt: datetime) -> datetime:
    """If dt is outside working hours, push to next valid window."""
    if dt.hour < WORK_START_HOUR:
        dt = dt.replace(hour=WORK_START_HOUR, minute=0, second=0, microsecond=0)
        dt += timedelta(minutes=random.randint(0, 30))
    elif dt.hour >= WORK_END_HOUR:
        # Push to next day 09:00
        dt = (dt + timedelta(days=1)).replace(
            hour=WORK_START_HOUR, minute=0, second=0, microsecond=0
        )
        dt += timedelta(minutes=random.randint(0, 30))
    return dt


def find_next_slot(future_slots: list[float]) -> int:
    """Return a Unix timestamp for the next valid posting slot.

    Args:
        future_slots: list of Unix timestamps of already-scheduled future posts.

    Returns:
        Unix timestamp (int) suitable for Telegram's schedule_date parameter.
    """
    now = datetime.now(TZ)

    # Determine candidate based on existing slots
    if not future_slots:
        candidate = now
    else:
        last_slot = max(future_slots)
        gap = random.uniform(MIN_GAP_HOURS, MAX_GAP_HOURS)
        candidate = datetime.fromtimestamp(last_slot, tz=TZ) + timedelta(hours=gap)

    # Clamp to working hours
    candidate = _clamp_to_working_hours(candidate)

    # Check daily limit — if exceeded, roll to next day(s)
    for _ in range(365):
        day_count = _slots_on_date(future_slots, candidate.date())
        if day_count < MAX_DAILY_POSTS:
            break
        # Roll to next day
        candidate = (candidate + timedelta(days=1)).replace(
            hour=WORK_START_HOUR, minute=0, second=0, microsecond=0
        )
        candidate += timedelta(minutes=random.randint(0, 30))
    else:
        # Fallback — should never happen in practice
        candidate = now + timedelta(days=1)

    # Ensure candidate is far enough in the future for Telegram
    min_future = now + timedelta(seconds=_MIN_FUTURE_SECONDS)
    if candidate < min_future:
        candidate = min_future

    # Re-clamp after possible min_future adjustment
    candidate = _clamp_to_working_hours(candidate)

    return int(candidate.timestamp())
