"""Use gitstory from Python: read a repository and print a few facts.

    python examples/use_as_a_library.py [path-to-a-repository]

The metrics are a plain, JSON-ready ``dict``; see docs/metrics.md for every field.
"""

from __future__ import annotations

import sys
from pathlib import Path

from gitstory.collect import collect_history
from gitstory.metrics import compute_metrics
from gitstory.model import Filters
from gitstory.util import utc_now


def main() -> None:
    """Analyse the repository given on the command line (default: the current directory)."""
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path()
    history = collect_history(path, Filters(no_merges=True, max_commits=5_000))
    metrics = compute_metrics(history, now=utc_now())

    name = metrics["repository"]["name"]
    totals, streaks, bus = metrics["totals"], metrics["streaks"], metrics["busFactor"]
    print(f"{name}: {totals['commits']:,} commits by {totals['authors']} authors")
    print(f"longest streak: {streaks['longest']} days, bus factor: {bus['value']}")
    for highlight in metrics["highlights"][:5]:
        print(f"- {highlight['title']}: {highlight['value']} ({highlight['caption']})")


if __name__ == "__main__":
    main()
