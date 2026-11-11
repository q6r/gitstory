"""Release cadence from tags.

Every tag reachable from the analysed revision counts as a release; annotated tags use the
tagger date, lightweight tags the date of the commit they point at.
"""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Sequence
from datetime import datetime
from itertools import pairwise
from typing import Any

from gitstory.model import UTC, Tag
from gitstory.util import iso_utc

MAX_LISTED = 50
SECONDS_PER_DAY = 86_400


def release_cadence(tags: Sequence[Tag], as_of: datetime) -> dict[str, Any]:
    """Summarise ``tags`` (any order): counts, intervals between releases, the latest one."""
    ordered = sorted(tags, key=lambda tag: (tag.created_at.astimezone(UTC), tag.name))
    gaps = [
        round((later.created_at - earlier.created_at).total_seconds() / SECONDS_PER_DAY, 1)
        for earlier, later in pairwise(ordered)
    ]
    per_year = Counter(tag.created_at.astimezone(UTC).year for tag in ordered)
    items = [
        {
            "name": tag.name,
            "date": iso_utc(tag.created_at),
            "annotated": tag.annotated,
            "daysSincePrevious": None if index == 0 else gaps[index - 1],
        }
        for index, tag in enumerate(ordered)
    ]
    latest = ordered[-1] if ordered else None
    return {
        "count": len(ordered),
        "annotated": sum(1 for tag in ordered if tag.annotated),
        "meanIntervalDays": round(statistics.fmean(gaps), 1) if gaps else None,
        "medianIntervalDays": round(statistics.median(gaps), 1) if gaps else None,
        "latest": {
            "name": latest.name,
            "date": iso_utc(latest.created_at),
            "daysAgo": max(0, int((as_of - latest.created_at).total_seconds() // SECONDS_PER_DAY)),
        }
        if latest
        else None,
        "perYear": [{"year": year, "releases": per_year[year]} for year in sorted(per_year)],
        "items": items[-MAX_LISTED:],
    }
