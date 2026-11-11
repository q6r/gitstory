"""Churn hotspots and the language breakdown."""

from __future__ import annotations

import pytest
from builders import commit

from gitstory.metrics import files as files_module
from gitstory.metrics.files import (
    MAX_ALIAS_HOPS,
    MIN_HOTSPOT_COMMITS,
    TOP_HOTSPOTS,
    TOP_LANGUAGES,
    FileAccumulator,
)
from gitstory.model import Commit, FileChange


def accumulate(*commits: Commit) -> FileAccumulator:
    """Feed commits in the order given: newest first, like ``git log``."""
    acc = FileAccumulator()
    for item in commits:
        acc.add(item)
    return acc


def paths(acc: FileAccumulator, top: int = TOP_HOTSPOTS) -> list[str]:
    return [entry["path"] for entry in acc.hotspots_section(top)["files"]]


# ---------------------------------------------------------------------------- hotspots


def test_hotspots_rank_by_changed_lines_then_commits_then_path() -> None:
    acc = accumulate(
        commit(
            files=[("big.py", 10, 0), ("tie_b.py", 5, 0), ("tie_a.py", 5, 0), ("tie_c.py", 4, 0)]
        ),
        commit(
            files=[("big.py", 5, 5), ("tie_b.py", 5, 0), ("tie_a.py", 5, 0), ("tie_c.py", 3, 0)]
        ),
        commit(files=[("tie_c.py", 3, 0)]),
    )
    section = acc.hotspots_section()
    assert [f["path"] for f in section["files"]] == ["big.py", "tie_c.py", "tie_a.py", "tie_b.py"]
    big = section["files"][0]
    assert (big["commits"], big["additions"], big["deletions"], big["changes"]) == (2, 15, 5, 20)
    assert big["language"] == "Python"
    assert section["basis"] == "changedLines"


def test_a_file_touched_once_is_not_a_hotspot_while_files_touched_twice_exist() -> None:
    acc = accumulate(
        commit(files=[("dump.json", 5000, 0), ("core.py", 5, 0)]),
        commit(files=[("core.py", 5, 0)]),
    )
    section = acc.hotspots_section()
    assert [f["path"] for f in section["files"]] == ["core.py"]
    assert section["minCommits"] == MIN_HOTSPOT_COMMITS
    assert section["uniqueFiles"] == 2


def test_when_nothing_was_touched_twice_every_file_is_listed() -> None:
    acc = accumulate(commit(files=[("a.py", 1, 0), ("b.py", 3, 0)]))
    section = acc.hotspots_section()
    assert [f["path"] for f in section["files"]] == ["b.py", "a.py"]
    assert section["minCommits"] == 1


def test_the_hotspot_list_is_cut_at_top_and_shares_use_all_tracked_lines() -> None:
    acc = accumulate(
        commit(files=[(f"f{i}.py", 10 * (i + 1), 0) for i in range(4)] + [("once.py", 60, 0)]),
        commit(files=[(f"f{i}.py", 0, 0) for i in range(4)]),
    )
    assert paths(acc, top=2) == ["f3.py", "f2.py"]
    section = acc.hotspots_section(top=2)
    assert section["uniqueFiles"] == 5
    # the share is relative to every tracked line, "once.py" included (160 in total)
    assert section["files"][0]["share"] == 0.25


def test_no_files_no_hotspots() -> None:
    section = FileAccumulator().hotspots_section()
    assert section["files"] == []
    assert section["uniqueFiles"] == 0
    assert FileAccumulator().languages_section() == {
        "basis": "changedLines",
        "total": 0,
        "items": [],
    }


# ------------------------------------------------------------------------------ renames


def test_a_rename_is_followed_back_through_older_commits() -> None:
    acc = accumulate(
        commit(files=[FileChange("new.py", 1, 1, old_path="old.py")]),  # newest: the rename
        commit(files=[("old.py", 5, 0)]),
        commit(files=[("old.py", 2, 0)]),
    )
    section = acc.hotspots_section()
    assert [(f["path"], f["commits"], f["additions"]) for f in section["files"]] == [
        ("new.py", 3, 8)
    ]
    assert section["uniqueFiles"] == 1


def test_a_chain_of_renames_resolves_to_the_newest_name() -> None:
    acc = accumulate(
        commit(files=[FileChange("c.py", 0, 0, old_path="b.py")]),
        commit(files=[FileChange("b.py", 0, 0, old_path="a.py")]),
        commit(files=[("a.py", 4, 0)]),
    )
    assert acc.resolve("a.py") == "c.py"
    assert paths(acc) == ["c.py"]
    assert acc.hotspots_section()["files"][0]["additions"] == 4


