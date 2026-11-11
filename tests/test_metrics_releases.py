"""Release cadence from tags."""

from __future__ import annotations

import random

from builders import tag, when

from gitstory.metrics.releases import MAX_LISTED, release_cadence

AS_OF = when("2024-02-10T00:00:00+00:00")


def test_no_tags_no_releases() -> None:
    assert release_cadence([], AS_OF) == {
        "count": 0,
        "annotated": 0,
        "meanIntervalDays": None,
        "medianIntervalDays": None,
        "latest": None,
        "perYear": [],
        "items": [],
    }


def test_a_single_tag_has_no_interval() -> None:
    result = release_cadence([tag("v1.0.0", "2024-02-01T00:00:00+00:00", annotated=True)], AS_OF)
    assert result["count"] == 1
    assert result["annotated"] == 1
    assert result["meanIntervalDays"] is None
    assert result["latest"] == {"name": "v1.0.0", "date": "2024-02-01T00:00:00Z", "daysAgo": 9}
    assert result["items"] == [
        {
            "name": "v1.0.0",
            "date": "2024-02-01T00:00:00Z",
            "annotated": True,
            "daysSincePrevious": None,
        }
    ]


def test_intervals_are_measured_between_consecutive_releases() -> None:
    tags = [
        tag("v1", "2024-01-01T00:00:00+00:00"),
        tag("v2", "2024-01-11T00:00:00+00:00", annotated=True),
        tag("v3", "2024-01-31T00:00:00+00:00"),
    ]
    result = release_cadence(tags, AS_OF)
    assert [i["daysSincePrevious"] for i in result["items"]] == [None, 10.0, 20.0]
    assert result["meanIntervalDays"] == 15.0
    assert result["medianIntervalDays"] == 15.0
    assert result["annotated"] == 1
    assert result["latest"]["name"] == "v3"
    assert result["latest"]["daysAgo"] == 10


def test_the_median_differs_from_the_mean_for_uneven_releases() -> None:
    tags = [
        tag("a", "2024-01-01T00:00:00+00:00"),
        tag("b", "2024-01-02T00:00:00+00:00"),  # 1 day
        tag("c", "2024-01-04T00:00:00+00:00"),  # 2 days
        tag("d", "2024-01-13T00:00:00+00:00"),  # 9 days
    ]
    result = release_cadence(tags, AS_OF)
    assert result["medianIntervalDays"] == 2.0
    assert result["meanIntervalDays"] == 4.0


def test_intervals_are_rounded_to_a_tenth_of_a_day() -> None:
    tags = [tag("a", "2024-01-01T00:00:00+00:00"), tag("b", "2024-01-01T08:00:00+00:00")]
    assert release_cadence(tags, AS_OF)["items"][1]["daysSincePrevious"] == 0.3


def test_input_order_does_not_matter_and_ties_are_broken_by_name() -> None:
    tags = [
        tag("c", "2024-01-03T00:00:00+00:00"),
        tag("b", "2024-01-01T00:00:00+00:00"),
        tag("a", "2024-01-01T00:00:00+00:00"),
    ]
    forward = release_cadence(tags, AS_OF)
    backward = release_cadence(list(reversed(tags)), AS_OF)
    assert [i["name"] for i in forward["items"]] == ["a", "b", "c"]
    assert forward == backward
    assert forward["items"][1]["daysSincePrevious"] == 0.0


def test_releases_are_ordered_by_the_utc_instant_not_the_local_clock() -> None:
    early_in_utc = tag("a", "2024-01-02T01:00:00+14:00")  # 2024-01-01T11:00Z
    late_in_utc = tag("b", "2024-01-01T20:00:00+00:00")
    result = release_cadence([late_in_utc, early_in_utc], AS_OF)
    assert [i["name"] for i in result["items"]] == ["a", "b"]
    assert result["items"][0]["date"] == "2024-01-01T11:00:00Z"


def test_the_year_is_the_utc_year() -> None:
    tags = [
        tag("a", "2023-12-31T23:00:00-05:00"),  # already 2024 in UTC
        tag("b", "2024-12-31T23:30:00+00:00"),
        tag("c", "2022-06-01T00:00:00+00:00"),
    ]
    result = release_cadence(tags, when("2025-01-01T00:00:00+00:00"))
    assert result["perYear"] == [
        {"year": 2022, "releases": 1},
        {"year": 2024, "releases": 2},
    ]


def test_a_release_after_the_reference_date_is_not_reported_as_negative() -> None:
    result = release_cadence([tag("future", "2024-03-01T00:00:00+00:00")], AS_OF)
    assert result["latest"]["daysAgo"] == 0


def test_only_the_newest_releases_are_listed_but_all_are_counted() -> None:
    names = [f"v{i:03d}" for i in range(MAX_LISTED + 10)]
    tags = [
        tag(name, f"2023-{1 + i // 28:02d}-{1 + i % 28:02d}T00:00:00+00:00")
        for i, name in enumerate(names)
    ]
    random.Random(7).shuffle(tags)
    result = release_cadence(tags, AS_OF)
    assert result["count"] == MAX_LISTED + 10
    assert len(result["items"]) == MAX_LISTED
    assert result["items"][-1]["name"] == names[-1]
    assert result["items"][0]["name"] == names[10]
    assert result["items"][0]["daysSincePrevious"] is not None  # the full history was used
