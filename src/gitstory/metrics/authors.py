"""The authors leaderboard and the bus-factor heuristic.

Authors are identified by their mailmap-resolved e-mail address (case-insensitive); the
name shown is the one used most often with that address. Add a ``.mailmap`` to your
repository to merge several identities of the same person. E-mail addresses are used for
grouping only and never appear in the output.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from datetime import date, datetime
from typing import Any

from gitstory.model import Commit
from gitstory.util import iso_utc, ratio

TOP_AUTHORS = 7
BUS_FACTOR_NOTE = (
    "Smallest number of authors whose commits add up to at least half of all commits. "
    "A rough heuristic: it says nothing about who knows the code or who reviews it."
)


class _Author:
    __slots__ = ("additions", "commits", "days", "deletions", "first", "last", "names")

    def __init__(self) -> None:
        self.names: Counter[str] = Counter()
        self.commits = 0
        self.additions = 0
        self.deletions = 0
        self.days: set[date] = set()
        self.first: datetime | None = None
        self.last: datetime | None = None

    def display_name(self) -> str:
        """The most frequently used name (ties: alphabetically first)."""
        return min(self.names.items(), key=lambda item: (-item[1], item[0]))[0]


def _fold(authors: Iterable[_Author]) -> _Author:
    """Merge several authors into one aggregate (used for the "Other" row)."""
    merged = _Author()
    for author in authors:
        merged.commits += author.commits
        merged.additions += author.additions
        merged.deletions += author.deletions
        merged.days |= author.days
        if author.first and (merged.first is None or author.first < merged.first):
            merged.first = author.first
        if author.last and (merged.last is None or author.last > merged.last):
            merged.last = author.last
    return merged


def identity_key(commit: Commit) -> str:
    """Group key of an author: the case-folded e-mail, or the name when there is no e-mail."""
    return commit.email.casefold() or f"name:{commit.author.casefold()}"


def bus_factor(commit_counts: Iterable[int]) -> int:
    """Return the smallest number of authors covering at least half of all commits.

    ``commit_counts`` holds one commit count per author, in any order. Returns 0 when there
    are no commits. It is a heuristic, not a measure of knowledge distribution.
    """
    counts = sorted((count for count in commit_counts if count > 0), reverse=True)
    total = sum(counts)
    covered = 0
    for index, count in enumerate(counts, start=1):
        covered += count
        if covered * 2 >= total:
            return index
    return 0


class AuthorAccumulator:
    """Aggregates per-author statistics while the history streams by."""

    def __init__(self) -> None:
        self._authors: dict[str, _Author] = {}
        self.total_commits = 0

    def add(self, commit: Commit) -> None:
        """Record one commit."""
        key = identity_key(commit)
        stats = self._authors.get(key)
        if stats is None:
            stats = self._authors[key] = _Author()
        stats.names[commit.author] += 1
        stats.commits += 1
        stats.additions += commit.additions
        stats.deletions += commit.deletions
        stats.days.add(commit.authored_at.date())
        instant = commit.utc
        if stats.first is None or instant < stats.first:
            stats.first = instant
        if stats.last is None or instant > stats.last:
            stats.last = instant
        self.total_commits += 1

    @property
    def count(self) -> int:
        """Number of distinct authors seen."""
        return len(self._authors)

    def _ranked(self) -> list[tuple[str, _Author]]:
        return sorted(
            self._authors.items(),
            key=lambda item: (
                -item[1].commits,
                -(item[1].additions + item[1].deletions),
                item[1].display_name().casefold(),
                item[1].display_name(),
                item[0],
            ),
        )

    def _entry(self, name: str, stats: _Author, *, members: int, other: bool) -> dict[str, Any]:
        return {
            "name": name,
            "commits": stats.commits,
            "additions": stats.additions,
            "deletions": stats.deletions,
            "activeDays": len(stats.days),
            "firstCommitAt": iso_utc(stats.first) if stats.first else None,
            "lastCommitAt": iso_utc(stats.last) if stats.last else None,
            "share": ratio(stats.commits, self.total_commits),
            "members": members,
            "other": other,
        }

    def section(self, top: int = TOP_AUTHORS) -> dict[str, Any]:
        """The ``authors`` section: the top authors plus an "Other" fold of the rest."""
        ranked = self._ranked()
        items = [
            self._entry(stats.display_name(), stats, members=1, other=False)
            for _, stats in ranked[:top]
        ]
        rest = [stats for _, stats in ranked[top:]]
        if rest:
            items.append(self._entry("Other", _fold(rest), members=len(rest), other=True))
        return {"total": self.count, "limit": top, "items": items}

    def bus_factor_section(self) -> dict[str, Any]:
        """The ``busFactor`` section (clearly labelled as a heuristic)."""
        ranked = self._ranked()
        value = bus_factor(stats.commits for _, stats in ranked)
        covered = sum(stats.commits for _, stats in ranked[:value])
        return {
            "value": value,
            "threshold": 0.5,
            "coverage": ratio(covered, self.total_commits),
            "authors": [stats.display_name() for _, stats in ranked[:value]],
            "heuristic": True,
            "note": BUS_FACTOR_NOTE,
        }
