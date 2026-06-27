"""Tests for topic rotator — weekly rotation with shift, and schedule randomizer."""

from datetime import date, time, timedelta

import pytest

from src.topic_rotator import (
    get_random_post_time,
    get_todays_posts,
    get_todays_topic,
    get_week_number,
    get_week_plan,
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
    """get_todays_topic = the day's PRIMARY (first) topic (thin accessor over the plan)."""

    def test_returns_category_dict(self):
        topic = get_todays_topic(date(2026, 3, 23))
        assert "name" in topic
        assert "sources_key" in topic

    def test_same_day_always_same_topic(self):
        d = date(2026, 3, 23)
        assert get_todays_topic(d) == get_todays_topic(d)

    def test_biohacker_is_primary_on_sun_wed_fri(self):
        sunday = date(2026, 3, 22)  # Sunday
        for offset in (0, 3, 5):  # Sun, Wed, Fri
            assert get_todays_topic(sunday + timedelta(days=offset))["sources_key"] == "biohacker"

    def test_other_days_are_not_biohacker(self):
        sunday = date(2026, 3, 22)
        for offset in (1, 2, 4, 6):  # Mon, Tue, Thu, Sat
            assert get_todays_topic(sunday + timedelta(days=offset))["sources_key"] != "biohacker"

    def test_all_days_map_to_valid_topics(self):
        start = date(2026, 3, 22)
        valid_names = {c["name"] for c in load_topic_categories()}
        for i in range(42):
            assert get_todays_topic(start + timedelta(days=i))["name"] in valid_names


# ---------------------------------------------------------------------------
# Weekly plan — 9 posts/week (Phase 2.19): biohacker 3x, others 1x
# ---------------------------------------------------------------------------


class TestWeeklyPlan:
    SUNDAY = date(2026, 3, 22)

    def _week_posts(self, sunday=SUNDAY):
        posts = []
        for i in range(7):
            posts += get_todays_posts(sunday + timedelta(days=i))
        return posts

    def test_nine_posts_per_week(self):
        assert len(self._week_posts()) == 9

    def test_biohacker_three_times(self):
        bio = [p for p in self._week_posts() if p["topic"]["sources_key"] == "biohacker"]
        assert len(bio) == 3

    def test_each_other_topic_exactly_once(self):
        others = [p["topic"]["sources_key"] for p in self._week_posts() if p["topic"]["sources_key"] != "biohacker"]
        assert len(others) == 6
        assert len(set(others)) == 6

    def test_biohacker_lands_on_sun_wed_fri(self):
        bio_days = [
            i
            for i in range(7)
            if any(p["topic"]["sources_key"] == "biohacker" for p in get_todays_posts(self.SUNDAY + timedelta(days=i)))
        ]
        assert bio_days == [0, 3, 5]

    def test_double_days_are_sun_and_wed(self):
        counts = [len(get_todays_posts(self.SUNDAY + timedelta(days=i))) for i in range(7)]
        assert counts == [2, 1, 1, 2, 1, 1, 1]

    def test_biohacker_posts_have_distinct_show_offsets(self):
        offsets = sorted(p["show_offset"] for p in self._week_posts() if p["topic"]["sources_key"] == "biohacker")
        assert offsets == [0, 1, 2]

    def test_rotation_shifts_between_weeks(self):
        w1 = get_todays_posts(date(2026, 3, 23))[0]["topic"]["sources_key"]  # Mon week 1
        w2 = get_todays_posts(date(2026, 3, 30))[0]["topic"]["sources_key"]  # Mon week 2
        assert w1 != w2

    @pytest.mark.parametrize("weeks_ahead", range(8))
    def test_every_week_is_complete(self, weeks_ahead):
        posts = self._week_posts(self.SUNDAY + timedelta(weeks=weeks_ahead))
        keys = [p["topic"]["sources_key"] for p in posts]
        assert len(keys) == 9
        assert keys.count("biohacker") == 3
        non_bio = [k for k in keys if k != "biohacker"]
        assert len(set(non_bio)) == 6  # all 6 other topics, once each

    def test_windows_assigned(self):
        windows = {p["window"] for p in self._week_posts()}
        assert {"morning", "late_morning", "evening", "weekday", "weekend"} <= windows

    def test_week_plan_matches_todays_posts(self):
        plan = get_week_plan(get_week_number(self.SUNDAY))
        assert plan["sun"] == get_todays_posts(self.SUNDAY)


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
        assert weekend["start_hour"] == 11
        assert weekend["end_hour"] == 13


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
        """Weekend post time should be between 11 AM - 12:59 PM CT."""
        d = date(2026, 3, 28)  # Saturday
        t = get_random_post_time(d)
        assert t.hour >= 11
        assert t.hour < 13

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
        assert 11 <= t.hour < 13

    @pytest.mark.parametrize(
        "window,lo,hi",
        [("morning", 6, 8), ("late_morning", 10, 12), ("evening", 19, 21)],
    )
    def test_explicit_window_overrides_date(self, window, lo, hi):
        # A weekday date but an explicit window -> uses that window, not weekday 15-17
        t = get_random_post_time(date(2026, 3, 23), window=window)
        assert lo <= t.hour < hi
