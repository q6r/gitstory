"""Commit-size distribution on a log scale, plus the biggest commit.

Size is the number of changed lines (added + deleted) over all files, binary files counting
as zero. Merge commits carry no diff and are left out of the distribution.
"""

from __future__ import annotations

import math
from array import array
from typing import Any

from gitstory.model import Commit
from gitstory.util import iso_utc, truncate

# (id, lowest size, highest size or None for open-ended): one bucket per decade
BUCKETS: tuple[tuple[str, int, int | None], ...] = (
    ("0", 0, 0),
    ("1-9", 1, 9),
    ("10-99", 10, 99),
    ("100-999", 100, 999),
    ("1000-9999", 1_000, 9_999),
    ("10000+", 10_000, None),
)


def bucket_index(size: int) -> int:
    """Return the index into :data:`BUCKETS` that a commit of ``size`` changed lines falls in.

    Buckets are decades, so the index is simply the number of decimal digits (exact integer
    arithmetic, no floating-point logarithm), capped at the open-ended last bucket.
    """
    if size <= 0:
        return 0
    return min(len(BUCKETS) - 1, len(str(size)))


def percentile(sorted_sizes: array[int], fraction: float) -> int:
    """Nearest-rank percentile of an ascending array (0 for an empty one)."""
    if not sorted_sizes:
        return 0
    rank = max(1, math.ceil(fraction * len(sorted_sizes)))
    return sorted_sizes[rank - 1]


class SizeAccumulator:
    """Collects commit sizes compactly (one unsigned integer per commit)."""

    def __init__(self) -> None:
        self._sizes: array[int] = array("Q")
        self._buckets = [0] * len(BUCKETS)
        self._biggest: Commit | None = None

    @staticmethod
    def _rank(commit: Commit) -> tuple[int, float, str]:
        """Sort key under which the *smallest* tuple is the biggest, earliest commit."""
        return (-commit.changes, commit.utc.timestamp(), commit.sha)

    def add(self, commit: Commit) -> None:
        """Record one commit."""
        if commit.is_merge:
            return
        size = commit.changes
        self._sizes.append(size)
        self._buckets[bucket_index(size)] += 1
        if self._biggest is None or self._rank(commit) < self._rank(self._biggest):
            self._biggest = commit

    def section(self) -> dict[str, Any]:
        """The ``commitSize`` section."""
        ordered = array("Q", sorted(self._sizes))
        count = len(ordered)
        return {
            "unit": "changedLines",
            "commits": count,
            "mean": round(sum(ordered) / count, 1) if count else 0.0,
            "median": percentile(ordered, 0.5),
            "p90": percentile(ordered, 0.9),
            "max": ordered[-1] if count else 0,
            "buckets": [
                {"id": ident, "min": low, "max": high, "commits": self._buckets[i]}
                for i, (ident, low, high) in enumerate(BUCKETS)
            ],
        }

    def biggest_commit(self) -> dict[str, Any] | None:
        """The ``records.biggestCommit`` entry, or ``None`` if there were no commits."""
        commit = self._biggest
        if commit is None:
            return None
        return {
            "sha": commit.sha[:12],
            "subject": truncate(commit.subject, 120),
            "author": commit.author,
            "date": iso_utc(commit.utc),
            "additions": commit.additions,
            "deletions": commit.deletions,
            "files": commit.file_count,
        }
