"""Stream a repository's history out of the system ``git``.

Design notes
------------
* ``git`` is the only program ever started. It is launched with a fixed argv list (no
  shell), from an absolute path resolved on ``PATH`` (never the current directory) and with
  a scrubbed environment (``GIT_OPTIONAL_LOCKS=0``, no pager, no prompts, no inherited
  repository-selection variables).
* ``git log -z --numstat`` output is NUL-delimited, so paths containing quotes, tabs,
  newlines or ``=>`` are unambiguous and renames arrive as separate old/new tokens. Text-mode
  numstat (``{old => new}``) is deliberately not used: it cannot be parsed reliably.
* Every commit record starts with a random per-run marker that repository content cannot
  forge; the header fields behind it are consumed positionally (they cannot contain NUL).
* The output is read in fixed-size chunks and parsed incrementally, so memory use does not
  grow with the size of the history. Token, file-count and text lengths are all bounded.
* Repository content is untrusted data: it is decoded leniently, sanitised, and never
  evaluated, formatted or used as a command.
"""

from __future__ import annotations

import os
import re
import secrets
import subprocess
import tempfile
import threading
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import IO

from gitstory.model import (
    UTC,
    Commit,
    ConfigError,
    FileChange,
    Filters,
    GitstoryError,
    History,
    ParseStats,
    RepoInfo,
    Tag,
)
from gitstory.util import iso_utc, parse_git_timestamp, sanitize_text

CHUNK_SIZE = 1 << 16
MAX_TOKEN_BYTES = 1 << 20
MAX_FILES_PER_COMMIT = 50_000
MAX_TAGS = 2_000
MAX_NAME_CHARS = 200
MAX_SUBJECT_CHARS = 500
MAX_PATH_CHARS = 1_024
MAX_STDERR_BYTES = 4_096
MAX_REVISION_CHARS = 255
HEADER_FIELDS = 6

_SHA = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
_NUMBER_DIGITS = 12
_LOG_FIELDS = "%H%x00%P%x00%aN%x00%aE%x00%aI%x00%s"
_TAG_FORMAT = (
    "%(refname:lstrip=2)%00%(objecttype)%00%(*objectname)%00%(objectname)%00"
    "%(creatordate:iso-strict)"
)
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_DROPPED_ENVIRONMENT = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_NAMESPACE",
    "GIT_PREFIX",
    "GIT_PAGER",
    "GIT_EXTERNAL_DIFF",
    "GIT_DIFF_OPTS",
)


class GitError(GitstoryError):
    """Base class of errors caused by git or by the repository."""


class GitNotFoundError(GitError):
    """The ``git`` executable could not be found on PATH."""


class NotARepositoryError(GitError):
    """The given path is not (inside) a git repository."""


class RevisionNotFoundError(GitError):
    """The requested branch, tag or commit does not exist."""


class EmptyRepositoryError(GitError):
    """The repository has no commits yet."""


class GitCommandError(GitError):
    """A git command failed."""


class GitTimeoutError(GitCommandError):
    """A git command ran longer than the allowed time and was killed."""


# --------------------------------------------------------------------------- environment


def find_git() -> str:
    """Return the absolute path of ``git`` found on PATH.

    Relative and empty PATH entries are skipped so a ``git`` planted in the current
    directory (which Windows would otherwise prefer) is never executed.
    """
    names = ("git.exe",) if os.name == "nt" else ("git",)
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        directory = Path(entry.strip().strip('"')) if entry.strip() else None
        if directory is None or not directory.is_absolute():
            continue
        for name in names:
            candidate = directory / name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    raise GitNotFoundError("git was not found on PATH; install git and try again")


def git_environment() -> dict[str, str]:
    """Build the scrubbed environment git is run with."""
    env = {key: value for key, value in os.environ.items() if key not in _DROPPED_ENVIRONMENT}
    env.update(
        GIT_OPTIONAL_LOCKS="0",
        GIT_PAGER="cat",
        PAGER="cat",
        GIT_TERMINAL_PROMPT="0",
        LC_ALL="C",
    )
    return env


def _describe_failure(returncode: int, stderr: str) -> str:
    text = sanitize_text(stderr, 400) or "no error output"
    if "dubious ownership" in stderr:
        return (
            "git refuses to read this repository because it is owned by another user "
            "(safe.directory). If you trust it, run: git config --global --add "
            f"safe.directory <path>. git said: {text}"
        )
    return f"git exited with status {returncode}: {text}"


