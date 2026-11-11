"""Tiny builders for in-memory commits and histories (no git involved)."""

from __future__ import annotations

import itertools
from collections.abc import Iterable, Sequence
from datetime import datetime

from gitstory.model import (
    UTC,
    Commit,
    FileChange,
    Filters,
    History,
    RepoInfo,
    Tag,
)
from gitstory.util import parse_git_timestamp

_counter = itertools.count(1)


def when(text: str) -> datetime:
    """Parse ``2024-01-02T10:00:00+00:00`` (or ``...Z``); raises if it is not valid."""
    moment = parse_git_timestamp(
        text if text.endswith("Z") or "+" in text[10:] or "-" in text[10:] else text + "Z"
    )
    assert moment is not None, text
    return moment


def commit(
    at: str = "2024-01-02T10:00:00+00:00",
    *,
    author: str = "Ada",
    email: str | None = None,
    subject: str = "feat: thing",
    files: Iterable[tuple[str, int, int] | FileChange] = (),
    merge: bool = False,
    sha: str | None = None,
) -> Commit:
    """A commit made at ``at`` (an ISO timestamp with the author's offset)."""
    changes = tuple(f if isinstance(f, FileChange) else FileChange(f[0], f[1], f[2]) for f in files)
    number = next(_counter)
    parents = ("a" * 40, "b" * 40) if merge else ("a" * 40,)
    return Commit(
        sha=sha or f"{number:040x}",
        parents=parents,
        author=author,
        email=email if email is not None else f"{author.lower().replace(' ', '.')}@example.com",
        authored_at=when(at),
        subject=subject,
        additions=sum(c.additions for c in changes),
        deletions=sum(c.deletions for c in changes),
        file_count=len(changes),
        files=changes,
    )


def history(
    commits: Sequence[Commit],
    *,
    tags: Sequence[Tag] = (),
    filters: Filters | None = None,
    name: str = "demo",
    branch: str | None = "main",
) -> History:
    """A :class:`History` over ``commits`` (newest first, like ``git log``)."""
    repo = RepoInfo(name=name, root="/demo", tip="f" * 40, branch=branch)
    return History(repo=repo, commits=list(commits), filters=filters or Filters(), tags=tags)


def tag(name: str, at: str, *, annotated: bool = False) -> Tag:
    return Tag(name=name, created_at=when(at), annotated=annotated)


__all__ = ["UTC", "commit", "history", "tag", "when"]
