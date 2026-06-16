"""Topic rotator — 7-category weekly rotation with shift, and schedule randomizer."""

import random
from datetime import date, time, timedelta
from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).parent.parent / "config"

# Fixed epoch (a known Sunday) for stable week numbering
_EPOCH = date(2026, 1, 4)


def load_topic_categories() -> list[dict]:
    """Load topic categories from YAML config."""
    with open(CONFIG_DIR / "topics.yaml") as f:
        config = yaml.safe_load(f)
    return config["categories"]


def load_schedule_config() -> dict:
    """Load posting schedule config from YAML."""
    with open(CONFIG_DIR / "schedule.yaml") as f:
        return yaml.safe_load(f)


def get_week_number(d: date) -> int:
    """Get week number for rotation. Weeks start on Sunday.

    Uses a fixed epoch so Sunday-Saturday always share the same week number,
    avoiding the ISO week boundary problem (ISO weeks start on Monday,
    so Sunday belongs to the previous week).
    """
    # Sun=0, Mon=1, ..., Sat=6
    sunday_weekday = (d.weekday() + 1) % 7
    week_start = d - timedelta(days=sunday_weekday)
    return (week_start - _EPOCH).days // 7


def get_todays_topic(d: date) -> dict:
    """Get today's topic category based on weekly rotation.

    Topics shift by 1 position each week. Day of week determines
    which topic within the shifted sequence. Full cycle repeats
    after 7 weeks. Weeks run Sunday-Saturday.
    """
    categories = load_topic_categories()
    n = len(categories)  # 7

    week = get_week_number(d)
    weekday = (d.weekday() + 1) % 7  # Sun=0, Mon=1, ..., Sat=6

    # Shift by week number, wrap around after 7 weeks
    offset = (week % n) + weekday
    index = offset % n

    return categories[index]


def get_random_post_time(
    d: date,
    previous_time: time | None = None,
    min_hour_diff: int = 1,
) -> time:
    """Generate a random posting time within the day's window.

    Windows come from schedule.yaml (weekday 4-5:59 PM CT, weekend 11 PM-midnight CT).
    If previous_time is given, ensures at least min_hour_diff hours difference.
    """
    config = load_schedule_config()
    is_weekend = d.weekday() >= 5

    window = config["posting_windows"]["weekend" if is_weekend else "weekday"]
    start_hour = window["start_hour"]
    end_hour = window["end_hour"]

    # Generate random minute within window (start_hour:00 to end_hour-1:59)
    total_minutes = (end_hour - start_hour) * 60
    max_attempts = 100

    for _ in range(max_attempts):
        offset = random.randint(0, total_minutes - 1)
        hour = start_hour + offset // 60
        minute = offset % 60
        candidate = time(hour, minute)

        if previous_time is None:
            return candidate

        # Check minimum hour difference
        diff = abs((candidate.hour * 60 + candidate.minute) - (previous_time.hour * 60 + previous_time.minute))
        if diff >= min_hour_diff * 60:
            return candidate

    # Fallback: return a time at the opposite end of the window from previous
    if previous_time and previous_time.hour < start_hour + (end_hour - start_hour) // 2:
        return time(end_hour - 1, random.randint(0, 59))
    return time(start_hour, random.randint(0, 59))
