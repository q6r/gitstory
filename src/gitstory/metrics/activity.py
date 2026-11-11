"""Time-based metrics: commits per day, week, month and year, the weekday x hour matrix, the
activity calendar, streaks, the longest gap and the busiest day.

Time basis
----------
Every bucketing here (the calendar day a commit belongs to, its weekday, its hour) uses the
author's *local* time exactly as recorded in the commit, i.e. the UTC offset of the author's
clock at commit time. A commit made at 23:30 in UTC-03:00 therefore counts for that evening
and for the 23:00 column, not for the next UTC day. Absolute instants (first/last commit)
are reported in UTC.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from itertools import pairwise
from typing import Any

from gitstory.model import UTC, Commit
from gitstory.util import iso_utc

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
CALENDAR_WEEKS = 53
MAX_WEEKLY_POINTS = 1040  # twenty years
MAX_MONTHLY_POINTS = 480  # forty years
HEAT_LEVELS = 5  # levels 1..5 for non-empty days; 0 is an empty day


def week_start(day: date) -> date:
    """Return the Monday of the ISO week containing ``day``."""
    return day - timedelta(days=day.weekday())


def heat_thresholds(counts: Iterable[int]) -> tuple[int, ...]:
    """Return the four quintile cut-offs used to map non-empty days onto levels 1..5.

    Quantiles of the *non-empty* days spread colours sensibly even for very skewed data
    (one 40-commit day next to many 1-commit days).
    """
    positive = sorted(count for count in counts if count > 0)
    if not positive:
        return (0,) * (HEAT_LEVELS - 1)
    last = len(positive) - 1
    return tuple(positive[(k * last) // HEAT_LEVELS] for k in range(1, HEAT_LEVELS))


def heat_level(count: int, thresholds: Sequence[int]) -> int:
    """Map a day's commit count to a level 0..5 given :func:`heat_thresholds`."""
    if count <= 0:
        return 0
    return 1 + sum(1 for cut in thresholds if count > cut)


def weekly_series(days: Mapping[date, int], end: date) -> list[dict[str, Any]]:
    """Dense ISO-week series (Monday start) from the first active week through ``end``."""
    if not days:
        return []
    counts: Counter[date] = Counter()
    for day, number in days.items():
        counts[week_start(day)] += number
    last = week_start(end)
    first = max(min(counts), last - timedelta(weeks=MAX_WEEKLY_POINTS - 1))
    series: list[dict[str, Any]] = []
    cursor = first
    while cursor <= last:
        iso = cursor.isocalendar()
        series.append(
            {
                "week": f"{iso[0]:04d}-W{iso[1]:02d}",
                "start": cursor.isoformat(),
                "commits": counts.get(cursor, 0),
            }
        )
        cursor += timedelta(weeks=1)
    return series


def monthly_series(days: Mapping[date, int], end: date) -> list[dict[str, Any]]:
    """Dense calendar-month series from the first active month through ``end``."""
    if not days:
        return []
    counts: Counter[int] = Counter()
    for day, number in days.items():
        counts[day.year * 12 + day.month - 1] += number
    last = end.year * 12 + end.month - 1
    first = max(min(counts), last - MAX_MONTHLY_POINTS + 1)
    return [
        {"month": f"{index // 12:04d}-{index % 12 + 1:02d}", "commits": counts.get(index, 0)}
        for index in range(first, last + 1)
    ]


def yearly_series(days: Mapping[date, int], end: date) -> list[dict[str, Any]]:
    """Dense calendar-year series from the first active year through ``end``."""
    if not days:
        return []
    counts: Counter[int] = Counter()
    for day, number in days.items():
        counts[day.year] += number
    return [
        {"year": year, "commits": counts.get(year, 0)} for year in range(min(counts), end.year + 1)
    ]


def calendar_grid(
    days: Mapping[date, int], as_of: date, weeks: int = CALENDAR_WEEKS
) -> dict[str, Any]:
    """Build the Monday-first activity calendar covering the last ``weeks`` weeks.

    The grid starts on the Monday ``weeks - 1`` weeks before the week of ``as_of`` and ends
    on ``as_of`` itself, one entry per day, so a heatmap can lay it out column by column.
    """
    first = week_start(as_of) - timedelta(weeks=weeks - 1)
    cells: list[tuple[date, int]] = []
    cursor = first
    while cursor <= as_of:
        cells.append((cursor, days.get(cursor, 0)))
        cursor += timedelta(days=1)
    counts = [count for _, count in cells]
    cuts = heat_thresholds(counts)
    return {
        "weeks": weeks,
        "weekStart": "mon",
        "from": first.isoformat(),
        "to": as_of.isoformat(),
        "total": sum(counts),
        "max": max(counts, default=0),
        "days": [
            {"date": day.isoformat(), "commits": count, "level": heat_level(count, cuts)}
            for day, count in cells
        ],
    }


@dataclass(frozen=True, slots=True)
class Streaks:
    """Run-length facts about active days (days with at least one commit)."""

    current: int
    current_start: date | None
    longest: int
    longest_start: date | None
    longest_end: date | None
    days_since_last: int | None

    def as_dict(self) -> dict[str, Any]:
        """JSON-ready form (camelCase, ISO dates)."""
        return {
            "current": self.current,
            "currentFrom": self.current_start.isoformat() if self.current_start else None,
            "longest": self.longest,
            "longestFrom": self.longest_start.isoformat() if self.longest_start else None,
            "longestTo": self.longest_end.isoformat() if self.longest_end else None,
            "daysSinceLastCommit": self.days_since_last,
        }


