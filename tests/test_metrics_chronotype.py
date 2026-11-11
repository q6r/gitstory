"""Chronotype: when in the day the commits happen."""

from __future__ import annotations

from typing import Any

import pytest

from gitstory.metrics.chronotype import MIN_COMMITS, WINDOWS, classify


def spread(**per_hour: int) -> list[int]:
    """A 24-slot histogram from ``h9=3, h22=1`` style arguments."""
    hours = [0] * 24
    for name, count in per_hour.items():
        hours[int(name.removeprefix("h"))] = count
    return hours


def window_hours(window: dict[str, Any]) -> list[int]:
    start, end = window["from"], window["to"]
    return list(range(start, end)) if start < end else [*range(start, 24), *range(0, end)]


def test_the_histogram_must_have_24_slots() -> None:
    for size in (0, 23, 25):
        with pytest.raises(ValueError, match="24 entries"):
            classify([1] * size)


def test_no_commits_means_no_label_and_no_peak() -> None:
    result = classify([0] * 24)
    assert result["type"] is None
    assert result["label"] == "Not enough data"
    assert result["peakHour"] is None
    assert result["total"] == 0
    assert all(w["commits"] == 0 and w["share"] == 0.0 for w in result["windows"])


def test_a_label_needs_a_minimum_number_of_commits() -> None:
    few = classify(spread(h9=MIN_COMMITS - 1))
    assert few["type"] is None
    assert few["label"] == "Not enough data"
    assert few["peakHour"] == 9  # the facts are still reported
    assert few["windows"][1]["commits"] == MIN_COMMITS - 1
    enough = classify(spread(h9=MIN_COMMITS))
    assert enough["type"] == "nine-to-five"
    assert enough["label"] == "Nine-to-five"


@pytest.mark.parametrize(
    ("hour", "expected"),
    [(h, "night-owl") for h in (22, 23, 0, 1, 2, 3, 4)]
    + [(h, "early-bird") for h in (5, 6, 7, 8)]
    + [(h, "nine-to-five") for h in range(9, 18)]
    + [(h, "evening") for h in (18, 19, 20, 21)],
)
def test_every_hour_belongs_to_exactly_one_window(hour: int, expected: str) -> None:
    result = classify(spread(**{f"h{hour}": 20}))
    assert result["type"] == expected
    assert [w["id"] for w in result["windows"] if w["commits"]] == [expected]


def test_the_windows_partition_the_day() -> None:
    covered = [hour for window in classify([1] * 24)["windows"] for hour in window_hours(window)]
    assert sorted(covered) == list(range(24))
    assert [w[0] for w in WINDOWS] == [w["id"] for w in classify([1] * 24)["windows"]]


def test_commit_counts_and_shares_add_up() -> None:
    hours = [(h * 7) % 5 for h in range(24)]
    result = classify(hours)
    assert result["total"] == sum(hours)
    assert sum(w["commits"] for w in result["windows"]) == sum(hours)
    assert sum(w["share"] for w in result["windows"]) == pytest.approx(1.0, abs=0.001)


def test_the_label_follows_the_density_of_a_window_not_its_raw_share() -> None:
    # 60 % of the commits are 9-to-5, but spread over nine hours; the evening has 40 %
    # in four hours, so that is where this author is unusually active.
    hours = spread(**{f"h{h}": 2 for h in range(9, 18)}, **{f"h{h}": 3 for h in range(18, 22)})
    result = classify(hours)
    shares = {w["id"]: w["share"] for w in result["windows"]}
    assert shares["nine-to-five"] > shares["evening"]
    assert result["type"] == "evening"


def test_a_perfectly_even_day_goes_to_the_first_window() -> None:
    assert classify([1] * 24)["type"] == "early-bird"  # ties are resolved by a fixed order


def test_the_peak_hour_is_the_busiest_and_the_earliest_on_a_tie() -> None:
    assert classify(spread(h15=3, h3=3, h20=1))["peakHour"] == 3
    assert classify(spread(h23=9, h0=2))["peakHour"] == 23


def test_the_result_does_not_alias_the_input() -> None:
    hours = spread(h10=12)
    result = classify(hours)
    hours[10] = 0
    assert result["hours"][10] == 12
    assert result["hours"] is not hours


def test_the_night_window_wraps_around_midnight() -> None:
    result = classify(spread(h23=6, h0=3, h4=3, h5=0))
    night = next(w for w in result["windows"] if w["id"] == "night-owl")
    assert (night["from"], night["to"], night["commits"]) == (22, 5, 12)
    assert result["type"] == "night-owl"
