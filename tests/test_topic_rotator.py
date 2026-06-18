"""Tests for topic rotator — weekly rotation with shift, and schedule randomizer."""

from datetime import date, time, timedelta

import pytest

from src.topic_rotator import (
    get_random_post_time,
    get_todays_topic,
    get_week_number,
    load_schedule_config,
    load_topic_categories,
)

# ---------------------------------------------------------------------------
# Topic loading
# ---------------------------------------------------------------------------


class TestLoadTopics:
    def test_loads_seven_categories(self):
        categories = load_topic_categories()
        assert len(categories) == 7

    def test_each_category_has_name_and_sources_key(self):
        categories = load_topic_categories()
        for cat in categories:
            assert "name" in cat
            assert "sources_key" in cat

    def test_known_categories_present(self):
        categories = load_topic_categories()
        names = [c["name"] for c in categories]
        assert "AI News" in names
        assert "Biohacker" in names
        assert "My Agent Build" in names


# ---------------------------------------------------------------------------
# Week number calculation
# ---------------------------------------------------------------------------


class TestWeekNumber:
    def test_same_week_returns_same_number(self):
        sunday = date(2026, 3, 22)  # Sunday — start of week
        saturday = date(2026, 3, 28)  # Saturday — end of same week
        assert get_week_number(sunday) == get_week_number(saturday)

    def test_different_weeks_return_different_numbers(self):
        week1 = date(2026, 3, 23)
        week2 = date(2026, 3, 30)
        assert get_week_number(week1) != get_week_number(week2)

    def test_week_number_increments_by_one(self):
        week1 = date(2026, 3, 23)
        week2 = date(2026, 3, 30)
        assert get_week_number(week2) == get_week_number(week1) + 1


# ---------------------------------------------------------------------------
# Topic rotation — shift by 1 each week
# ---------------------------------------------------------------------------


class TestGetTodaysTopic:
    def test_returns_category_dict(self):
        topic = get_todays_topic(date(2026, 3, 23))
        assert "name" in topic
        assert "sources_key" in topic

    def test_same_day_always_same_topic(self):
        d = date(2026, 3, 23)
        assert get_todays_topic(d) == get_todays_topic(d)

    def test_different_days_same_week_different_topics(self):
        """Monday and Tuesday of same week should have different topics."""
        monday = date(2026, 3, 23)
        tuesday = date(2026, 3, 24)
        assert get_todays_topic(monday) != get_todays_topic(tuesday)

    def test_seven_days_cover_all_topics(self):
        """A full week (Sun-Sat) should cover all 7 categories."""
        sunday = date(2026, 3, 22)  # Sunday — start of calendar week
        topics = [get_todays_topic(sunday + timedelta(days=i)) for i in range(7)]
        names = [t["name"] for t in topics]
        assert len(set(names)) == 7, f"Duplicate categories in week: {names}"

    def test_sunday_saturday_same_week_different_topics(self):
        """Regression: Sunday and Saturday must NOT collide (ISO week bug)."""
        sunday = date(2026, 3, 22)
        saturday = date(2026, 3, 28)
        assert get_todays_topic(sunday) != get_todays_topic(saturday)

    @pytest.mark.parametrize("weeks_ahead", range(8))
    def test_every_sun_sat_week_has_unique_topics(self, weeks_ahead):
        """Every Sun-Sat week should cover all 7 categories."""
        sunday = date(2026, 3, 22) + timedelta(weeks=weeks_ahead)
        topics = [get_todays_topic(sunday + timedelta(days=i)) for i in range(7)]
        names = [t["name"] for t in topics]
        assert len(set(names)) == 7, f"Duplicates in week of {sunday}: {names}"

    def test_rotation_shifts_between_weeks(self):
        """Monday of week 2 should not be the same topic as Monday of week 1."""
        week1_monday = date(2026, 3, 23)
        week2_monday = date(2026, 3, 30)
        assert get_todays_topic(week1_monday) != get_todays_topic(week2_monday)

    def test_full_cycle_repeats_after_7_weeks(self):
        """After 7 weeks, the rotation should repeat."""
        d = date(2026, 3, 23)
        cycle1 = get_todays_topic(d)
        cycle2 = get_todays_topic(d + timedelta(weeks=7))
        assert cycle1 == cycle2

    def test_all_days_in_7_week_cycle_are_valid(self):
        """Every day in a 7-week cycle maps to a valid topic."""
        start = date(2026, 3, 23)
        categories = load_topic_categories()
        valid_names = {c["name"] for c in categories}
        for i in range(49):
            topic = get_todays_topic(start + timedelta(days=i))
            assert topic["name"] in valid_names


# ---------------------------------------------------------------------------
# Schedule config loading
# ---------------------------------------------------------------------------


class TestLoadSchedule:
    def test_loads_posting_windows(self):
        config = load_schedule_config()
        assert "posting_windows" in config
        assert "weekday" in config["posting_windows"]
        assert "weekend" in config["posting_windows"]

    def test_weekday_hours(self):
        config = load_schedule_config()
        weekday = config["posting_windows"]["weekday"]
        assert weekday["start_hour"] == 15
        assert weekday["end_hour"] == 17

    def test_weekend_hours(self):
        config = load_schedule_config()
        weekend = config["posting_windows"]["weekend"]
        assert weekend["start_hour"] == 23
        assert weekend["end_hour"] == 24


# ---------------------------------------------------------------------------
# Random posting time
# ---------------------------------------------------------------------------


class TestGetRandomPostTime:
    def test_weekday_within_window(self):
        """Weekday post time should be between 3-4:59 PM CT (3-5 PM window)."""
        d = date(2026, 3, 23)  # Monday
        t = get_random_post_time(d)
        assert t.hour >= 15
        assert t.hour < 17

    def test_weekend_within_window(self):
        """Weekend post time should be between 11 PM - midnight CT."""
        d = date(2026, 3, 28)  # Saturday
        t = get_random_post_time(d)
        assert t.hour >= 23
        assert t.hour < 24

    def test_returns_time_object(self):
        t = get_random_post_time(date(2026, 3, 23))
        assert isinstance(t, time)

    def test_respects_min_hour_difference(self):
        """When previous time is given, new time should differ by >= 1 hour."""
        prev = time(17, 30)
        # Run 50 times to check stochastically
        for _ in range(50):
            t = get_random_post_time(date(2026, 3, 24), previous_time=prev)
            diff_minutes = abs((t.hour * 60 + t.minute) - (prev.hour * 60 + prev.minute))
            assert diff_minutes >= 60

    def test_different_seeds_different_times(self):
        """Two calls should generally produce different times (not deterministic)."""
        d = date(2026, 3, 23)
        times = {get_random_post_time(d) for _ in range(20)}
        # With 20 attempts, we should get at least 2 different times
        assert len(times) >= 2

    @pytest.mark.parametrize(
        "d",
        [
            date(2026, 3, 23),  # Monday
            date(2026, 3, 24),  # Tuesday
            date(2026, 3, 25),  # Wednesday
            date(2026, 3, 26),  # Thursday
            date(2026, 3, 27),  # Friday
        ],
    )
    def test_all_weekdays_use_weekday_window(self, d):
        t = get_random_post_time(d)
        assert 15 <= t.hour < 17

    @pytest.mark.parametrize(
        "d",
        [
            date(2026, 3, 28),  # Saturday
            date(2026, 3, 29),  # Sunday
        ],
    )
    def test_all_weekends_use_weekend_window(self, d):
        t = get_random_post_time(d)
        assert 23 <= t.hour < 24
