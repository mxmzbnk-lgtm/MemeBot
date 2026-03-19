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


def _slots_on_date(slots: list[float], date) -> int:
    count = 0
    for ts in slots:
        dt = datetime.fromtimestamp(ts, tz=TZ)
        if dt.date() == date:
            count += 1
    return count


def _clamp_to_working_hours(dt: datetime) -> datetime:
    if dt.hour < WORK_START_HOUR:
        dt = dt.replace(hour=WORK_START_HOUR, minute=0, second=0, microsecond=0)
        dt += timedelta(minutes=random.randint(0, 30))
    elif dt.hour >= WORK_END_HOUR:
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
        Unix timestamp (int).
    """
    now = datetime.now(TZ)

    if not future_slots:
        candidate = now + timedelta(minutes=random.randint(3, 10))
    else:
        last_slot = max(future_slots)
        gap = random.uniform(MIN_GAP_HOURS, MAX_GAP_HOURS)
        candidate = datetime.fromtimestamp(last_slot, tz=TZ) + timedelta(hours=gap)

    candidate = _clamp_to_working_hours(candidate)

    for _ in range(365):
        day_count = _slots_on_date(future_slots, candidate.date())
        if day_count < MAX_DAILY_POSTS:
            break
        candidate = (candidate + timedelta(days=1)).replace(
            hour=WORK_START_HOUR, minute=0, second=0, microsecond=0
        )
        candidate += timedelta(minutes=random.randint(0, 30))

    # Ensure slot is in the future
    min_future = now + timedelta(minutes=2)
    if candidate < min_future:
        candidate = min_future
        candidate = _clamp_to_working_hours(candidate)

    return int(candidate.timestamp())
