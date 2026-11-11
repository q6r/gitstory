"""The ``git log -z --numstat`` parser, fed with hand-made byte streams.

The parser never sees a real git here: that makes malformed, truncated, oversized and
hostile output easy to produce.
"""

from __future__ import annotations

import random
from collections.abc import Iterable, Iterator

import pytest

from gitstory import collect
from gitstory.collect import iter_tokens, parse_log
from gitstory.model import Commit, ParseStats

NONCE = "feedfacecafe"
MARKER = b"\x01" + NONCE.encode()
SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40


def record(
    *,
    sha: str = SHA_A,
    parents: str = "",
    name: bytes | str = "Ada",
    email: str = "ada@example.com",
    date: str = "2024-01-02T03:04:05Z",
    subject: bytes | str = "feat: thing",
    stats: Iterable[bytes] = (),
    marker: bytes = MARKER,
) -> bytes:
    """One record in the exact shape ``git log -z --numstat`` produces."""
    as_bytes = [x if isinstance(x, bytes) else x.encode() for x in (name, subject)]
    fields = [marker, sha.encode(), parents.encode(), as_bytes[0], email.encode(), date.encode()]
    fields.append(as_bytes[1])
    out = b"\0".join(fields) + b"\0"
    entries = list(stats)
    if entries:
        out += b"\n" + b"".join(entry + b"\0" for entry in entries)
    return out


def parse(stream: bytes, *, chunk: int | None = None) -> tuple[list[Commit], ParseStats]:
    stats = ParseStats()
    chunks: Iterator[bytes] | list[bytes] = [stream]
    if chunk is not None:
        chunks = [stream[i : i + chunk] for i in range(0, len(stream), chunk)]
    return list(parse_log(chunks, NONCE, stats)), stats


def test_parses_a_simple_commit_with_numstat() -> None:
    stream = record(
        parents=SHA_B, stats=[b"3\t1\tsrc/a.py", b"0\t5\tdocs/b.md"], subject="fix(core)!: x"
    )
    (commit,), stats = parse(stream)
    assert commit.sha == SHA_A
    assert commit.parents == (SHA_B,)
    assert commit.author == "Ada"
    assert commit.subject == "fix(core)!: x"
    assert (commit.additions, commit.deletions, commit.file_count) == (3, 6, 2)
    assert [(f.path, f.additions, f.deletions) for f in commit.files] == [
        ("src/a.py", 3, 1),
        ("docs/b.md", 0, 5),
    ]
    assert not commit.is_merge
    assert stats.records == 1
    assert stats.malformed == stats.noise_tokens == stats.truncated_tokens == 0


def test_keeps_the_authors_offset_and_exposes_utc() -> None:
    (commit,), _ = parse(record(date="2024-01-02T23:30:00-03:00"))
    offset = commit.authored_at.utcoffset()
    assert offset is not None
    assert offset.total_seconds() == -3 * 3600
    assert commit.utc.isoformat() == "2024-01-03T02:30:00+00:00"
    assert commit.authored_at.hour == 23


def test_root_commits_merges_and_empty_commits() -> None:
    stream = (
        record(sha=SHA_A, parents="")
        + record(sha=SHA_B, parents=f"{SHA_A} {SHA_C}", subject="Merge branch 'x'")
        + record(sha=SHA_C, parents=SHA_A)
    )
    commits, _ = parse(stream)
    assert [c.parents for c in commits] == [(), (SHA_A, SHA_C), (SHA_A,)]
    assert [c.is_merge for c in commits] == [False, True, False]
    assert all(c.files == () and c.changes == 0 for c in commits)


def test_binary_files_have_dashes_and_zero_counts() -> None:
    (commit,), _ = parse(record(stats=[b"-\t-\timg.png", b"2\t0\ta.txt"]))
    binary, text = commit.files
    assert binary.binary
    assert (binary.additions, binary.deletions) == (0, 0)
    assert not text.binary
    assert commit.changes == 2


def test_renames_arrive_as_three_tokens_and_keep_both_paths() -> None:
    stats = [b"2\t1\t", b"old/name.py", b"new/name.py", b"1\t0\tother.py"]
    (commit,), parse_stats = parse(record(stats=stats))
    renamed, other = commit.files
    assert (renamed.old_path, renamed.path) == ("old/name.py", "new/name.py")
    assert (renamed.additions, renamed.deletions) == (2, 1)
    assert other.old_path is None
    assert commit.file_count == 2
    assert parse_stats.malformed == 0


def test_binary_rename_and_pure_rename() -> None:
    stats = [b"-\t-\t", b"a.bin", b"b.bin", b"0\t0\t", b"x.txt", b"y.txt"]
    (commit,), _ = parse(record(stats=stats))
    binary, pure = commit.files
    assert binary.binary
    assert (binary.old_path, binary.path) == ("a.bin", "b.bin")
    assert (pure.additions, pure.deletions, pure.binary) == (0, 0, False)