def _run(git: str, args: Sequence[str], *, timeout: float | None) -> tuple[int, str, str]:
    """Run a short git command and return ``(returncode, stdout, stderr)`` decoded leniently."""
    try:
        done = subprocess.run(  # noqa: S603 - fixed argv list, absolute executable, no shell
            [git, "--no-pager", *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            env=git_environment(),
            timeout=timeout,
            check=False,
            creationflags=_NO_WINDOW,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitTimeoutError(f"git timed out after {timeout:g}s") from exc
    except OSError as exc:
        raise GitCommandError(f"could not run git: {exc}") from exc
    return (
        done.returncode,
        done.stdout.decode("utf-8", errors="replace"),
        done.stderr.decode("utf-8", errors="replace"),
    )


# ------------------------------------------------------------------------------ repository


_RANGE_LIKE = re.compile(r"\.\.|^\^|\^[!@-]")


def _validate_revision(revision: str) -> str:
    """Accept one branch, tag or commit; refuse option look-alikes, ranges and control chars."""
    if (
        not revision
        or revision.startswith("-")
        or len(revision) > MAX_REVISION_CHARS
        or any(ord(char) < 0x20 or ord(char) == 0x7F for char in revision)
        or _RANGE_LIKE.search(revision)
    ):
        raise ConfigError(
            f"invalid branch or revision {sanitize_text(revision, 60)!r}: "
            "give a single branch, tag or commit (ranges are not supported)"
        )
    return revision


@dataclass(frozen=True, slots=True)
class _Layout:
    """Where the repository lives, as reported by ``git rev-parse``."""

    root: str
    bare: bool
    shallow: bool


def _inspect(git: str, directory: Path, timeout: float | None) -> _Layout:
    """Find the repository root and its bare/shallow flags with a single git call."""
    base = [
        "-C",
        str(directory),
        "rev-parse",
        "--is-bare-repository",
        "--is-shallow-repository",
        "--absolute-git-dir",
    ]
    code, out, err = _run(git, [*base, "--show-toplevel"], timeout=timeout)
    if code != 0 and "work tree" in err:  # a bare repository (or a .git directory) has no top level
        code, out, err = _run(git, base, timeout=timeout)
    if code != 0:
        if "not a git repository" in err:
            raise NotARepositoryError(
                f"{directory} is not inside a git repository (run gitstory in a clone, "
                "or pass the path of one)"
            )
        raise GitCommandError(_describe_failure(code, err))
    lines = [line.strip() for line in out.splitlines()]
    if len(lines) < 3:
        raise GitCommandError(f"unexpected output while inspecting {directory}")
    return _Layout(
        root=lines[3] if len(lines) > 3 else lines[2],
        bare=lines[0] == "true",
        shallow=lines[1] == "true",
    )


def _resolve(
    git: str, root: str, revision: str, *, explicit: bool, timeout: float | None
) -> tuple[str, str | None]:
    """Resolve ``revision`` to a commit id (and the checked-out branch name for HEAD)."""
    code, out, _ = _run(
        git,
        ["-C", root, "log", "-1", "--format=%H%x00%D", "--end-of-options", revision, "--"],
        timeout=timeout,
    )
    tip, _, decorations = out.strip().partition("\0")
    if code != 0 or not _SHA.fullmatch(tip):
        if not explicit:
            raise EmptyRepositoryError(
                "this repository has no commits yet; make a commit and try again"
            )
        raise RevisionNotFoundError(
            f"unknown branch, tag or commit: {sanitize_text(revision, 80)!r}"
        )
    if explicit:
        return tip, revision
    branch = next(
        (
            part[len("HEAD -> ") :]
            for part in decorations.split(", ")
            if part.startswith("HEAD -> ")
        ),
        None,
    )
    return tip, branch


def open_repository(
    path: str | os.PathLike[str],
    *,
    branch: str | None = None,
    git: str | None = None,
    timeout: float | None = 60,
) -> RepoInfo:
    """Validate ``path`` and gather the facts about its repository.

    ``branch`` selects the revision to analyse (default HEAD). Raises
    :class:`NotARepositoryError`, :class:`EmptyRepositoryError` or
    :class:`RevisionNotFoundError` with an actionable message. Two git calls, because every
    process start is slow on some platforms.
    """
    git = git or find_git()
    directory = Path(path).expanduser()
    if not directory.is_dir():
        raise NotARepositoryError(f"not a directory: {directory}")
    revision = _validate_revision(branch) if branch is not None else "HEAD"
    layout = _inspect(git, directory, timeout)
    tip, current = _resolve(
        git, layout.root, revision, explicit=branch is not None, timeout=timeout
    )

    name = PurePosixPath(layout.root.replace("\\", "/")).name
    if layout.bare:
        name = name.removesuffix(".git")
    elif name == ".git":
        name = PurePosixPath(layout.root.replace("\\", "/")).parent.name
    return RepoInfo(
        name=sanitize_text(name or directory.resolve().name, 100) or "repository",
        root=layout.root,
        tip=tip,
        branch=sanitize_text(current, 100) if current else None,
        shallow=layout.shallow,
        bare=layout.bare,
    )


# -------------------------------------------------------------------------------- log argv


def build_log_argv(git: str, repo: RepoInfo, filters: Filters, nonce: str) -> list[str]:
    """Return the exact argv used to stream the history (a pure function, unit-tested)."""
    for value in (filters.author or "", *filters.paths):
        if "\0" in value:
            raise ConfigError("filter values must not contain NUL characters")
    argv = [
        git,
        "--no-pager",
        "-C",
        repo.root,
        "-c",
        "core.quotePath=false",
        "-c",
        "log.showSignature=false",
        "log",
        "-z",
        "--numstat",
        "-M",
        "--no-color",
        "--no-ext-diff",
        "--no-textconv",
        "--use-mailmap",
        "--encoding=UTF-8",
        f"--format=%x01{nonce}%x00{_LOG_FIELDS}",
    ]
    if filters.since is not None:
        argv.append(f"--since={iso_utc(filters.since)}")
    if filters.until is not None:
        argv.append(f"--until={iso_utc(filters.until)}")
    if filters.author:
        argv += ["--regexp-ignore-case", f"--author={filters.author}"]
    if filters.no_merges:
        argv.append("--no-merges")
    if filters.max_commits is not None:
        argv.append(f"--max-count={filters.max_commits}")
    argv += ["--end-of-options", repo.tip, "--", *filters.paths]
    return argv


# --------------------------------------------------------------------------------- process


class _GitProcess:
    """A running ``git`` whose stdout is consumed in chunks.

    stderr goes to a temporary file (so a chatty git can never deadlock on a full pipe) and
    an optional watchdog timer kills the process when the time budget is exhausted.
    """

    def __init__(self, argv: list[str], *, timeout: float | None) -> None:
        self.timeout = timeout
        self.timed_out = False
        self._stderr = tempfile.TemporaryFile()  # noqa: SIM115 - closed in close()
        try:
            self._process = subprocess.Popen(  # noqa: S603 - fixed argv list, no shell
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=self._stderr,
                env=git_environment(),
                bufsize=0,
                creationflags=_NO_WINDOW,
            )
        except OSError as exc:
            self._stderr.close()
            raise GitCommandError(f"could not run git: {exc}") from exc
        self._timer: threading.Timer | None = None
        if timeout is not None:
            self._timer = threading.Timer(timeout, self._expire)
            self._timer.daemon = True
            self._timer.start()

    def _expire(self) -> None:
        self.timed_out = True
        self._process.kill()

    def chunks(self) -> Iterator[bytes]:
        """Yield stdout as it arrives until git closes it."""
        stdout: IO[bytes] | None = self._process.stdout
        if stdout is None:
            raise GitCommandError("git produced no output stream")
        while True:
            data = stdout.read(CHUNK_SIZE)
            if not data:
                return
            yield data

    def stderr_text(self) -> str:
        """Return the first few KiB git wrote to stderr."""
        self._stderr.seek(0)
        return self._stderr.read(MAX_STDERR_BYTES).decode("utf-8", errors="replace")

    def finish(self) -> None:
        """Wait for a git that has closed stdout and raise if it failed."""
        returncode = self._process.wait()
        if self.timed_out:
            raise GitTimeoutError(f"git did not finish within {self.timeout:g}s and was stopped")
        if returncode != 0:
            raise GitCommandError(_describe_failure(returncode, self.stderr_text()))

    def close(self) -> None:
        """Stop git if it is still running and release every resource (idempotent)."""
        if self._timer is not None:
            self._timer.cancel()
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
        if self._process.stdout is not None:
            self._process.stdout.close()
        self._stderr.close()


# ----------------------------------------------------------------------------------- parser


def iter_tokens(chunks: Iterable[bytes], stats: ParseStats) -> Iterator[bytes]:
    """Split a stream of arbitrary byte chunks into NUL-terminated tokens.

    A token longer than ``MAX_TOKEN_BYTES`` is truncated (and counted) rather than buffered
    without bound. Bytes after the last NUL (a cut-off stream) are never yielded.
    """
    pending = bytearray()
    truncated = False
    for chunk in chunks:
        position = 0
        while True:
            nul = chunk.find(b"\0", position)
            end = len(chunk) if nul < 0 else nul
            room = MAX_TOKEN_BYTES - len(pending)
            piece = chunk[position:end]
            if len(piece) > room:
                piece = piece[:room]
                truncated = True
            pending += piece
            if nul < 0:
                break
            if truncated:
                stats.truncated_tokens += 1
                truncated = False
            yield bytes(pending)
            pending.clear()
            position = nul + 1
    if pending:
        stats.incomplete_tail = True


class _Record:
    """The commit currently being assembled from tokens."""

    __slots__ = ("additions", "deletions", "file_count", "files", "header", "old_path", "pending")

    def __init__(self) -> None:
        self.header: list[bytes] = []
        self.files: list[FileChange] = []
        self.additions = 0
        self.deletions = 0
        self.file_count = 0
        self.pending: tuple[int, int, bool] | None = None
        self.old_path: bytes | None = None


def _decode_path(raw: bytes) -> str:
    return sanitize_text(raw.decode("utf-8", errors="replace"), MAX_PATH_CHARS, strip=False)


def _parse_count(raw: bytes) -> int | None:
    if raw.isdigit() and len(raw) <= _NUMBER_DIGITS:
        return int(raw)
    return None


def _add_file(
    record: _Record,
    stats: ParseStats,
    path: bytes,
    counts: tuple[int, int, bool],
    old: bytes | None,
) -> None:
    added, deleted, binary = counts
    record.additions += added
    record.deletions += deleted
    record.file_count += 1
    if len(record.files) >= MAX_FILES_PER_COMMIT:
        stats.omitted_files += 1
        return
    record.files.append(
        FileChange(
            path=_decode_path(path),
            additions=added,
            deletions=deleted,
            binary=binary,
            old_path=_decode_path(old) if old is not None else None,
        )
    )


def _feed_numstat(record: _Record, token: bytes, stats: ParseStats) -> None:
    """Consume one numstat token of a ``-z`` log; unrecognised tokens are counted and ignored."""
    if record.pending is not None:
        if record.old_path is None:
            record.old_path = token
            return
        _add_file(record, stats, token, record.pending, record.old_path)
        record.pending = None
        record.old_path = None
        return
    head = token[1:] if token.startswith(b"\n") else token
    parts = head.split(b"\t", 2)
    if len(parts) != 3:
        stats.noise_tokens += 1
        return
    added_raw, deleted_raw, path = parts
    binary = added_raw == b"-" or deleted_raw == b"-"
    added = 0 if added_raw == b"-" else _parse_count(added_raw)
    deleted = 0 if deleted_raw == b"-" else _parse_count(deleted_raw)
    if added is None or deleted is None:
        stats.noise_tokens += 1
        return
    counts = (0, 0, True) if binary else (added, deleted, False)
    if path:
        _add_file(record, stats, path, counts, None)
    else:
        record.pending = counts  # a rename: the old and new paths follow as separate tokens


def _build_commit(record: _Record, stats: ParseStats) -> Commit | None:
    if len(record.header) < HEADER_FIELDS or record.pending is not None:
        stats.malformed += 1
        return None
    sha_raw, parents_raw, name_raw, email_raw, date_raw, subject_raw = record.header
    sha = sha_raw.decode("ascii", errors="replace")
    parents = tuple(parents_raw.decode("ascii", errors="replace").split())
    moment = parse_git_timestamp(date_raw.decode("ascii", errors="replace"))
    if (
        not _SHA.fullmatch(sha)
        or moment is None
        or not all(_SHA.fullmatch(parent) for parent in parents)
    ):
        stats.malformed += 1
        return None
    stats.records += 1
    return Commit(
        sha=sha,
        parents=parents,
        author=sanitize_text(name_raw.decode("utf-8", errors="replace"), MAX_NAME_CHARS)
        or "Unknown",
        email=sanitize_text(email_raw.decode("utf-8", errors="replace"), MAX_NAME_CHARS),
        authored_at=moment,
        subject=sanitize_text(subject_raw.decode("utf-8", errors="replace"), MAX_SUBJECT_CHARS),
        additions=record.additions,
        deletions=record.deletions,
        file_count=record.file_count,
        files=tuple(record.files),
    )


def parse_log(chunks: Iterable[bytes], nonce: str, stats: ParseStats) -> Iterator[Commit]:
    """Parse the byte stream of ``git log -z --numstat`` into commits, incrementally.

    ``nonce`` is the random marker embedded in the log format. Malformed records are
    counted in ``stats`` and skipped; this function never raises on bad input.
    """
    marker = b"\x01" + nonce.encode("ascii")
    record: _Record | None = None
    for token in iter_tokens(chunks, stats):
        if token == marker:
            if record is not None:
                commit = _build_commit(record, stats)
                if commit is not None:
                    yield commit
            record = _Record()
        elif record is None:
            stats.noise_tokens += 1
        elif len(record.header) < HEADER_FIELDS:
            record.header.append(token)
        else:
            _feed_numstat(record, token, stats)
    if record is not None:
        if stats.incomplete_tail:
            stats.malformed += 1  # the stream was cut off inside this record
        else:
            commit = _build_commit(record, stats)
            if commit is not None:
                yield commit


# ----------------------------------------------------------------------------- public API


def iter_commits(
    repo: RepoInfo,
    filters: Filters,
    *,
    stats: ParseStats | None = None,
    timeout: float | None = None,
    git: str | None = None,
) -> Iterator[Commit]:
    """Stream the commits of ``repo`` that match ``filters``, newest first.

    git starts when iteration starts and is stopped as soon as the iterator is exhausted,
    closed, or ``filters.max_commits`` is reached. Raises :class:`GitCommandError` (or its
    :class:`GitTimeoutError` subclass) if git fails or exceeds ``timeout`` seconds.
    """
    stats = stats if stats is not None else ParseStats()
    nonce = secrets.token_hex(12)
    argv = build_log_argv(git or find_git(), repo, filters, nonce)
    process = _GitProcess(argv, timeout=timeout)
    try:
        for produced, commit in enumerate(parse_log(process.chunks(), nonce, stats), start=1):
            yield commit
            if filters.max_commits is not None and produced >= filters.max_commits:
                return
        process.finish()
    finally:
        process.close()


def list_tags(
    repo: RepoInfo,
    filters: Filters,
    *,
    git: str | None = None,
    timeout: float | None = 60,
) -> list[Tag]:
    """Return the tags reachable from the analysed revision, oldest first.

    Annotated tags use the tagger date, lightweight tags the commit date. Tags outside the
    ``since``/``until`` window are dropped. At most ``MAX_TAGS`` (the newest) are returned.
    """
    code, out, _ = _run(
        git or find_git(),
        [
            "-C",
            repo.root,
            "for-each-ref",
            "--sort=-creatordate",
            f"--count={MAX_TAGS}",
            f"--merged={repo.tip}",
            f"--format={_TAG_FORMAT}",
            "refs/tags",
        ],
        timeout=timeout,
    )
    if code != 0:
        raise GitCommandError("git could not list the repository's tags")
    tags: list[Tag] = []
    for line in out.split("\n"):
        fields = line.split("\0")
        if len(fields) != 5 or not fields[0]:
            continue
        name, object_type, _peeled, _direct, stamp = fields
        created = parse_git_timestamp(stamp)
        if created is None:
            continue
        if (filters.since and created < filters.since) or (
            filters.until and created > filters.until
        ):
            continue
        tags.append(
            Tag(name=sanitize_text(name, 100), created_at=created, annotated=object_type == "tag")
        )
    tags.sort(key=lambda tag: (tag.created_at.astimezone(UTC), tag.name))
    return tags


def collect_history(
    path: str | os.PathLike[str],
    filters: Filters | None = None,
    *,
    timeout: float | None = None,
    git: str | None = None,
) -> History:
    """Open ``path`` and return a :class:`History` for :func:`gitstory.metrics.compute_metrics`.

    Repository problems (not a repository, no commits, unknown branch) raise immediately;
    the commit stream itself is lazy. ``timeout`` bounds each git invocation in seconds.
    """
    filters = filters or Filters()
    git = git or find_git()
    repo = open_repository(path, branch=filters.branch, git=git)
    warnings: list[str] = []
    if repo.shallow:
        warnings.append(
            "This is a shallow clone: history before the shallow boundary is missing, so "
            "totals, streaks and first-commit dates are incomplete. "
            "Run `git fetch --unshallow` for the full story."
        )
    tags = list_tags(repo, filters, git=git)
    if len(tags) >= MAX_TAGS:
        warnings.append(f"Only the newest {MAX_TAGS} tags are considered for release cadence.")
    stats = ParseStats()
    commits = iter_commits(repo, filters, stats=stats, timeout=timeout, git=git)
    return History(
        repo=repo, commits=commits, filters=filters, tags=tags, warnings=warnings, stats=stats
    )