def test_alias_cycles_cannot_hang_the_resolver() -> None:
    acc = FileAccumulator()
    acc._aliases.update({"a": "b", "b": "a"})  # a corrupt state no real history produces
    assert acc.resolve("a") in {"a", "b"}
    chain = {f"p{i}": f"p{i + 1}" for i in range(MAX_ALIAS_HOPS * 3)}
    acc._aliases.update(chain)
    assert acc.resolve("p0") == f"p{MAX_ALIAS_HOPS}"  # gives up after a bounded number of hops


def test_the_language_of_a_change_is_decided_by_the_name_at_that_commit() -> None:
    acc = accumulate(
        commit(files=[FileChange("app.ts", 3, 0, old_path="app.js")]),
        commit(files=[("app.js", 7, 0)]),
    )
    languages = {i["name"]: i["changes"] for i in acc.languages_section()["items"]}
    assert languages == {"JavaScript": 7, "TypeScript": 3}
    # the file itself is listed under its newest name, hence as TypeScript
    assert acc.hotspots_section()["files"][0]["language"] == "TypeScript"


# ------------------------------------------------------------------- generated and binary


def test_lockfiles_bundles_and_vendored_code_are_counted_but_not_ranked() -> None:
    acc = accumulate(
        commit(
            files=[
                ("package-lock.json", 900, 100),
                ("dist/app.min.js", 50, 0),
                ("web/node_modules/x/index.js", 10, 0),
                ("src/real.py", 3, 1),
            ]
        )
    )
    assert acc.touched == 4
    assert acc.ignored == 3
    assert list(acc.files) == ["src/real.py"]
    assert [i["name"] for i in acc.languages_section()["items"]] == ["Python"]
    assert acc.hotspots_section()["ignoredChanges"] == 3


def test_binary_files_are_counted_and_have_no_lines() -> None:
    acc = accumulate(
        commit(files=[FileChange("logo.png", binary=True), ("main.py", 2, 0)]),
        commit(files=[FileChange("logo.png", binary=True), ("main.py", 2, 0)]),
    )
    assert acc.binary == 2
    assert acc.touched == 4
    assert paths(acc) == ["main.py", "logo.png"]  # zero changed lines ranks last
    assert acc.hotspots_section()["files"][1]["changes"] == 0
    assert [i["name"] for i in acc.languages_section()["items"]] == ["Python"]


# ----------------------------------------------------------------------- resource bound


def test_tracking_stops_at_the_file_limit_but_languages_keep_counting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(files_module, "MAX_TRACKED_FILES", 2)
    acc = accumulate(
        commit(files=[("a.py", 1, 0), ("b.py", 2, 0), ("c.py", 4, 0)]),
        commit(files=[("a.py", 8, 0), ("d.py", 16, 0)]),
    )
    assert acc.untracked == 2  # c.py and d.py
    assert acc.hotspots_section()["uniqueFiles"] == 2
    (python,) = acc.languages_section()["items"]
    assert python["changes"] == 1 + 2 + 4 + 8 + 16  # nothing is lost from the language totals
    assert acc.files["a.py"].commits == 2  # files that were already tracked keep updating


# ---------------------------------------------------------------------------- languages


def test_languages_are_ranked_and_the_tail_is_folded_into_other() -> None:
    sizes = [("a.py", 50), ("b.js", 40), ("c.go", 30), ("d.rs", 20), ("e.md", 10), ("f.zzz", 5)]
    acc = accumulate(commit(files=[(path, lines, 0) for path, lines in sizes]))
    section = acc.languages_section(top=2)
    assert section["total"] == 155
    assert [(i["name"], i["changes"], i["other"]) for i in section["items"]] == [
        ("Python", 50, False),
        ("JavaScript", 40, False),
        ("Other", 30 + 20 + 10 + 5, True),  # the tail and the unrecognised files together
    ]
    assert [i["share"] for i in section["items"]] == [0.3226, 0.2581, 0.4194]


def test_the_default_language_limit_and_unknown_extensions() -> None:
    languages = ["a.py", "b.js", "c.go", "d.rs", "e.md", "f.rb", "g.lua", "h.zig"]
    acc = accumulate(commit(files=[(name, 10 - i, 0) for i, name in enumerate(languages)]))
    items = acc.languages_section()["items"]
    assert len(items) == TOP_LANGUAGES + 1
    assert items[-1]["name"] == "Other"
    assert items[-1]["changes"] == 4 + 3  # Lua and Zig, the two smallest languages
    assert [i["other"] for i in items] == [False] * TOP_LANGUAGES + [True]


def test_unrecognised_files_alone_make_an_other_row_but_no_named_language() -> None:
    acc = accumulate(commit(files=[("data.zzz", 7, 3)]))
    (row,) = acc.languages_section()["items"]
    assert (row["name"], row["changes"], row["share"], row["other"]) == ("Other", 10, 1.0, True)


def test_languages_with_no_changed_lines_are_left_out() -> None:
    acc = accumulate(commit(files=[("empty.py", 0, 0), ("real.js", 2, 0)]))
    assert [i["name"] for i in acc.languages_section()["items"]] == ["JavaScript"]
