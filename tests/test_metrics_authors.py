"""The authors leaderboard and the bus-factor heuristic."""

from __future__ import annotations

import json

import pytest
from builders import commit

from gitstory.metrics.authors import (
    BUS_FACTOR_NOTE,
    TOP_AUTHORS,
    AuthorAccumulator,
    bus_factor,
    identity_key,
)
from gitstory.model import Commit


def accumulate(*commits: Commit) -> AuthorAccumulator:
    acc = AuthorAccumulator()
    for item in commits:
        acc.add(item)
    return acc


def many(author: str, count: int, *, lines: int = 0, day: str = "2024-01-01") -> list[Commit]:
    """``count`` commits by ``author`` on ``day`` (each changing ``lines`` lines)."""
    files = [("f.py", lines, 0)] if lines else []
    return [commit(f"{day}T10:00:00+00:00", author=author, files=files) for _ in range(count)]


# ------------------------------------------------------------------------------ bus factor


@pytest.mark.parametrize(
    ("counts", "expected"),
    [
        ([], 0),
        ([0, 0], 0),
        ([10], 1),
        ([5, 5], 1),  # exactly half is enough
        ([4, 3, 3], 2),
        ([3, 4, 3], 2),  # the order of the input does not matter
        ([1] * 10, 5),
        ([6, 0, 4], 1),  # authors without commits are ignored
        ([-3, 6, 4], 1),  # ... and so are nonsense negatives
    ],
)
def test_bus_factor_is_the_smallest_group_covering_half_of_the_commits(
    counts: list[int], expected: int
) -> None:
    assert bus_factor(counts) == expected


def test_bus_factor_accepts_any_iterable_once() -> None:
    assert bus_factor(count for count in (2, 2, 2, 2)) == 2


# ----------------------------------------------------------------------------- identities


def test_an_identity_is_the_casefolded_email_or_the_name_when_there_is_none() -> None:
    assert identity_key(commit(email="Ada@Example.COM")) == "ada@example.com"
    assert identity_key(commit(author="Ada", email="")) == "name:ada"
    assert identity_key(commit(author="ADA", email="")) == identity_key(
        commit(author="ada", email="")
    )


def test_one_person_under_several_names_is_one_author_and_the_commonest_name_wins() -> None:
    acc = accumulate(
        commit(author="Ada", email="ada@example.com"),
        commit(author="Ada L.", email="ADA@example.com"),
        commit(author="Ada", email="ada@example.com"),
    )
    section = acc.section()
    assert acc.count == section["total"] == 1
    assert [(a["name"], a["commits"]) for a in section["items"]] == [("Ada", 3)]


def test_the_display_name_of_a_tied_author_is_alphabetical() -> None:
    acc = accumulate(
        commit(author="Zed", email="same@example.com"),
        commit(author="Amy", email="same@example.com"),
    )
    assert acc.section()["items"][0]["name"] == "Amy"


def test_authors_without_an_email_are_grouped_by_name() -> None:
    acc = accumulate(
        commit(author="Ghost", email=""),
        commit(author="ghost", email=""),
        commit(author="Other", email=""),
    )
    assert acc.count == 2


# ------------------------------------------------------------------------------- ranking


def test_ranking_is_by_commits_then_changed_lines_then_name() -> None:
    acc = accumulate(
        *many("Carol", 2, lines=10),
        *many("Bob", 2, lines=100),
        *many("Alice", 3),
        *many("dave", 1),
        *many("Dora", 1),
    )
    names = [a["name"] for a in acc.section()["items"]]
    # Alice has most commits; Bob beats Carol on lines; Dora and dave tie and sort by name
    assert names == ["Alice", "Bob", "Carol", "dave", "Dora"]


def test_names_are_compared_case_insensitively_but_deterministically() -> None:
    acc = accumulate(*many("bob", 1), *many("Alice", 1), *many("alice2", 1))
    assert [a["name"] for a in acc.section()["items"]] == ["Alice", "alice2", "bob"]


def test_the_ranking_does_not_depend_on_the_order_commits_arrive_in() -> None:
    pool = [*many("A", 3), *many("B", 2, lines=5), *many("C", 2), *many("D", 1)]
    forward = accumulate(*pool).section()
    backward = accumulate(*reversed(pool)).section()
    assert [a["name"] for a in forward["items"]] == [a["name"] for a in backward["items"]]


# ------------------------------------------------------------------- the "Other" fold


