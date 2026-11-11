"""Time-based metrics: series, calendar, heat levels, streaks, gaps and the accumulator."""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta, timezone

import pytest
from builders import commit

from gitstory.metrics import activity
from gitstory.metrics.activity import (
    ActivityAccumulator,
    busiest_day,
    calendar_grid,
    compute_streaks,
    heat_level,
    heat_thresholds,
    longest_gap,
    monthly_series,
    week_start,
    weekly_series,
    yearly_series,
)

D = date.fromisoformat


def days(*texts: str) -> list[date]:
    return [D(t) for t in texts]


# ------------------------------------------------------------------------------- series


def test_week_start_is_the_monday() -> None:
    assert week_start(D("2024-03-06")) == D("2024-03-04")  # a Wednesday
    assert week_start(D("2024-03-04")) == D("2024-03-04")
    assert week_start(D("2024-03-10")) == D("2024-03-04")  # a Sunday


def test_weekly_series_is_dense_and_uses_iso_week_labels() -> None:
    counts = {D("2020-12-30"): 2, D("2021-01-04"): 1, D("2021-01-05"): 3}
    series = weekly_series(counts, D("2021-01-12"))
    assert [row["start"] for row in series] == ["2020-12-28", "2021-01-04", "2021-01-11"]
    assert [row["week"] for row in series] == ["2020-W53", "2021-W01", "2021-W02"]
    assert [row["commits"] for row in series] == [2, 4, 0]  # empty weeks are present, as zeros
    assert weekly_series({}, D("2021-01-01")) == []


def test_series_cover_the_gap_until_the_end_date() -> None:
    counts = {D("2024-01-15"): 1}
    assert [r["month"] for r in monthly_series(counts, D("2024-04-02"))] == [
        "2024-01", "2024-02", "2024-03", "2024-04",
    ]  # fmt: skip
    assert [r["commits"] for r in monthly_series(counts, D("2024-04-02"))] == [1, 0, 0, 0]
    assert yearly_series(counts, D("2026-06-01")) == [
        {"year": 2024, "commits": 1},
        {"year": 2025, "commits": 0},
        {"year": 2026, "commits": 0},
    ]
    assert monthly_series({}, D("2024-01-01")) == []
    assert yearly_series({}, D("2024-01-01")) == []


def test_dense_series_are_bounded_even_for_absurd_dates() -> None:
    counts = {D("1971-01-01"): 1, D("2026-01-01"): 1}
    weeks = weekly_series(counts, D("2026-01-01"))
    months = monthly_series(counts, D("2026-01-01"))
    assert len(weeks) == activity.MAX_WEEKLY_POINTS
    assert len(months) == activity.MAX_MONTHLY_POINTS
    assert weeks[-1]["commits"] == 1
    assert months[-1]["commits"] == 1


# ---------------------------------------------------------------------------- calendar


def test_calendar_covers_53_weeks_monday_first_up_to_as_of() -> None:
    as_of = D("2026-11-10")  # a Tuesday
    counts = {D("2026-11-10"): 3, D("2026-11-09"): 1, D("2025-11-10"): 2, D("2025-11-09"): 9}
    grid = calendar_grid(counts, as_of)
    first = D(grid["from"])
    assert first.weekday() == 0
    assert first == D("2025-11-10")
    assert grid["to"] == "2026-11-10"
    assert grid["weeks"] == 53
    assert grid["weekStart"] == "mon"
    cells = grid["days"]
    assert len(cells) == 52 * 7 + 2
    assert [D(c["date"]) for c in cells] == [first + timedelta(days=i) for i in range(len(cells))]
    assert cells[0]["commits"] == 2
    assert cells[-1]["commits"] == 3
    assert D("2025-11-09").isoformat() not in {c["date"] for c in cells}  # before the window
    assert grid["total"] == 2 + 1 + 3
    assert grid["max"] == 3


def test_calendar_levels_and_empty_history() -> None:
    grid = calendar_grid({}, D("2026-11-10"))
    assert grid["total"] == 0
    assert grid["max"] == 0
    assert {c["level"] for c in grid["days"]} == {0}
    busy = calendar_grid({D("2026-11-10"): 5, D("2026-11-09"): 1}, D("2026-11-10"))
    levels = {c["date"]: c["level"] for c in busy["days"]}
    assert levels["2026-11-08"] == 0
    assert 1 <= levels["2026-11-09"] < levels["2026-11-10"] <= 5
    assert len(calendar_grid({}, D("2026-11-10"), weeks=8)["days"]) == 7 * 7 + 2


# -------------------------------------------------------------------------- heat levels


