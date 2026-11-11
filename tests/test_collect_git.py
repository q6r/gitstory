"""The collector against real git: the synthetic repository, its oracle and awkward repositories.

Starting a git process is slow on some platforms, so the tests share one resolved
repository and one full commit list instead of re-reading the history in every test.
"""

from __future__ import annotations

import functools
import os
import shutil
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pytest
from make_repo import (
    HOSTILE_AUTHORS,
    HOSTILE_PATHS,
    SpecCommit,
    SyntheticRepo,
    build_hostile_repo,
    make_real_repo,
    run_git,
)

from gitstory import collect
from gitstory.collect import (
    EmptyRepositoryError,
    GitCommandError,
    GitNotFoundError,
    GitTimeoutError,
    NotARepositoryError,
    RevisionNotFoundError,
    build_log_argv,
    collect_history,
    find_git,
    git_environment,
    iter_commits,
    list_tags,
    open_repository,
)
from gitstory.model import UTC, Commit, ConfigError, Filters, ParseStats, RepoInfo

JOAO = "Jo\N{LATIN SMALL LETTER A WITH TILDE}o Silva"


def expected_keys(commits: list[SpecCommit]) -> Counter[tuple[str, datetime, str]]:
    return Counter((c.author, c.when.astimezone(UTC), c.subject) for c in commits)


def got_keys(commits: list[Commit]) -> Counter[tuple[str, datetime, str]]:
    return Counter((c.author, c.utc, c.subject) for c in commits)


@functools.cache
def resolved(path: Path, branch: str | None = None) -> RepoInfo:
    return open_repository(path, branch=branch)


def collect_all(path: Path, **filters: object) -> list[Commit]:
    selected = Filters(**filters)  # type: ignore[arg-type]
    return list(iter_commits(resolved(path, selected.branch), selected))


@pytest.fixture(scope="module")
def all_commits(synthetic: SyntheticRepo) -> list[Commit]:
    return collect_all(synthetic.path)


