"""Chronotype: when in the day the commits happen.

The hour is the author's *local* hour as recorded in the commit. Four windows partition the
day; the label comes from the window with the highest share relative to its width (a window
that merely covers more hours does not win by default). Percentages of every window are
reported so the label can be judged. With fewer than ``MIN_COMMITS`` commits no label is given.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from gitstory.util import ratio

MIN_COMMITS = 10

# (id, label, first hour, last hour + 1); the night window wraps around midnight
WINDOWS: tuple[tuple[str, str, int, int], ...] = (
    ("early-bird", "Early bird", 5, 9),
    ("nine-to-five", "Nine-to-five", 9, 18),
    ("evening", "Evening coder", 18, 22),
    ("night-owl", "Night owl", 22, 5),
)


def _hours_in(start: int, end: int) -> list[int]:
    if start < end:
        return list(range(start, end))
    return [*range(start, 24), *range(0, end)]


def classify(hours: Sequence[int]) -> dict[str, Any]:
    """Classify a 24-slot histogram of commits per local hour."""
    if len(hours) != 24:
        raise ValueError("hours must have 24 entries")
    total = sum(hours)
    windows: list[dict[str, Any]] = []
    best_id: str | None = None
    best_label = "Not enough data"
    best_lift = -1.0
    for ident, label, start, end in WINDOWS:
        covered = _hours_in(start, end)
        commits = sum(hours[hour] for hour in covered)
        share = ratio(commits, total)
        windows.append(
            {
                "id": ident,
                "label": label,
                "from": start,
                "to": end,
                "commits": commits,
                "share": share,
            }
        )
        lift = (commits / total) / (len(covered) / 24) if total else 0.0
        if lift > best_lift:
            best_id, best_label, best_lift = ident, label, lift
    if total < MIN_COMMITS:
        best_id, best_label = None, "Not enough data"
    peak = max(range(24), key=lambda hour: (hours[hour], -hour)) if total else None
    return {
        "type": best_id,
        "label": best_label,
        "total": total,
        "peakHour": peak,
        "hours": list(hours),
        "windows": windows,
    }