def test_heat_thresholds_use_quantiles_of_the_non_empty_days() -> None:
    assert heat_thresholds([0, 0]) == (0, 0, 0, 0)
    assert heat_thresholds([4]) == (4, 4, 4, 4)
    cuts = heat_thresholds([0, 0, 1, 1, 1, 2, 3, 5, 8, 40])
    assert list(cuts) == sorted(cuts)
    assert len(cuts) == activity.HEAT_LEVELS - 1


def test_heat_level_is_monotonic_and_bounded() -> None:
    rng = random.Random(7)
    for _ in range(200):
        counts = [rng.choice([0, 0, 1, 2, 3, 5, 9, 30, 100]) for _ in range(rng.randint(1, 60))]
        cuts = heat_thresholds(counts)
        previous = -1
        for count in range(0, max(counts) + 3):
            level = heat_level(count, cuts)
            assert level >= previous
            assert 0 <= level <= activity.HEAT_LEVELS
            previous = level
        assert heat_level(0, cuts) == 0
        if max(counts) > 0:
            assert heat_level(max(counts), cuts) >= 1


# ------------------------------------------------------------------------------ streaks


@pytest.mark.parametrize(
    ("active", "today", "current", "longest"),
    [
        ([], "2026-11-10", 0, 0),
        (["2026-11-10"], "2026-11-10", 1, 1),
        (["2026-11-09"], "2026-11-10", 1, 1),  # yesterday still counts: the day is not over
        (["2026-11-08"], "2026-11-10", 0, 1),  # a whole day without a commit breaks it
        (["2026-11-06", "2026-11-07", "2026-11-08", "2026-11-09"], "2026-11-10", 4, 4),
        (
            ["2026-11-01", "2026-11-02", "2026-11-04", "2026-11-05", "2026-11-06"],
            "2026-11-06",
            3,
            3,
        ),
        (["2026-11-01", "2026-11-02", "2026-11-03", "2026-11-05"], "2026-11-05", 1, 3),
        (["2026-11-11"], "2026-11-10", 1, 1),  # one day ahead: timezone skew is tolerated
        (["2026-11-12"], "2026-11-10", 0, 0),  # further ahead is clock skew and ignored
        (["2026-11-10", "2026-11-10", "2026-11-10"], "2026-11-10", 1, 1),  # duplicates
    ],
)
def test_streak_table(active: list[str], today: str, current: int, longest: int) -> None:
    result = compute_streaks([D(d) for d in active], D(today))
    assert (result.current, result.longest) == (current, longest)


def test_streak_dates_and_ties_go_to_the_earliest_run() -> None:
    result = compute_streaks(
        days("2026-01-01", "2026-01-02", "2026-02-01", "2026-02-02"), D("2026-03-01")
    )
    assert (result.longest, result.longest_start, result.longest_end) == (
        2,
        D("2026-01-01"),
        D("2026-01-02"),
    )
    assert result.current == 0
    assert result.current_start is None
    assert result.days_since_last == 27
    alive = compute_streaks(days("2026-11-08", "2026-11-09"), D("2026-11-10"))
    assert alive.current_start == D("2026-11-08")
    assert alive.days_since_last == 1
    assert alive.as_dict()["currentFrom"] == "2026-11-08"
    assert alive.as_dict()["longestTo"] == "2026-11-09"


def naive_streaks(active: set[date], today: date) -> tuple[int, int]:
    """Brute-force reference: walk every calendar day instead of computing runs."""
    horizon = today + timedelta(days=1)
    usable = {d for d in active if d <= horizon}
    if not usable:
        return 0, 0
    longest = 0
    for d in usable:
        length = 1
        while d - timedelta(days=length) in usable:
            length += 1
        longest = max(longest, length)
    last = max(usable)
    current = 0
    if (today - last).days <= 1:
        current = 1
        while last - timedelta(days=current) in usable:
            current += 1
    return current, longest


@pytest.mark.parametrize("seed", range(40))
def test_streaks_agree_with_a_brute_force_reference(seed: int) -> None:
    rng = random.Random(seed)
    today = D("2026-06-15")
    density = rng.choice([0.1, 0.3, 0.6, 0.9])
    active = {today - timedelta(days=i) for i in range(-3, 120) if rng.random() < density}
    result = compute_streaks(active, today)
    assert (result.current, result.longest) == naive_streaks(active, today)
    # properties that hold for any input
    assert (
        result.current
        <= result.longest
        <= max(1, len({d for d in active if d <= today + timedelta(days=1)}))
    )
    shuffled = list(active)
    rng.shuffle(shuffled)
    assert (
        compute_streaks(shuffled + shuffled, today) == result
    )  # order and duplicates do not matter


def test_adding_a_day_never_shortens_the_longest_streak() -> None:
    rng = random.Random(99)
    today = D("2026-06-15")
    active: set[date] = set()
    longest = 0
    for _ in range(80):
        active.add(today - timedelta(days=rng.randint(0, 60)))
        now = compute_streaks(active, today).longest
        assert now >= longest
        longest = now