def compute_streaks(active_days: Iterable[date], today: date) -> Streaks:
    """Compute the current and longest streaks of consecutive active days.

    The current streak is the run that ends today or yesterday (a streak is not broken until
    a whole day passes without a commit; one day of slack on the other side absorbs timezone
    skew). Days more than one day after ``today`` (clock-skewed commits) are ignored.
    """
    horizon = today + timedelta(days=1)
    days = sorted({day for day in active_days if day <= horizon})
    if not days:
        return Streaks(0, None, 0, None, None, None)
    runs: list[tuple[date, date]] = []
    start = previous = days[0]
    for day in days[1:]:
        if (day - previous).days != 1:
            runs.append((start, previous))
            start = day
        previous = day
    runs.append((start, previous))

    longest = max(runs, key=lambda run: ((run[1] - run[0]).days, -run[0].toordinal()))
    last_start, last_end = runs[-1]
    alive = (today - last_end).days <= 1
    return Streaks(
        current=(last_end - last_start).days + 1 if alive else 0,
        current_start=last_start if alive else None,
        longest=(longest[1] - longest[0]).days + 1,
        longest_start=longest[0],
        longest_end=longest[1],
        days_since_last=max(0, (today - last_end).days),
    )


def longest_gap(days: Sequence[date]) -> tuple[int, date, date] | None:
    """Return ``(quiet_days, last_active_before, first_active_after)`` for the longest gap.

    ``days`` are the distinct active days. A gap is the run of commit-free days between two
    consecutive active days; ties go to the earliest. ``None`` if there is no gap at all.
    """
    ordered = sorted(set(days))
    best: tuple[int, date, date] | None = None
    for before, after in pairwise(ordered):
        quiet = (after - before).days - 1
        if quiet > 0 and (best is None or quiet > best[0]):
            best = (quiet, before, after)
    return best


def busiest_day(days: Mapping[date, int]) -> tuple[date, int] | None:
    """Return the day with the most commits (the earliest wins ties), or ``None``."""
    if not days:
        return None
    day = min(days, key=lambda candidate: (-days[candidate], candidate))
    return day, days[day]


class ActivityAccumulator:
    """Counts commits per local day and per weekday/hour while the history streams by."""

    def __init__(self) -> None:
        self.days: Counter[date] = Counter()
        self.matrix: list[list[int]] = [[0] * 24 for _ in WEEKDAYS]
        self.first: datetime | None = None
        self.last: datetime | None = None
        self.total = 0

    def add(self, commit: Commit) -> None:
        """Record one commit."""
        local = commit.authored_at
        self.days[local.date()] += 1
        self.matrix[local.weekday()][local.hour] += 1
        instant = commit.utc
        if self.first is None or instant < self.first:
            self.first = instant
        if self.last is None or instant > self.last:
            self.last = instant
        self.total += 1

    @property
    def hours(self) -> list[int]:
        """Commits per local hour of day (0..23), over all weekdays."""
        return [sum(row[hour] for row in self.matrix) for hour in range(24)]

    def as_of_day(self, as_of: datetime) -> date:
        """The calendar day "today" means for these commits.

        It is the UTC date of ``as_of``, extended by one day when the newest commit's local
        date is already tomorrow (an author east of UTC committing in their morning).
        """
        today = as_of.astimezone(UTC).date()
        newest = max(self.days, default=None)
        if newest is not None and today < newest <= today + timedelta(days=1):
            return newest
        return today

    def activity_section(self, as_of: date) -> dict[str, Any]:
        """The ``activity`` section of the metrics document."""
        end = max([as_of, *self.days])
        peak = max((max(row) for row in self.matrix), default=0)
        return {
            "timezone": "author-local",
            "asOf": as_of.isoformat(),
            "byDay": [
                {"date": day.isoformat(), "commits": self.days[day]} for day in sorted(self.days)
            ],
            "byWeek": weekly_series(self.days, end),
            "byMonth": monthly_series(self.days, end),
            "byYear": yearly_series(self.days, end),
            "weekdayHour": {
                "weekdays": list(WEEKDAYS),
                "matrix": [list(row) for row in self.matrix],
                "max": peak,
            },
            "calendar": calendar_grid(self.days, as_of),
        }

    def streak_section(self, as_of: date) -> dict[str, Any]:
        """The ``streaks`` section of the metrics document."""
        return compute_streaks(self.days, as_of).as_dict()

    def totals_section(self) -> dict[str, Any]:
        """The time-related members of ``totals``."""
        span = (self.last - self.first).days + 1 if self.first and self.last else 0
        return {
            "activeDays": len(self.days),
            "firstCommitAt": iso_utc(self.first) if self.first else None,
            "lastCommitAt": iso_utc(self.last) if self.last else None,
            "spanDays": span,
            "commitsPerActiveDay": round(self.total / len(self.days), 2) if self.days else 0.0,
        }

    def records_section(self) -> dict[str, Any]:
        """The busiest-day and longest-gap members of ``records``."""
        busiest = busiest_day(self.days)
        gap = longest_gap(sorted(self.days))
        return {
            "busiestDay": {"date": busiest[0].isoformat(), "commits": busiest[1]}
            if busiest
            else None,
            "longestGap": {
                "days": gap[0],
                "from": gap[1].isoformat(),
                "to": gap[2].isoformat(),
            }
            if gap
            else None,
        }
