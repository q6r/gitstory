"""Plain data types shared by the collector, the metrics and the renderers.

Nothing in this module touches git, the filesystem or the clock, so it is safe to import
from anywhere. The error hierarchy lives here as well: every error gitstory raises on
purpose derives from :class:`GitstoryError`, which is what the CLI turns into an exit code.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone

UTC = timezone.utc


class GitstoryError(Exception):
    """Base class of every error gitstory raises on purpose."""


class ConfigError(GitstoryError):
    """An option, filter or environment value is invalid."""


@dataclass(frozen=True, slots=True)
class FileChange:
    """One file touched by a commit, as reported by ``git log --numstat``.

    ``path`` is the path *after* the change. For renames ``old_path`` holds the previous
    path. Binary files have ``binary=True`` and zero line counts (git reports ``-``).
    """

    path: str
    additions: int = 0
    deletions: int = 0
    binary: bool = False
    old_path: str | None = None

    @property
    def changes(self) -> int:
        """Lines added plus lines deleted."""
        return self.additions + self.deletions


@dataclass(frozen=True, slots=True)
class Commit:
    """A parsed commit.

    ``author`` and ``email`` are mailmap-resolved. ``authored_at`` keeps the UTC offset the
    author's clock recorded; use :attr:`utc` for the absolute instant. ``additions`` and
    ``deletions`` are the totals over *all* changed files, while ``files`` may be truncated
    for pathological commits (``file_count`` is always the real number of changed paths).
    Merge commits carry no line statistics because ``git log`` shows no diff for them.
    """

    sha: str
    parents: tuple[str, ...]
    author: str
    email: str
    authored_at: datetime
    subject: str
    additions: int = 0
    deletions: int = 0
    file_count: int = 0
    files: tuple[FileChange, ...] = ()

    @property
    def is_merge(self) -> bool:
        """True for commits with more than one parent."""
        return len(self.parents) > 1

    @property
    def changes(self) -> int:
        """Lines added plus lines deleted, over all files."""
        return self.additions + self.deletions

    @property
    def utc(self) -> datetime:
        """The authoring instant in UTC."""
        return self.authored_at.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class Tag:
    """A tag with the date it was created (tagger date, or commit date if lightweight)."""

    name: str
    created_at: datetime
    annotated: bool = False


@dataclass(frozen=True, slots=True)
class RepoInfo:
    """Facts about the repository being analysed."""

    name: str
    root: str
    tip: str
    branch: str | None = None
    shallow: bool = False
    bare: bool = False

    @property
    def head(self) -> str:
        """Short (12 character) id of the analysed commit."""
        return self.tip[:12]

    @property
    def detached(self) -> bool:
        """True when HEAD is not on a branch."""
        return self.branch is None


@dataclass(frozen=True, slots=True)
class Filters:
    """History filters shared by every command.

    ``since``/``until`` are timezone-aware and compared by git against the *committer* date.
    ``author`` is a case-insensitive regular expression matched by git against the author
    name and e-mail. ``branch`` is any single revision (branch, tag, commit); ``None`` means
    HEAD. ``paths`` are git pathspecs.
    """

    since: datetime | None = None
    until: datetime | None = None
    author: str | None = None
    branch: str | None = None
    paths: tuple[str, ...] = ()
    no_merges: bool = False
    max_commits: int | None = None

    def __post_init__(self) -> None:
        if self.max_commits is not None and self.max_commits < 1:
            raise ConfigError("max_commits must be a positive integer")
        if self.since and self.until and self.since > self.until:
            raise ConfigError("--since is later than --until")


@dataclass(slots=True)
class ParseStats:
    """Counters the log parser fills in while streaming, surfaced as warnings."""

    records: int = 0
    malformed: int = 0
    noise_tokens: int = 0
    truncated_tokens: int = 0
    omitted_files: int = 0
    incomplete_tail: bool = False


@dataclass(slots=True)
class History:
    """Everything the metrics need: repository facts, a (lazy) commit stream and tags.

    ``commits`` may be a one-shot generator streaming from ``git log``; consume it once.
    ``stats`` is only complete after the stream has been exhausted.
    """

    repo: RepoInfo
    commits: Iterable[Commit]
    filters: Filters = field(default_factory=Filters)
    tags: Sequence[Tag] = ()
    warnings: list[str] = field(default_factory=list)
    stats: ParseStats = field(default_factory=ParseStats)
