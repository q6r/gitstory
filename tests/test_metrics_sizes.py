"""Commit-size distribution and the biggest commit."""

from __future__ import annotations

import random
from array import array
from typing import Any

import pytest
from builders import commit

from gitstory.metrics.sizes import BUCKETS, SizeAccumulator, bucket_index, percentile
from gitstory.model import Commit


def sized(lines: int, *, subject: str = "feat: thing", author: str = "Ada") -> Commit:
    """A commit that changes exactly ``lines`` lines (a mix of additions and deletions)."""
    files = [("f.py", lines - lines // 2, lines // 2)] if lines else []
    return commit(subject=subject, author=author, files=files)


def biggest(acc: SizeAccumulator) -> dict[str, Any]:
    result = acc.biggest_commit()
    assert result is not None
    return result


# ---------------------------------------------------------------------------- buckets


@pytest.mark.parametrize(
    ("size", "bucket"),
    [
        (-5, "0"),
        (0, "0"),
        (1, "1-9"),
        (9, "1-9"),
        (10, "10-99"),
        (99, "10-99"),
        (100, "100-999"),
        (999, "100-999"),
        (1_000, "1000-9999"),
        (9_999, "1000-9999"),
        (10_000, "10000+"),
        (10**12, "10000+"),
    ],
)
def test_buckets_are_decades(size: int, bucket: str) -> None:
    assert BUCKETS[bucket_index(size)][0] == bucket


def test_every_size_lands_in_a_bucket_whose_range_contains_it() -> None:
    rng = random.Random(5)
    for _ in range(2_000):
        size = max(0, int(10 ** rng.uniform(0, 7)) - rng.randint(0, 1))
        _, low, high = BUCKETS[bucket_index(size)]
        assert low <= size
        assert high is None or size <= high


def test_percentiles_use_the_nearest_rank_method() -> None:
    assert percentile(array("Q"), 0.5) == 0
    assert percentile(array("Q", [7]), 0.9) == 7
    ten = array("Q", range(1, 11))
    assert percentile(ten, 0.5) == 5
    assert percentile(ten, 0.9) == 9
    assert percentile(ten, 1.0) == 10
    assert percentile(ten, 0.0) == 1  # never indexes before the first element


# ------------------------------------------------------------------------- distribution


def test_the_distribution_of_a_small_history() -> None:
    acc = SizeAccumulator()
    for lines in (0, 1, 2, 3, 100):
        acc.add(sized(lines))
    section = acc.section()
    assert section["unit"] == "changedLines"
    assert section["commits"] == 5
    assert section["mean"] == 21.2
    assert section["median"] == 2
    assert section["p90"] == 100
    assert section["max"] == 100
    counts = {b["id"]: b["commits"] for b in section["buckets"]}
    assert counts == {"0": 1, "1-9": 3, "10-99": 0, "100-999": 1, "1000-9999": 0, "10000+": 0}
    assert sum(counts.values()) == section["commits"]


def test_buckets_describe_their_own_ranges() -> None:
    buckets = SizeAccumulator().section()["buckets"]
    assert [(b["id"], b["min"], b["max"]) for b in buckets] == [
        ("0", 0, 0),
        ("1-9", 1, 9),
        ("10-99", 10, 99),
        ("100-999", 100, 999),
        ("1000-9999", 1000, 9999),
        ("10000+", 10000, None),
    ]


def test_merge_commits_are_not_part_of_the_distribution() -> None:
    acc = SizeAccumulator()
    acc.add(sized(10))
    acc.add(commit(merge=True, files=[("f.py", 5000, 0)]))
    assert acc.section()["commits"] == 1
    assert acc.section()["max"] == 10
    big = biggest(acc)
    assert big["additions"] + big["deletions"] == 10


def test_an_empty_accumulator() -> None:
    acc = SizeAccumulator()
    section = acc.section()
    assert (section["commits"], section["mean"], section["median"]) == (0, 0.0, 0)
    assert (section["p90"], section["max"]) == (0, 0)
    assert all(bucket["commits"] == 0 for bucket in section["buckets"])
    assert acc.biggest_commit() is None


def test_sizes_are_stored_compactly() -> None:
    acc = SizeAccumulator()
    for lines in range(1_000):
        acc.add(sized(lines))
    assert acc._sizes.itemsize == 8  # one unsigned 64-bit integer per commit, not a Python int


# ---------------------------------------------------------------------- biggest commit


def test_the_biggest_commit_carries_its_facts() -> None:
    acc = SizeAccumulator()
    acc.add(sized(5, author="Small"))
    acc.add(
        commit(
            "2024-03-01T23:30:00-03:00",
            author="Ada",
            subject="feat: the import",
            files=[("a.py", 70, 0), ("b.py", 30, 5)],
            sha=("0123456789abcdef" * 3)[:40],
        )
    )
    assert biggest(acc) == {
        "sha": "0123456789ab",
        "subject": "feat: the import",
        "author": "Ada",
        "date": "2024-03-02T02:30:00Z",  # an absolute instant, in UTC
        "additions": 100,
        "deletions": 5,
        "files": 2,
    }


def test_equally_big_commits_go_to_the_earliest_instant() -> None:
    earlier = commit("2023-01-01T00:00:00+00:00", files=[("f.py", 50, 0)], sha="f" * 40)
    later = commit("2023-06-01T00:00:00+00:00", files=[("f.py", 50, 0)], sha="a" * 40)
    for order in ((later, earlier), (earlier, later)):
        acc = SizeAccumulator()
        for item in order:
            acc.add(item)
        assert biggest(acc)["sha"] == "f" * 12


def test_equally_big_commits_at_the_same_instant_go_to_the_smallest_sha() -> None:
    first = commit("2023-01-01T00:00:00+00:00", files=[("f.py", 50, 0)], sha="b" * 40)
    second = commit("2023-01-01T00:00:00+00:00", files=[("f.py", 50, 0)], sha="a" * 40)
    for order in ((first, second), (second, first)):
        acc = SizeAccumulator()
        for item in order:
            acc.add(item)
        assert biggest(acc)["sha"] == "a" * 12


def test_a_very_long_subject_is_truncated() -> None:
    acc = SizeAccumulator()
    acc.add(sized(3, subject="x" * 500))
    subject = biggest(acc)["subject"]
    assert len(subject) == 120
    assert subject.endswith("\N{HORIZONTAL ELLIPSIS}")


def test_a_history_of_empty_commits_still_has_a_biggest_one() -> None:
    acc = SizeAccumulator()
    acc.add(sized(0, subject="chore: empty"))
    big = biggest(acc)
    assert (big["additions"], big["deletions"]) == (0, 0)
    assert acc.section()["buckets"][0]["commits"] == 1
