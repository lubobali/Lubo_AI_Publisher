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

    Thin accessor over get_todays_posts: returns the day's PRIMARY (first) topic.
    Kept for single-topic callers/tests; the full daily schedule is get_todays_posts.
    """
    posts = get_todays_posts(d)
    if posts:
        return posts[0]["topic"]
    return load_topic_categories()[0]


# Days run Sunday -> Saturday (matches get_week_number's Sunday-start weeks).
WEEKDAY_NAMES = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]
BIOHACKER_KEY = "biohacker"


def get_week_plan(week: int) -> dict[str, list[dict]]:
    """Build the full week's post plan from schedule.yaml `weekly_plan`.

    Returns {weekday_name: [ {topic, window, show_offset}, ... ]}. Biohacker is
    pinned to its slots (3x/week) and each occurrence gets an incrementing
    show_offset (0,1,2) so the 3 posts pull different shows. The 6 non-biohacker
    topics fill the `rotate` slots in week-order and shift by week for variety,
    so each appears exactly once and Monday is not always the same topic.
    """
    plan_cfg = load_schedule_config()["weekly_plan"]
    categories = load_topic_categories()
    bio = next(c for c in categories if c["sources_key"] == BIOHACKER_KEY)
    others = [c for c in categories if c["sources_key"] != BIOHACKER_KEY]

    n = len(others)  # 6
    shift = week % n if n else 0
    rotated = others[shift:] + others[:shift]

    plan: dict[str, list[dict]] = {}
    rotate_idx = 0
    show_offset = 0
    for day in WEEKDAY_NAMES:
        day_posts: list[dict] = []
        for slot in plan_cfg.get(day, []):
            window = slot["window"]
            if slot["topic"] == BIOHACKER_KEY:
                day_posts.append({"topic": bio, "window": window, "show_offset": show_offset})
                show_offset += 1
            else:  # rotate
                day_posts.append({"topic": rotated[rotate_idx % n], "window": window, "show_offset": 0})
                rotate_idx += 1
        plan[day] = day_posts
    return plan


def get_todays_posts(d: date) -> list[dict]:
    """Today's scheduled posts: a list of {topic, window, show_offset} (1 or 2 entries)."""
    week = get_week_number(d)
    weekday = (d.weekday() + 1) % 7  # Sun=0, Mon=1, ..., Sat=6
    return get_week_plan(week)[WEEKDAY_NAMES[weekday]]


def get_random_post_time(
    d: date,
    previous_time: time | None = None,
    min_hour_diff: int = 1,
    window: str | None = None,
) -> time:
    """Generate a random posting time within a window.

    `window` names a posting_windows entry (weekday, weekend, morning,
    late_morning, evening). If omitted, falls back to weekday/weekend by the
    date. If previous_time is given, ensures at least min_hour_diff hours diff.
    """
    config = load_schedule_config()
    if window is None:
        window = "weekend" if d.weekday() >= 5 else "weekday"

    win = config["posting_windows"][window]
    start_hour = win["start_hour"]
    end_hour = win["end_hour"]

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
