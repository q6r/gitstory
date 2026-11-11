"""The "wrapped" highlights."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from builders import commit, history, tag, when

from gitstory.metrics import compute_metrics
from gitstory.metrics.highlights import build_highlights
from gitstory.model import Commit

NOW = "2024-06-30T12:00:00+00:00"
ORDER = [
    "commits",
    "activeDays",
    "longestStreak",
    "currentStreak",
    "busiestDay",
    "chronotype",
    "topAuthor",
    "busFactor",
    "biggestCommit",
    "topLanguage",
    "hotFile",
    "longestGap",
    "commitStyle",
    "releases",
    "signatureWord",
]


def document(*commits: Commit, tags: tuple[Any, ...] = ()) -> dict[str, Any]:
    return compute_metrics(history(list(commits), tags=tags), now=when(NOW))


def by_id(metrics: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in metrics["highlights"]}


def test_a_history_without_commits_has_no_highlights() -> None:
    assert document()["highlights"] == []


def test_a_single_plain_commit_gets_only_the_highlights_it_can_support() -> None:
    metrics = document(commit("2024-06-29T10:00:00+00:00", subject="init"))
    assert [h["id"] for h in metrics["highlights"]] == [
        "commits",
        "activeDays",
        "longestStreak",
        "currentStreak",
        "busiestDay",
        "topAuthor",
        "busFactor",
        "signatureWord",
    ]
    facts = by_id(metrics)
    assert facts["commits"]["value"] == 1
    assert facts["commits"]["caption"] == "from 2024-06-29 to 2024-06-29, by 1 author"
    assert facts["activeDays"]["caption"] == "100% of the days in the range had a commit"
    assert facts["currentStreak"]["caption"] == "days in a row, since 2024-06-29"
    assert facts["topAuthor"]["caption"] == "1 commit, 100% of the total"
    assert (
        facts["busFactor"]["caption"] == "author wrote at least half of all commits (a heuristic)"
    )
    assert facts["signatureWord"]["caption"] == "used 1 time in commit subjects"


def test_the_synthetic_repository_has_every_highlight_in_a_fixed_order(
    metrics: dict[str, Any],
) -> None:
    assert [h["id"] for h in metrics["highlights"]] == ORDER
    assert len({h["id"] for h in metrics["highlights"]}) == len(ORDER)
    for item in metrics["highlights"]:
        assert set(item) == {"id", "title", "value", "caption"}
        assert item["title"]
        assert item["caption"]


def test_counts_are_numbers_so_that_a_ui_can_animate_them(metrics: dict[str, Any]) -> None:
    facts = by_id(metrics)
    numeric = ("commits", "activeDays", "longestStreak", "currentStreak", "busiestDay")
    for ident in (*numeric, "busFactor", "biggestCommit", "longestGap", "releases"):
        assert isinstance(facts[ident]["value"], int), ident
        assert not isinstance(facts[ident]["value"], bool)
    for ident in ("chronotype", "topAuthor", "topLanguage", "hotFile", "commitStyle"):
        assert isinstance(facts[ident]["value"], str), ident
    assert facts["commitStyle"]["value"].endswith("%")


def test_the_highlights_agree_with_the_rest_of_the_document(metrics: dict[str, Any]) -> None:
    facts = by_id(metrics)
    assert facts["commits"]["value"] == metrics["totals"]["commits"]
    assert facts["activeDays"]["value"] == metrics["totals"]["activeDays"]
    assert facts["longestStreak"]["value"] == metrics["streaks"]["longest"]
    assert facts["busiestDay"]["value"] == metrics["records"]["busiestDay"]["commits"]
    assert facts["longestGap"]["value"] == metrics["records"]["longestGap"]["days"]
    assert facts["topAuthor"]["value"] == metrics["authors"]["items"][0]["name"]
    assert facts["releases"]["value"] == metrics["releases"]["count"]
    assert facts["hotFile"]["value"] == metrics["hotspots"]["files"][0]["path"]


def test_the_highlights_are_a_pure_function_of_the_document(metrics: dict[str, Any]) -> None:
    before = copy.deepcopy(metrics)
    assert build_highlights(metrics) == metrics["highlights"]
    assert metrics == before


def test_authors_are_counted_with_the_right_plural() -> None:
    two = document(
        commit("2024-06-28T10:00:00+00:00", author="Ada"),
        commit("2024-06-29T10:00:00+00:00", author="Bob"),
    )
    assert by_id(two)["commits"]["caption"] == "from 2024-06-28 to 2024-06-29, by 2 authors"
    assert by_id(two)["topAuthor"]["caption"] == "1 commit, 50% of the total"
    assert by_id(two)["busFactor"]["caption"].startswith("author wrote")  # exactly half is enough
    three = document(*[commit(f"2024-06-2{i}T10:00:00+00:00", author=f"P{i}") for i in range(3)])
    assert by_id(three)["busFactor"]["value"] == 2
    assert by_id(three)["busFactor"]["caption"].startswith("authors wrote")


def test_a_lapsed_streak_says_how_long_ago_the_last_commit_was() -> None:
    metrics = document(commit("2024-06-20T10:00:00+00:00"))
    streak = by_id(metrics)["currentStreak"]
    assert streak["value"] == 0
    assert streak["caption"] == "the last commit was 10 days ago"


def test_the_longest_quiet_spell_names_its_dates() -> None:
    metrics = document(
        commit("2024-06-10T10:00:00+00:00"),
        commit("2024-06-01T10:00:00+00:00"),
    )
    gap = by_id(metrics)["longestGap"]
    assert gap["value"] == 8
    assert gap["caption"] == "days without a commit, between 2024-06-01 and 2024-06-10"


def test_the_biggest_commit_quotes_its_subject_and_is_skipped_when_nothing_changed() -> None:
    big = document(commit("2024-06-29T10:00:00+00:00", subject="fix: it", files=[("a.py", 8, 2)]))
    opening, closing = "\N{LEFT DOUBLE QUOTATION MARK}", "\N{RIGHT DOUBLE QUOTATION MARK}"
    assert by_id(big)["biggestCommit"]["value"] == 10
    assert (
        by_id(big)["biggestCommit"]["caption"] == f"changed lines: {opening}fix: it{closing} by Ada"
    )
    empty = document(commit("2024-06-29T10:00:00+00:00", subject="chore: nothing"))
    assert "biggestCommit" not in by_id(empty)


def test_language_and_hot_file_come_from_the_churn_statistics() -> None:
    metrics = document(
        commit("2024-06-29T10:00:00+00:00", files=[("src/core.py", 6, 0), ("README.md", 1, 0)]),
        commit("2024-06-28T10:00:00+00:00", files=[("src/core.py", 3, 1)]),
    )
    facts = by_id(metrics)
    assert facts["topLanguage"]["value"] == "Python"
    assert facts["topLanguage"]["caption"] == "91% of the changed lines"
    assert facts["hotFile"]["value"] == "src/core.py"
    assert facts["hotFile"]["caption"] == "2 commits and 10 changed lines"


def test_the_commit_style_is_the_share_of_conventional_subjects() -> None:
    metrics = document(
        commit("2024-06-26T10:00:00+00:00", subject="feat: a"),
        commit("2024-06-27T10:00:00+00:00", subject="fix: b"),
        commit("2024-06-28T10:00:00+00:00", subject="docs: c"),
        commit("2024-06-29T10:00:00+00:00", subject="just words"),
    )
    style = by_id(metrics)["commitStyle"]
    assert style["value"] == "75%"
    assert style["caption"] == "of commit subjects follow Conventional Commits"


def test_releases_mention_the_cadence_only_when_there_is_one() -> None:
    one = document(
        commit("2024-06-29T10:00:00+00:00"),
        tags=(tag("v1.0.0", "2024-06-01T00:00:00+00:00"),),
    )
    assert by_id(one)["releases"]["caption"] == "tagged, the latest v1.0.0"
    two = document(
        commit("2024-06-29T10:00:00+00:00"),
        tags=(
            tag("v1.0.0", "2024-05-01T00:00:00+00:00"),
            tag("v1.1.0", "2024-05-31T00:00:00+00:00"),
        ),
    )
    assert by_id(two)["releases"]["caption"] == (
        "tagged, the latest v1.1.0, one every 30 days on average"
    )


def test_the_signature_word_is_the_most_frequent_one() -> None:
    metrics = document(
        commit("2024-06-27T10:00:00+00:00", subject="tweak colours"),
        commit("2024-06-28T10:00:00+00:00", subject="tweak spacing"),
        commit("2024-06-29T10:00:00+00:00", subject="tweak the layout"),
    )
    word = by_id(metrics)["signatureWord"]
    assert word["value"] == "tweak"
    assert word["caption"] == "used 3 times in commit subjects"


@pytest.mark.parametrize(
    ("section", "ident"),
    [("authors", "topAuthor"), ("languages", "topLanguage")],
)
def test_a_leader_that_is_only_the_folded_tail_is_not_celebrated(
    metrics: dict[str, Any], section: str, ident: str
) -> None:
    metrics[section]["items"][0]["other"] = True
    assert ident not in {h["id"] for h in build_highlights(metrics)}


def test_a_chronotype_needs_enough_commits_to_be_called() -> None:
    few = document(*[commit(f"2024-06-{day:02d}T23:00:00+00:00") for day in range(1, 6)])
    assert "chronotype" not in by_id(few)
    many = document(*[commit(f"2024-06-{day:02d}T23:00:00+00:00") for day in range(1, 13)])
    night = by_id(many)["chronotype"]
    assert night["value"] == "Night owl"
    assert night["caption"] == "100% of commits land between 22:00 and 04:59"
