"""Metrics over parsed commits.

:func:`compute_metrics` makes a single pass over the (possibly streaming) commit history,
feeding each commit to small accumulators, and assembles the ``gitstory.metrics/v1``
document (see ``docs/metrics.md``). Memory use does not grow with the number of
commits except for compact per-author, per-file and per-day tallies.

Every accumulator and helper is a plain, independently testable unit; the output is a
JSON-ready ``dict`` whose key order and list ordering are fully deterministic.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from gitstory import __version__
from gitstory.metrics.activity import ActivityAccumulator
from gitstory.metrics.authors import AuthorAccumulator
from gitstory.metrics.chronotype import classify
from gitstory.metrics.files import MAX_TRACKED_FILES, FileAccumulator
from gitstory.metrics.highlights import build_highlights
from gitstory.metrics.messages import MessageAccumulator
from gitstory.metrics.releases import release_cadence
from gitstory.metrics.sizes import SizeAccumulator
from gitstory.model import History, ParseStats
from gitstory.util import iso_utc

SCHEMA = "gitstory.metrics/v1"

__all__ = ["SCHEMA", "compute_metrics"]


def _parse_warnings(stats: ParseStats, untracked: int) -> list[str]:
    warnings: list[str] = []
    if stats.malformed:
        warnings.append(
            f"{stats.malformed} commit records in the git output could not be parsed "
            "and were skipped."
        )
    if stats.noise_tokens:
        warnings.append(f"{stats.noise_tokens} unexpected values in the git output were ignored.")
    if stats.truncated_tokens:
        warnings.append(
            f"{stats.truncated_tokens} over-long values in the git output were truncated."
        )
    if stats.omitted_files:
        warnings.append(
            f"{stats.omitted_files} file entries beyond the per-commit limit were not itemised "
            "(line totals still include them)."
        )
    if stats.incomplete_tail:
        warnings.append("The git output ended unexpectedly; the last record was dropped.")
    if untracked:
        warnings.append(
            f"Hotspot tracking stopped at {MAX_TRACKED_FILES:,} files; {untracked} later file "
            "changes are not attributed to individual files."
        )
    return warnings


def compute_metrics(history: History, *, now: datetime) -> dict[str, Any]:
    """Consume ``history.commits`` once and return the ``gitstory.metrics/v1`` document.

    ``now`` is the reference clock (timezone-aware); ``--until`` earlier than ``now`` takes
    its place as the "as of" moment, so streaks and the calendar describe that window.
    """
    filters = history.filters
    as_of = min(now, filters.until) if filters.until else now
    activity = ActivityAccumulator()
    authors = AuthorAccumulator()
    files = FileAccumulator()
    messages = MessageAccumulator()
    sizes = SizeAccumulator()
    merges = additions = deletions = commits = 0
    for commit in history.commits:
        commits += 1
        merges += int(commit.is_merge)
        additions += commit.additions
        deletions += commit.deletions
        activity.add(commit)
        authors.add(commit)
        files.add(commit)
        messages.add(commit)
        sizes.add(commit)

    today = activity.as_of_day(as_of)
    warnings = [*history.warnings, *_parse_warnings(history.stats, files.untracked)]
    if commits == 0:
        warnings.append("No commits matched the given filters.")

    repo = history.repo
    document: dict[str, Any] = {
        "schema": SCHEMA,
        "generator": {"name": "gitstory", "version": __version__},
        "generatedAt": iso_utc(now),
        "repository": {
            "name": repo.name,
            "branch": repo.branch,
            "detached": repo.detached,
            "head": repo.head,
            "shallow": repo.shallow,
        },
        "filters": {
            "since": iso_utc(filters.since) if filters.since else None,
            "until": iso_utc(filters.until) if filters.until else None,
            "author": filters.author,
            "branch": filters.branch,
            "paths": list(filters.paths),
            "noMerges": filters.no_merges,
            "maxCommits": filters.max_commits,
        },
        "warnings": warnings,
        "totals": {
            "commits": commits,
            "mergeCommits": merges,
            "authors": authors.count,
            "additions": additions,
            "deletions": deletions,
            "filesChanged": files.touched,
            "binaryChanges": files.binary,
            **activity.totals_section(),
        },
        "activity": activity.activity_section(today),
        "streaks": activity.streak_section(today),
        "authors": authors.section(),
        "busFactor": authors.bus_factor_section(),
        "hotspots": files.hotspots_section(),
        "languages": files.languages_section(),
        "commitSize": sizes.section(),
        "conventional": messages.conventional_section(),
        "words": messages.words_section(),
        "chronotype": classify(activity.hours),
        "records": {
            "biggestCommit": sizes.biggest_commit(),
            **activity.records_section(),
        },
        "releases": release_cadence(history.tags, as_of),
    }
    document["highlights"] = build_highlights(document)
    return document
