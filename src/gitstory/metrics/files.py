"""Churn hotspots (files by lines changed) and the language / file-type breakdown.

* *changes* = lines added + lines deleted, as ``git log --numstat`` reports them.
* Renames are followed: because ``git log`` streams newest first, a rename seen at commit C
  tells us that older commits touching the old path belong to the new path, so a file that
  moved around is still counted as one hotspot (under its newest name). This is best-effort
  for non-linear histories.
* Lockfiles, minified bundles, source maps and vendored directories are ignored here (see
  :func:`gitstory.languages.is_generated`); binary files have no line counts.
* The language of a change is decided by the file's name *at the time of that commit*.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from gitstory.languages import OTHER, is_generated, language_for
from gitstory.model import Commit
from gitstory.util import ratio

TOP_HOTSPOTS = 15
TOP_LANGUAGES = 6
MIN_HOTSPOT_COMMITS = 2
MAX_TRACKED_FILES = 500_000
MAX_ALIAS_HOPS = 64


class _FileStat:
    __slots__ = ("additions", "commits", "deletions", "language")

    def __init__(self, language: str) -> None:
        self.commits = 0
        self.additions = 0
        self.deletions = 0
        self.language = language


class FileAccumulator:
    """Aggregates per-file churn and per-language line counts."""

    def __init__(self) -> None:
        self.files: dict[str, _FileStat] = {}
        self.languages: Counter[str] = Counter()
        self._aliases: dict[str, str] = {}
        self.touched = 0
        self.binary = 0
        self.ignored = 0
        self.untracked = 0

    def resolve(self, path: str) -> str:
        """Follow rename aliases from an old path to the newest name of the same file."""
        for _ in range(MAX_ALIAS_HOPS):
            newer = self._aliases.get(path)
            if newer is None or newer == path:
                break
            path = newer
        return path

    def add(self, commit: Commit) -> None:
        """Record every file change of one commit (newest commits must arrive first)."""
        for change in commit.files:
            self.touched += 1
            if change.binary:
                self.binary += 1
            path = self.resolve(change.path)
            if change.old_path is not None and change.old_path != path:
                self._aliases[change.old_path] = path
            if is_generated(change.path):
                self.ignored += 1
                continue
            language = language_for(change.path)
            stat = self.files.get(path)
            if stat is None:
                if len(self.files) >= MAX_TRACKED_FILES:
                    self.untracked += 1
                    self.languages[language] += change.changes
                    continue
                stat = self.files[path] = _FileStat(language_for(path))
            stat.commits += 1
            stat.additions += change.additions
            stat.deletions += change.deletions
            self.languages[language] += change.changes

    def hotspots_section(self, top: int = TOP_HOTSPOTS) -> dict[str, Any]:
        """The ``hotspots`` section: the files with the most changed lines.

        A hotspot is a file that keeps changing, so files touched by a single commit (a data
        dump, a vendored import) are not ranked, unless no file was touched twice.
        """
        total = sum(stat.additions + stat.deletions for stat in self.files.values())
        repeated = [item for item in self.files.items() if item[1].commits >= MIN_HOTSPOT_COMMITS]
        pool = repeated or list(self.files.items())
        ranked = sorted(
            pool,
            key=lambda item: (
                -(item[1].additions + item[1].deletions),
                -item[1].commits,
                item[0],
            ),
        )
        return {
            "basis": "changedLines",
            "minCommits": MIN_HOTSPOT_COMMITS if repeated else 1,
            "uniqueFiles": len(self.files),
            "ignoredChanges": self.ignored,
            "files": [
                {
                    "path": path,
                    "commits": stat.commits,
                    "additions": stat.additions,
                    "deletions": stat.deletions,
                    "changes": stat.additions + stat.deletions,
                    "share": ratio(stat.additions + stat.deletions, total),
                    "language": stat.language,
                }
                for path, stat in ranked[:top]
            ],
        }

    def languages_section(self, top: int = TOP_LANGUAGES) -> dict[str, Any]:
        """The ``languages`` section: the top languages plus an "Other" fold."""
        total = sum(self.languages.values())
        named = sorted(
            ((name, lines) for name, lines in self.languages.items() if name != OTHER),
            key=lambda item: (-item[1], item[0]),
        )
        items = [
            {"name": name, "changes": lines, "share": ratio(lines, total), "other": False}
            for name, lines in named[:top]
            if lines > 0
        ]
        folded = sum(lines for _, lines in named[top:]) + self.languages.get(OTHER, 0)
        if folded > 0:
            items.append(
                {"name": OTHER, "changes": folded, "share": ratio(folded, total), "other": True}
            )
        return {"basis": "changedLines", "total": total, "items": items}