def test_the_tail_is_folded_into_one_other_row() -> None:
    commits: list[Commit] = []
    for index, count in enumerate([9, 7, 5, 3, 2, 1]):
        commits += many(f"Author{index}", count, lines=count)
    section = accumulate(*commits).section(top=3)
    assert section["total"] == 6
    assert section["limit"] == 3
    names = [(a["name"], a["other"]) for a in section["items"]]
    assert names == [("Author0", False), ("Author1", False), ("Author2", False), ("Other", True)]
    other = section["items"][-1]
    assert other["commits"] == 3 + 2 + 1
    assert other["members"] == 3
    assert other["additions"] == 3 * 3 + 2 * 2 + 1 * 1  # `count` commits of `count` lines each
    assert sum(a["share"] for a in section["items"]) == pytest.approx(1.0, abs=0.001)


def test_no_other_row_when_everyone_fits() -> None:
    section = accumulate(*many("A", 2), *many("B", 1)).section(top=2)
    assert [a["name"] for a in section["items"]] == ["A", "B"]
    assert not any(a["other"] for a in section["items"])


def test_the_default_limit_is_seven_authors_plus_other() -> None:
    commits = [item for i in range(TOP_AUTHORS + 1) for item in many(f"P{i}", 1)]
    items = accumulate(*commits).section()["items"]
    assert len(items) == TOP_AUTHORS + 1
    assert items[-1]["other"] is True
    assert items[-1]["members"] == 1


def test_the_fold_counts_shared_days_once_and_keeps_the_widest_date_range() -> None:
    acc = accumulate(
        *many("X", 1, day="2024-01-01"),
        *many("X", 1, day="2024-01-02"),
        *many("Y", 1, day="2024-01-02"),
        *many("Y", 1, day="2024-01-05"),
    )
    (other,) = acc.section(top=0)["items"]
    assert other["other"] is True
    assert other["members"] == 2
    assert other["commits"] == 4
    assert other["activeDays"] == 3  # Jan 1, 2 and 5: Jan 2 is shared
    assert other["firstCommitAt"] == "2024-01-01T10:00:00Z"
    assert other["lastCommitAt"] == "2024-01-05T10:00:00Z"


# ------------------------------------------------------------------------- facts per author


def test_active_days_are_the_authors_local_days_and_instants_are_utc() -> None:
    acc = accumulate(
        commit("2024-03-01T23:30:00-03:00", author="Ana"),  # 2024-03-02T02:30Z
        commit("2024-03-02T10:00:00-03:00", author="Ana"),  # 2024-03-02T13:00Z
    )
    (ana,) = acc.section()["items"]
    assert ana["activeDays"] == 2  # two local days, although both fall on Mar 2 in UTC
    assert ana["firstCommitAt"] == "2024-03-02T02:30:00Z"
    assert ana["lastCommitAt"] == "2024-03-02T13:00:00Z"


def test_lines_and_shares_are_summed_per_author() -> None:
    acc = accumulate(
        commit(author="A", files=[("a.py", 10, 2), ("b.py", 1, 1)]),
        commit(author="A", files=[("a.py", 5, 0)]),
        commit(author="B", files=[("a.py", 0, 7)]),
    )
    a, b = acc.section()["items"]
    assert (a["additions"], a["deletions"]) == (16, 3)
    assert (b["additions"], b["deletions"]) == (0, 7)
    assert (a["share"], b["share"]) == (0.6667, 0.3333)


def test_an_empty_accumulator_has_an_empty_leaderboard() -> None:
    acc = AuthorAccumulator()
    assert acc.section() == {"total": 0, "limit": TOP_AUTHORS, "items": []}
    assert acc.bus_factor_section() == {
        "value": 0,
        "threshold": 0.5,
        "coverage": 0.0,
        "authors": [],
        "heuristic": True,
        "note": BUS_FACTOR_NOTE,
    }


def test_email_addresses_never_reach_the_output() -> None:
    acc = accumulate(
        commit(author="Ada", email="ada.secret@corp.example"),
        commit(author="Bob", email="bob.secret@corp.example"),
    )
    text = json.dumps([acc.section(), acc.bus_factor_section()])
    assert "secret" not in text
    assert "corp.example" not in text


# ------------------------------------------------------------------------ bus factor section


def test_the_bus_factor_section_names_who_covers_half_of_the_commits() -> None:
    acc = accumulate(*many("Big", 6), *many("Mid", 3), *many("Tiny", 1))
    section = acc.bus_factor_section()
    assert section["value"] == 1
    assert section["authors"] == ["Big"]
    assert section["coverage"] == 0.6
    assert section["heuristic"] is True
    assert "heuristic" in section["note"]


def test_the_bus_factor_grows_when_work_is_spread_out() -> None:
    acc = accumulate(*many("A", 3), *many("B", 3), *many("C", 3), *many("D", 1))
    section = acc.bus_factor_section()
    assert section["value"] == 2
    assert section["authors"] == ["A", "B"]
    assert section["coverage"] == 0.6