@pytest.fixture(scope="module")
def small_repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A real repository made with plain ``git commit`` (three commits)."""
    dates = [f"2024-01-0{i}T10:00:00+00:00" for i in range(1, 4)]
    return make_real_repo(tmp_path_factory.mktemp("small") / "small", dates)


def ancestors(repo: SyntheticRepo, index: int) -> set[int]:
    seen: set[int] = set()
    stack = [index]
    while stack:
        current = stack.pop()
        if current not in seen:
            seen.add(current)
            stack.extend(repo.commits[current].parents)
    return seen


# -------------------------------------------------------------------- the synthetic history


def test_every_commit_matches_the_oracle_exactly(synthetic: SyntheticRepo) -> None:
    history = collect_history(synthetic.path)
    commits = list(history.commits)
    assert len(commits) == len(synthetic.commits) > 700
    assert history.stats.malformed == 0
    assert history.stats.noise_tokens == 0

    def got(c: Commit) -> tuple[object, ...]:
        files = sorted((f.path, f.additions, f.deletions, f.binary, f.old_path) for f in c.files)
        counts = (c.additions, c.deletions, c.is_merge, c.file_count)
        return (c.author, c.utc, c.subject, *counts, tuple(files))

    def want(c: SpecCommit) -> tuple[object, ...]:
        files = sorted((x.path, x.additions, x.deletions, x.binary, x.old_path) for x in c.changes)
        counts = (c.additions, c.deletions, c.is_merge, len(c.changes))
        return (c.author, c.when.astimezone(UTC), c.subject, *counts, tuple(files))

    assert Counter(map(got, commits)) == Counter(map(want, synthetic.commits))


def test_merge_commits_are_flagged_and_carry_no_line_statistics(
    synthetic: SyntheticRepo, all_commits: list[Commit]
) -> None:
    merges = [c for c in all_commits if c.is_merge]
    assert len(merges) == sum(c.is_merge for c in synthetic.commits) > 20
    assert all(len(c.parents) == 2 and c.changes == 0 and c.files == () for c in merges)
    assert all(c.subject.startswith("Merge branch 'feature/") for c in merges)


def test_mailmap_folds_the_old_identity_into_the_canonical_one(
    synthetic: SyntheticRepo, all_commits: list[Commit]
) -> None:
    assert "joao silva" not in {c.author for c in all_commits}
    joao = [c for c in all_commits if c.author == JOAO]
    assert any(c.raw_name == "joao silva" for c in synthetic.commits), "fixture must use .mailmap"
    assert len(joao) == sum(c.author == JOAO for c in synthetic.commits)
    assert {c.email for c in joao} == {"joao@example.com.br"}
    # without the mailmap git itself reports two identities for the same person
    assert "joao silva" in run_git(synthetic.path, "log", "--format=%an").splitlines()


def test_authors_keep_the_timezone_they_recorded(all_commits: list[Commit]) -> None:
    offsets = {c.author: c.authored_at.utcoffset() for c in all_commits}
    assert offsets["Ada Lovelace"] is not None
    assert offsets["Ada Lovelace"].total_seconds() == -3 * 3600
    assert offsets["\N{CJK UNIFIED IDEOGRAPH-674E}\N{CJK UNIFIED IDEOGRAPH-96F7}"] is not None


def test_unicode_names_and_paths_come_through_untouched(all_commits: list[Commit]) -> None:
    assert (
        "Zo\N{LATIN SMALL LETTER E WITH DIAERESIS} M\N{LATIN SMALL LETTER U WITH DIAERESIS}ller"
        in {c.author for c in all_commits}
    )
    paths = {f.path for c in all_commits for f in c.files}
    assert "docs/caf\N{LATIN SMALL LETTER E WITH ACUTE}.md" in paths
    japanese = (
        "\N{CJK UNIFIED IDEOGRAPH-65E5}\N{CJK UNIFIED IDEOGRAPH-672C}\N{CJK UNIFIED IDEOGRAPH-8A9E}"
    )
    assert f"docs/{japanese}.md" in paths


def test_renames_report_both_paths(all_commits: list[Commit]) -> None:
    renames = {(f.old_path, f.path) for c in all_commits for f in c.files if f.old_path}
    assert renames == {
        ("src/app/utils.py", "src/app/helpers.py"),
        ("src/web/index.ts", "src/web/main.ts"),
        ("docs/guide.md", "docs/handbook/guide.md"),
    }


def test_binary_changes_have_zero_line_counts(all_commits: list[Commit]) -> None:
    binaries = [f for c in all_commits for f in c.files if f.binary]
    assert len(binaries) == 2
    assert all(f.path == "assets/logo.png" for f in binaries)
    assert all(f.additions == f.deletions == 0 for f in binaries)


def test_collection_is_deterministic(synthetic: SyntheticRepo, all_commits: list[Commit]) -> None:
    assert collect_all(synthetic.path) == all_commits


def test_subdirectories_resolve_to_the_repository_root(synthetic: SyntheticRepo) -> None:
    inside = open_repository(synthetic.path / "src" / "app")
    top = resolved(synthetic.path)
    assert inside == top
    assert top.name == "story-repo"
    assert top.branch == "main"
    assert not top.detached
    assert len(top.tip) == 40
    assert top.head == top.tip[:12]


# -------------------------------------------------------------------------------- filters


def test_since_and_until_follow_the_committer_date(synthetic: SyntheticRepo) -> None:
    since = datetime(2025, 1, 1, tzinfo=timezone.utc)
    until = datetime(2025, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
    got = collect_all(synthetic.path, since=since, until=until)
    want = [c for c in synthetic.commits if since <= c.when <= until]
    assert got_keys(got) == expected_keys(want)
    assert len(got) > 50


def test_the_boundary_second_is_inclusive(synthetic: SyntheticRepo) -> None:
    target = synthetic.commits[100].when
    got = collect_all(synthetic.path, since=target, until=target)
    assert {c.utc for c in got} == {target.astimezone(UTC)}
    assert len(got) == sum(c.when == target for c in synthetic.commits)


def test_author_filter_is_a_case_insensitive_regex_on_the_mailmapped_identity(
    synthetic: SyntheticRepo,
) -> None:
    ada = collect_all(synthetic.path, author="^ada lovelace")
    want = [c for c in synthetic.commits if c.author == "Ada Lovelace"]
    assert got_keys(ada) == expected_keys(want)
    # the mailmap is applied before matching, on every git version (--use-mailmap): the
    # canonical e-mail finds João's commits made under either identity, the old one finds none
    canonical = collect_all(synthetic.path, author="joao@example.com.br")
    assert len(canonical) == sum(c.author == JOAO for c in synthetic.commits) > 0
    assert collect_all(synthetic.path, author="joao.silva@old") == []


def test_branch_filter_accepts_branches_tags_and_commits(synthetic: SyntheticRepo) -> None:
    tag = next(t for t in synthetic.tags if t.name == "v1.0.0")
    got = collect_all(synthetic.path, branch="v1.0.0")
    assert len(got) == len(ancestors(synthetic, tag.commit_index))
    assert got[0].sha == run_git(synthetic.path, "rev-parse", "v1.0.0^{commit}").strip()
    assert collect_all(synthetic.path, branch=got[0].sha) == got
    assert resolved(synthetic.path, "v1.0.0").branch == "v1.0.0"
    listing = run_git(
        synthetic.path, "for-each-ref", "--format=%(refname:short)", "refs/heads/feature"
    )
    assert collect_all(synthetic.path, branch=listing.split()[0])


def test_path_filter_limits_history_to_commits_touching_the_path(synthetic: SyntheticRepo) -> None:
    got = collect_all(synthetic.path, paths=("docs/",))

    def under_docs(path: str | None) -> bool:
        return (path or "").startswith("docs/")

    touching = [
        c
        for c in synthetic.non_merges
        if any(under_docs(x.path) or under_docs(x.old_path) for x in c.changes)
    ]
    assert got_keys(got) == expected_keys(touching)
    assert all(under_docs(f.path) or under_docs(f.old_path) for c in got for f in c.files)


def test_multiple_paths_are_combined(synthetic: SyntheticRepo) -> None:
    both = collect_all(synthetic.path, paths=("docs/", "scripts/"))
    docs = collect_all(synthetic.path, paths=("docs/",))
    scripts = collect_all(synthetic.path, paths=("scripts/",))
    assert {c.sha for c in both} == {c.sha for c in docs} | {c.sha for c in scripts}


def test_no_merges_leaves_merge_commits_out(synthetic: SyntheticRepo) -> None:
    got = collect_all(synthetic.path, no_merges=True)
    assert len(got) == len(synthetic.non_merges)
    assert not any(c.is_merge for c in got)


def test_max_commits_returns_the_newest_matching_commits(synthetic: SyntheticRepo) -> None:
    got = collect_all(synthetic.path, max_commits=10)
    assert [c.sha for c in got] == run_git(synthetic.path, "rev-list", "-n", "10", "HEAD").split()


def test_filters_combine(synthetic: SyntheticRepo) -> None:
    since = datetime(2024, 1, 1, tzinfo=timezone.utc)
    got = collect_all(synthetic.path, since=since, author="^grace", no_merges=True, max_commits=5)
    assert len(got) == 5
    assert all(c.author == "Grace Hopper" and c.utc >= since and not c.is_merge for c in got)


def test_no_match_is_not_an_error(synthetic: SyntheticRepo) -> None:
    stats = ParseStats()
    stream = iter_commits(
        resolved(synthetic.path), Filters(author="nobody-has-this-name"), stats=stats
    )
    assert list(stream) == []
    assert stats.records == 0


def test_invalid_filter_combinations_are_rejected() -> None:
    later = datetime(2025, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(ConfigError, match="later than"):
        Filters(since=later, until=datetime(2024, 1, 1, tzinfo=timezone.utc))
    with pytest.raises(ConfigError, match="positive"):
        Filters(max_commits=0)


# --------------------------------------------------------------------------------- tags


def test_tags_match_the_oracle(synthetic: SyntheticRepo) -> None:
    tags = list_tags(resolved(synthetic.path), Filters())
    assert [(t.name, t.annotated) for t in tags] == [(t.name, t.annotated) for t in synthetic.tags]
    assert [t.created_at.astimezone(UTC) for t in tags] == [
        t.created_at.astimezone(UTC) for t in synthetic.tags
    ]


def test_tag_dates_respect_the_window_and_the_analysed_revision(synthetic: SyntheticRepo) -> None:
    since = datetime(2024, 1, 1, tzinfo=timezone.utc)
    recent = list_tags(resolved(synthetic.path), Filters(since=since))
    assert recent
    assert all(t.created_at >= since for t in recent)
    until = datetime(2021, 12, 31, tzinfo=timezone.utc)
    early = list_tags(resolved(synthetic.path), Filters(until=until))
    assert [t.name for t in early] == ["v0.1.0", "v0.2.0", "v0.3.0"]
    older = list_tags(resolved(synthetic.path, "v1.0.0"), Filters())
    assert [t.name for t in older] == [t.name for t in synthetic.tags[:4]]


# --------------------------------------------------------------------- repository states


def test_collect_history_bundles_repo_tags_and_warnings(synthetic: SyntheticRepo) -> None:
    history = collect_history(synthetic.path, Filters(max_commits=3))
    assert len(history.tags) == len(synthetic.tags)
    assert history.warnings == []
    assert history.repo == resolved(synthetic.path)
    assert len(list(history.commits)) == 3


def test_an_empty_repository_is_a_clean_error(tmp_path: Path) -> None:
    run_git(tmp_path, "init", "-q", "-b", "main")
    with pytest.raises(EmptyRepositoryError, match="no commits yet"):
        open_repository(tmp_path)
    with pytest.raises(RevisionNotFoundError):
        open_repository(tmp_path, branch="main")


def test_a_directory_that_is_not_a_repository(tmp_path: Path) -> None:
    with pytest.raises(NotARepositoryError, match="not inside a git repository"):
        open_repository(tmp_path)


def test_a_missing_path_and_a_file_are_not_repositories(tmp_path: Path) -> None:
    with pytest.raises(NotARepositoryError, match="not a directory"):
        open_repository(tmp_path / "does-not-exist")
    (tmp_path / "file.txt").write_text("x", encoding="utf-8")
    with pytest.raises(NotARepositoryError, match="not a directory"):
        open_repository(tmp_path / "file.txt")


def test_unknown_branches_and_tags_are_reported(synthetic: SyntheticRepo) -> None:
    with pytest.raises(RevisionNotFoundError, match="unknown branch"):
        open_repository(synthetic.path, branch="no-such-branch")


@pytest.mark.parametrize(
    "branch",
    ["--output=pwned.txt", "-x", "", "a\nb", "x" * 300, "main..HEAD", "a...b", "^main", "HEAD^!"],
)
def test_branch_values_that_are_options_ranges_or_control_text_are_refused_before_git_runs(
    synthetic: SyntheticRepo, branch: str, workdir: Path
) -> None:
    with pytest.raises(ConfigError, match="invalid branch"):
        open_repository(synthetic.path, branch=branch)
    assert not (workdir / "pwned.txt").exists()


@pytest.mark.parametrize(
    "branch", ["main; touch pwned", "$(touch pwned)", "main`touch pwned`", "HEAD~9999"]
)
def test_branch_values_are_never_interpreted_by_a_shell(
    synthetic: SyntheticRepo, branch: str, workdir: Path
) -> None:
    with pytest.raises(RevisionNotFoundError):
        open_repository(synthetic.path, branch=branch)
    assert not (workdir / "pwned").exists()


def test_detached_head_is_handled(small_repo: Path, tmp_path: Path) -> None:
    detached = tmp_path / "detached"
    shutil.copytree(small_repo, detached)
    run_git(detached, "checkout", "-q", "--detach")
    history = collect_history(detached)
    assert history.repo.detached
    assert history.repo.branch is None
    assert len(list(history.commits)) == 3
    assert not collect_history(small_repo).repo.detached


def test_a_shallow_clone_is_flagged(small_repo: Path, tmp_path: Path) -> None:
    shallow = tmp_path / "shallow"
    run_git(tmp_path, "clone", "-q", "--depth", "1", small_repo.as_uri(), str(shallow))
    history = collect_history(shallow)
    assert history.repo.shallow
    assert any("shallow clone" in warning for warning in history.warnings)
    assert len(list(history.commits)) == 1


def test_bare_repositories_work_and_lose_the_git_suffix(small_repo: Path, tmp_path: Path) -> None:
    bare = tmp_path / "project.git"
    run_git(tmp_path, "clone", "-q", "--bare", str(small_repo), str(bare))
    history = collect_history(bare)
    assert history.repo.bare
    assert history.repo.name == "project"
    assert len(list(history.commits)) == 3


def test_the_git_directory_itself_is_accepted(small_repo: Path) -> None:
    info = open_repository(small_repo / ".git")
    assert info.name == "small"
    assert not info.bare


def test_epoch_dated_commits_are_fine(tmp_path: Path) -> None:
    repo = make_real_repo(tmp_path / "old", ["1970-01-01T00:00:00+00:00"])
    (commit,) = collect_history(repo).commits
    assert commit.utc == datetime(1970, 1, 1, tzinfo=UTC)


# ----------------------------------------------------------------- git process handling


def test_the_log_command_is_a_fixed_argv_with_no_shell_and_safe_ordering(small_repo: Path) -> None:
    repo = resolved(small_repo)
    filters = Filters(
        since=datetime(2024, 1, 1, tzinfo=UTC),
        until=datetime(2024, 12, 31, 23, 59, 59, tzinfo=UTC),
        author="a;b $(c)",
        paths=("src/", "--weird"),
        no_merges=True,
        max_commits=7,
    )
    argv = build_log_argv("/usr/bin/git", repo, filters, "N0NCE")
    assert argv[:3] == ["/usr/bin/git", "--no-pager", "-C"]
    for flag in ("--no-ext-diff", "--no-textconv", "--no-color", "--use-mailmap", "--no-merges"):
        assert flag in argv
    assert "--since=2024-01-01T00:00:00Z" in argv
    assert "--until=2024-12-31T23:59:59Z" in argv
    assert "--author=a;b $(c)" in argv
    assert "--regexp-ignore-case" in argv
    assert "--max-count=7" in argv
    dashes = argv.index("--end-of-options")
    assert argv[dashes + 1] == repo.tip  # a full sha: it can never be mistaken for an option
    assert argv[dashes + 2 :] == ["--", "src/", "--weird"]  # paths only after the separator
    assert any(part.startswith("--format=%x01N0NCE%x00") for part in argv)


def test_filter_values_containing_nul_are_rejected(small_repo: Path) -> None:
    repo = resolved(small_repo)
    with pytest.raises(ConfigError, match="NUL"):
        build_log_argv("git", repo, Filters(author="a\0b"), "n")
    with pytest.raises(ConfigError, match="NUL"):
        build_log_argv("git", repo, Filters(paths=("a\0b",)), "n")


def test_the_git_environment_is_scrubbed(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.setenv(name, "/nowhere")
    env = git_environment()
    assert env["GIT_OPTIONAL_LOCKS"] == "0"
    assert env["GIT_PAGER"] == "cat"
    assert env["PAGER"] == "cat"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert not {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"} & set(env)


def test_inherited_repository_variables_cannot_redirect_the_collector(
    small_repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "elsewhere"))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path))
    assert len(list(collect_history(small_repo).commits)) == 3


def test_git_is_looked_up_on_path_only_never_in_the_current_directory(
    workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    planted = workdir / ("git.exe" if os.name == "nt" else "git")
    planted.write_text("#!/bin/sh\necho planted\n", encoding="utf-8")
    monkeypatch.setenv("PATH", os.pathsep.join(["", ".", "relative/bin", str(workdir / "missing")]))
    with pytest.raises(GitNotFoundError, match="not found on PATH"):
        find_git()


def test_find_git_returns_an_absolute_existing_file() -> None:
    found = Path(find_git())
    assert found.is_absolute()
    assert found.is_file()


def test_a_hung_git_is_killed_after_the_timeout() -> None:
    process = collect._GitProcess(
        [sys.executable, "-c", "import time; time.sleep(60)"], timeout=0.5
    )
    started = time.monotonic()
    try:
        assert list(process.chunks()) == []
        with pytest.raises(GitTimeoutError, match="did not finish"):
            process.finish()
    finally:
        process.close()
    assert time.monotonic() - started < 20


def test_a_failing_git_reports_its_error_output() -> None:
    code = "import sys; sys.stderr.write('fatal: the object store is on fire\\n'); sys.exit(128)"
    process = collect._GitProcess([sys.executable, "-c", code], timeout=None)
    try:
        list(process.chunks())
        with pytest.raises(GitCommandError, match="on fire"):
            process.finish()
    finally:
        process.close()


def test_a_missing_executable_is_a_git_error() -> None:
    with pytest.raises(GitCommandError, match="could not run git"):
        collect._GitProcess(["/definitely/not/a/real/git"], timeout=None)


def test_failure_after_partial_output_is_not_hidden(
    small_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = (
        "import sys\n"
        "sys.stdout.buffer.write(b'\\x01N\\0' + b'x' * 10)\n"
        "sys.stdout.flush(); sys.stderr.write('fatal: bad object'); sys.exit(2)\n"
    )
    monkeypatch.setattr(
        collect, "build_log_argv", lambda *_args, **_kwargs: [sys.executable, "-c", script]
    )
    with pytest.raises(GitCommandError, match="bad object"):
        list(iter_commits(resolved(small_repo), Filters(), git="unused"))


def test_dubious_ownership_gets_an_actionable_message() -> None:
    text = collect._describe_failure(128, "fatal: detected dubious ownership in repository at '/x'")
    assert "safe.directory" in text
    assert "git config --global --add" in text


def test_stopping_early_terminates_git(small_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[collect._GitProcess] = []

    class Spy(collect._GitProcess):
        def __init__(self, argv: list[str], *, timeout: float | None) -> None:
            super().__init__(argv, timeout=timeout)
            created.append(self)

    monkeypatch.setattr(collect, "_GitProcess", Spy)
    stream = iter_commits(resolved(small_repo), Filters())
    next(stream)
    stream.close()
    assert created[0]._process.poll() is not None


def test_parse_statistics_are_exposed_to_the_caller(small_repo: Path) -> None:
    stats = ParseStats()
    commits = list(iter_commits(resolved(small_repo), Filters(), stats=stats))
    assert stats.records == len(commits) == 3


def test_repo_info_head_and_detached_properties() -> None:
    info = RepoInfo(name="x", root="/x", tip="a" * 40, branch=None)
    assert info.detached
    assert info.head == "a" * 12


# ----------------------------------------------------------------------- hostile content


@pytest.fixture(scope="module")
def hostile(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, list[str]]:
    path = tmp_path_factory.mktemp("hostile") / "evil.git"
    return path, build_hostile_repo(path)


def test_hostile_repository_is_read_without_evaluating_anything(
    hostile: tuple[Path, list[str]], workdir: Path
) -> None:
    path, _ = hostile
    history = collect_history(path)
    commits = list(history.commits)
    assert len(commits) == len(HOSTILE_AUTHORS)
    assert history.stats.malformed == 0
    # nothing was executed: none of the payloads created files
    assert not any(workdir.glob("pwned*"))
    assert not any(path.parent.glob("pwned*"))
    for commit in commits:
        for text in (commit.author, commit.subject, commit.email, *(f.path for f in commit.files)):
            assert not any(ord(ch) < 0x20 or 0x7F <= ord(ch) < 0xA0 for ch in text), repr(text)
            assert "\N{RIGHT-TO-LEFT OVERRIDE}" not in text


def test_hostile_text_is_bounded_and_kept_as_inert_data(hostile: tuple[Path, list[str]]) -> None:
    path, _ = hostile
    commits = collect_all(path)
    subjects = {c.subject for c in commits}
    assert any(s.startswith("$(touch pwned)") and "{{data}}" in s for s in subjects)
    assert any("<img src=x onerror=alert(1)>" in s for s in subjects)  # data is kept, not "cleaned"
    assert max(len(s) for s in subjects) <= collect.MAX_SUBJECT_CHARS
    assert any(c.author == "{{data}}" for c in commits)


def test_awkward_paths_are_read_back_one_entry_each(hostile: tuple[Path, list[str]]) -> None:
    path, real_paths = hostile
    found = [f.path for c in collect_all(path) for f in c.files]
    # every commit writes every other path, so some paths are touched several times
    touched = sum(len(HOSTILE_PATHS[i::2]) for i in range(len(HOSTILE_AUTHORS)))
    assert len(found) == touched  # nothing was split, merged or dropped
    tab, newline = "\N{SYMBOL FOR HORIZONTAL TABULATION}", "\N{SYMBOL FOR LINE FEED}"
    assert set(found) == {p.replace("\t", tab).replace("\n", newline) for p in real_paths}