@pytest.mark.parametrize(
    "path",
    [
        'quote"d.txt',
        "-dash.txt",
        "=> arrow.txt",
        "{x => y}.txt",
        "a => b",
        "sp ace/caf\N{LATIN SMALL LETTER E WITH ACUTE}.md",
        "\N{CJK UNIFIED IDEOGRAPH-65E5}\N{CJK UNIFIED IDEOGRAPH-672C}/x.md",
        "tab\there",  # a tab inside the path must not be taken for a field separator
    ],
)
def test_awkward_paths_survive_because_the_stream_is_nul_delimited(path: str) -> None:
    (commit,), stats = parse(record(stats=[b"1\t0\t" + path.encode()]))
    expected = path.replace("\t", "\N{SYMBOL FOR HORIZONTAL TABULATION}")
    assert [f.path for f in commit.files] == [expected]
    assert stats.noise_tokens == 0


def test_control_characters_in_paths_and_names_are_neutralised() -> None:
    stream = record(
        name="Mallory \x1b[31mRED\x1b[0m",
        subject="\x1b]0;pwned\x07 hi",
        stats=[b"1\t0\tnew\nline.txt"],
    )
    (commit,), _ = parse(stream)
    for text in (commit.author, commit.subject, commit.files[0].path):
        assert "\x1b" not in text
        assert "\x07" not in text
        assert "\n" not in text


def test_invalid_utf8_is_replaced_not_fatal() -> None:
    (commit,), _ = parse(record(name=b"Ren\xe9", subject=b"caf\xff\xfe"))
    assert commit.author.startswith("Ren")
    assert "\N{REPLACEMENT CHARACTER}" in commit.author


def test_text_fields_are_bounded() -> None:
    (commit,), _ = parse(record(name="N" * 1000, subject="S" * 5000, email="e" * 1000))
    assert len(commit.author) <= collect.MAX_NAME_CHARS
    assert len(commit.subject) <= collect.MAX_SUBJECT_CHARS
    assert len(commit.email) <= collect.MAX_NAME_CHARS


def test_empty_author_becomes_unknown() -> None:
    (commit,), _ = parse(record(name=""))
    assert commit.author == "Unknown"


def test_sha256_object_ids_are_accepted() -> None:
    (commit,), _ = parse(record(sha="d" * 64, parents="e" * 64))
    assert len(commit.sha) == 64


# ------------------------------------------------------------------------------ malformed


def test_garbage_before_the_first_marker_is_counted_and_ignored() -> None:
    commits, stats = parse(b"warning: something\0\0" + record())
    assert len(commits) == 1
    assert stats.noise_tokens == 2


def test_a_forged_marker_inside_the_subject_cannot_start_a_record() -> None:
    # Repository content cannot know the per-run nonce, so a look-alike marker is just text.
    forged = b"\x01" + b"0123456789ab"
    stream = record(subject=forged + b" fake") + record(sha=SHA_B, stats=[forged + b"\t1\tx"])
    commits, stats = parse(stream)
    assert [c.sha for c in commits] == [SHA_A, SHA_B]
    assert stats.malformed == 0
    assert stats.noise_tokens == 1  # the forged stat entry is just an unreadable token


def test_a_stat_path_equal_to_a_fake_marker_is_not_special() -> None:
    commits, _ = parse(record(stats=[b"1\t0\t\x01" + NONCE.encode()[::-1]]))
    assert len(commits) == 1
    assert commits[0].file_count == 1


@pytest.mark.parametrize(
    "bad",
    [
        {"sha": "zzzz"},
        {"sha": "a" * 39},
        {"sha": "A" * 40},
        {"parents": "not-a-sha"},
        {"parents": f"{SHA_B} xyz"},
        {"date": "yesterday"},
        {"date": "2024-02-30T00:00:00Z"},
        {"date": "2024-01-01T00:00:00+25:00"},
    ],
)
def test_records_with_invalid_headers_are_skipped_and_counted(bad: dict[str, str]) -> None:
    stream = record(**bad) + record(sha=SHA_C)
    commits, stats = parse(stream)
    assert [c.sha for c in commits] == [SHA_C]
    assert stats.malformed == 1


def test_a_record_cut_short_before_the_next_marker_is_skipped() -> None:
    short = b"\0".join([MARKER, SHA_A.encode(), b"", b"Ada"]) + b"\0"
    commits, stats = parse(short + record(sha=SHA_B))
    assert [c.sha for c in commits] == [SHA_B]
    assert stats.malformed == 1