def test_a_contiguous_block_ending_today_is_both_current_and_longest() -> None:
    for length in (1, 2, 7, 30):
        block = [D("2026-06-15") - timedelta(days=i) for i in range(length)]
        result = compute_streaks(block, D("2026-06-15"))
        assert result.current == result.longest == length


# ----------------------------------------------------------------------- gaps and records


def test_longest_gap_counts_quiet_days_between_active_days() -> None:
    assert longest_gap(days("2026-01-01", "2026-01-02")) is None
    assert longest_gap(days("2026-01-01")) is None
    assert longest_gap([]) is None
    assert longest_gap(days("2026-01-01", "2026-01-05", "2026-01-06")) == (
        3,
        D("2026-01-01"),
        D("2026-01-05"),
    )
    # Jan 1 -> Jan 4 and Jan 5 -> Jan 8 are both two quiet days: a real tie, the earliest wins
    tie = longest_gap(days("2026-01-01", "2026-01-04", "2026-01-05", "2026-01-08"))
    assert tie == (2, D("2026-01-01"), D("2026-01-04"))
    # ... whatever order the days arrive in
    shuffled = longest_gap(days("2026-01-08", "2026-01-01", "2026-01-05", "2026-01-04"))
    assert shuffled == tie
    # a strictly longer later gap still beats an earlier one
    later = longest_gap(days("2026-01-01", "2026-01-04", "2026-02-01", "2026-02-04"))
    assert later == (27, D("2026-01-04"), D("2026-02-01"))
    assert longest_gap(days("2026-01-05", "2026-01-01", "2026-01-01")) == (
        3,
        D("2026-01-01"),
        D("2026-01-05"),
    )


def test_busiest_day_ties_go_to_the_earliest() -> None:
    assert busiest_day({}) is None
    assert busiest_day({D("2026-01-02"): 4, D("2026-01-01"): 4, D("2026-01-03"): 3}) == (
        D("2026-01-01"),
        4,
    )


# ----------------------------------------------------------------------- accumulator


def test_commits_belong_to_the_authors_local_day_hour_and_weekday() -> None:
    acc = ActivityAccumulator()
    # Friday 2024-03-01 23:30 at UTC-03:00 is already Saturday in UTC
    acc.add(commit("2024-03-01T23:30:00-03:00"))
    # Monday 2024-03-04 00:15 at UTC+09:00 is still Sunday in UTC
    acc.add(commit("2024-03-04T00:15:00+09:00"))
    assert dict(acc.days) == {D("2024-03-01"): 1, D("2024-03-04"): 1}
    assert acc.matrix[4][23] == 1  # Friday, hour 23
    assert acc.matrix[0][0] == 1  # Monday, hour 0
    assert acc.hours[23] == 1
    assert acc.hours[0] == 1
    assert sum(acc.hours) == 2


def test_first_and_last_are_absolute_instants() -> None:
    acc = ActivityAccumulator()
    acc.add(commit("2024-03-01T23:30:00-03:00"))  # 2024-03-02T02:30Z
    acc.add(commit("2024-03-02T01:00:00+00:00"))  # earlier instant, later local clock reading
    assert acc.first == datetime(2024, 3, 2, 1, 0, tzinfo=timezone.utc)
    assert acc.last == datetime(2024, 3, 2, 2, 30, tzinfo=timezone.utc)
    assert acc.totals_section()["spanDays"] == 1


def test_today_follows_the_utc_date_but_tolerates_an_author_already_in_tomorrow() -> None:
    acc = ActivityAccumulator()
    now = datetime(2026, 11, 9, 22, 0, tzinfo=timezone.utc)
    assert acc.as_of_day(now) == D("2026-11-09")
    acc.add(commit("2026-11-10T07:30:00+13:00"))  # a New Zealand morning, still Nov 9 in UTC
    assert acc.as_of_day(now) == D("2026-11-10")
    far = ActivityAccumulator()
    far.add(commit("2026-11-20T07:30:00+00:00"))
    assert far.as_of_day(now) == D("2026-11-09")  # clock-skewed commit: ignored


def test_sections_of_an_empty_accumulator_are_well_formed() -> None:
    acc = ActivityAccumulator()
    today = D("2026-11-10")
    section = acc.activity_section(today)
    assert section["byDay"] == []
    assert section["byWeek"] == []
    assert section["byYear"] == []
    assert section["weekdayHour"]["max"] == 0
    assert len(section["weekdayHour"]["matrix"]) == 7
    assert acc.streak_section(today)["current"] == 0
    assert acc.totals_section() == {
        "activeDays": 0,
        "firstCommitAt": None,
        "lastCommitAt": None,
        "spanDays": 0,
        "commitsPerActiveDay": 0.0,
    }
    assert acc.records_section() == {"busiestDay": None, "longestGap": None}