def test_a_rename_missing_its_paths_drops_the_record() -> None:
    stream = record(stats=[b"1\t0\t", b"only-old"]) + record(sha=SHA_B)
    commits, stats = parse(stream)
    assert [c.sha for c in commits] == [SHA_B]
    assert stats.malformed == 1


@pytest.mark.parametrize(
    "junk", [b"not numstat", b"x\ty\tz", b"1\t2", b"99999999999999\t0\tbig", b"-\t3\tmixed"]
)
def test_unreadable_numstat_entries_are_counted_not_fatal(junk: bytes) -> None:
    commits, stats = parse(record(stats=[junk, b"1\t0\tok.txt"]))
    assert len(commits) == 1
    assert commits[0].files[-1].path == "ok.txt"
    if junk != b"-\t3\tmixed":  # one dash alone still means "binary"
        assert stats.noise_tokens == 1


def test_a_stream_cut_off_mid_record_drops_only_the_unfinished_record() -> None:
    whole = record(sha=SHA_A) + record(sha=SHA_B, stats=[b"1\t0\ta.txt"])
    cut = whole[:-5]  # git was killed in the middle of the last record
    commits, stats = parse(cut)
    assert [c.sha for c in commits] == [SHA_A]
    assert stats.incomplete_tail is True
    assert stats.malformed == 1


def test_empty_input_yields_nothing() -> None:
    commits, stats = parse(b"")
    assert commits == []
    assert stats == ParseStats()


def test_oversized_tokens_are_truncated_and_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(collect, "MAX_TOKEN_BYTES", 64)
    (commit,), stats = parse(record(subject="x" * 10_000), chunk=7)
    assert commit.subject == "x" * 64
    assert stats.truncated_tokens == 1


def test_files_beyond_the_per_commit_limit_are_counted_but_not_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(collect, "MAX_FILES_PER_COMMIT", 2)
    entries = [f"{i}\t1\tfile{i}".encode() for i in range(1, 6)]
    (commit,), stats = parse(record(stats=entries))
    assert commit.file_count == 5
    assert len(commit.files) == 2
    assert (commit.additions, commit.deletions) == (15, 5)  # totals still include everything
    assert stats.omitted_files == 3


# ------------------------------------------------------------------- property-style tests


def _random_stream(rng: random.Random, count: int) -> bytes:
    parts = []
    for i in range(count):
        entries = []
        for _ in range(rng.randint(0, 4)):
            kind = rng.random()
            path = f"dir{rng.randint(0, 9)}/file{rng.randint(0, 99)}.py".encode()
            if kind < 0.15:
                entries += [
                    b"%d\t%d\t" % (rng.randint(0, 9), rng.randint(0, 9)),
                    path,
                    path + b".new",
                ]
            elif kind < 0.25:
                entries.append(b"-\t-\t" + path)
            else:
                entries.append(b"%d\t%d\t" % (rng.randint(0, 99), rng.randint(0, 99)) + path)
        parts.append(
            record(
                sha=f"{i:040x}",
                parents=f"{i - 1:040x}" if i else "",
                name=rng.choice(["Ada", "Zoë", "李雷", "x" * 30]),
                subject=rng.choice(["feat: a", "fix: b", "plain subject", "café ☕"]),
                stats=entries,
            )
        )
    return b"".join(parts)


@pytest.mark.parametrize("seed", range(25))
def test_result_does_not_depend_on_how_the_stream_is_chunked(seed: int) -> None:
    rng = random.Random(seed)
    stream = _random_stream(rng, 40)
    reference, ref_stats = parse(stream)
    assert len(reference) == 40
    cuts = sorted(rng.sample(range(1, len(stream)), 25))
    pieces = [stream[a:b] for a, b in zip([0, *cuts], [*cuts, len(stream)], strict=True)]
    stats = ParseStats()
    assert list(parse_log(pieces, NONCE, stats)) == reference
    assert stats == ref_stats


@pytest.mark.parametrize("size", [1, 2, 3, 5, 64])
def test_tokenizer_handles_tiny_chunks(size: int) -> None:
    stream = b"a\0\0bcd\0e\0"
    pieces = [stream[i : i + size] for i in range(0, len(stream), size)]
    assert list(iter_tokens(pieces, ParseStats())) == [b"a", b"", b"bcd", b"e"]


def test_random_garbage_never_raises() -> None:
    rng = random.Random(1234)
    for _ in range(200):
        blob = bytes(rng.getrandbits(8) for _ in range(rng.randint(0, 300)))
        # sprinkle real markers so the state machine is exercised, not just the noise path
        blob = blob.replace(b"\x07", MARKER + b"\0")
        commits, stats = parse(blob, chunk=rng.randint(1, 50))
        assert stats.records == len(commits)
