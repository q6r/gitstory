"""The assembled ``gitstory.metrics/v1`` document, checked against the synthetic repository."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest
from builders import commit, history, when
from make_repo import (
    FINAL_STREAK,
    GAP_FIRST,
    GAP_LAST,
    HACK_DAY,
    HACK_DAY_COMMITS,
    IMPORT_LINES,
    STREAK_FIRST,
    STREAK_LAST,
    SyntheticRepo,
)

from gitstory import __version__
from gitstory.metrics import SCHEMA, compute_metrics
from gitstory.metrics import files as files_module
from gitstory.model import UTC, Filters, History, ParseStats, RepoInfo
from gitstory.util import iso_utc, to_json

SECTIONS = [
    "schema",
    "generator",
    "generatedAt",
    "repository",
    "filters",
    "warnings",
    "totals",
    "activity",
    "streaks",
    "authors",
    "busFactor",
    "hotspots",
    "languages",
    "commitSize",
    "conventional",
    "words",
    "chronotype",
    "records",
    "releases",
    "highlights",
]


# ------------------------------------------------------------------------------ the shape


def test_the_document_has_exactly_the_documented_sections_in_order(
    metrics: dict[str, Any],
) -> None:
    assert list(metrics) == SECTIONS
    assert metrics["schema"] == SCHEMA == "gitstory.metrics/v1"
    assert metrics["generator"] == {"name": "gitstory", "version": __version__}
    assert metrics["generatedAt"] == "2026-11-10T12:00:00Z"


def test_the_document_is_plain_json_without_nan_and_survives_a_round_trip(
    metrics: dict[str, Any],
) -> None:
    text = to_json(metrics)  # raises on NaN and Infinity
    assert json.loads(text) == metrics
    assert text == to_json(json.loads(text))


def test_computing_again_gives_identical_bytes(
    metrics: dict[str, Any], synthetic: SyntheticRepo
) -> None:
    from gitstory.collect import collect_history

    again = compute_metrics(collect_history(synthetic.path), now=synthetic.now)
    assert to_json(again) == to_json(metrics)


def test_no_e_mail_address_leaves_the_repository(
    metrics: dict[str, Any], synthetic: SyntheticRepo
) -> None:
    text = to_json(metrics)
    for item in synthetic.commits:
        assert item.email not in text
        assert item.raw_email not in text
    assert "@example" not in text


def test_the_repository_section_describes_the_checkout(metrics: dict[str, Any]) -> None:
    repo = metrics["repository"]
    assert repo["name"] == "story-repo"
    assert repo["branch"] == "main"
    assert repo["detached"] is False
    assert repo["shallow"] is False
    assert len(repo["head"]) == 12


def test_the_filters_are_echoed_back() -> None:
    filters = Filters(
        since=when("2024-01-01T00:00:00Z"),
        until=when("2024-12-31T23:59:59Z"),
        author="ada",
        paths=("src",),
        no_merges=True,
        max_commits=50,
    )
    commits = [commit("2024-03-01T10:00:00+00:00"), commit("2024-02-01T10:00:00+00:00")]
    document = compute_metrics(history(commits, filters=filters), now=when("2026-11-10T12:00:00Z"))
    assert document["filters"] == {
        "since": "2024-01-01T00:00:00Z",
        "until": "2024-12-31T23:59:59Z",
        "author": "ada",
        "branch": None,
        "paths": ["src"],
        "noMerges": True,
        "maxCommits": 50,
    }
    assert document["activity"]["asOf"] == "2024-12-31"  # --until is the "as of" moment


# ----------------------------------------------------------------------------- the oracle


def test_the_totals_match_the_oracle(metrics: dict[str, Any], synthetic: SyntheticRepo) -> None:
    commits = synthetic.commits
    totals = metrics["totals"]
    assert totals["commits"] == len(commits)
    assert totals["mergeCommits"] == sum(c.is_merge for c in commits)
    assert totals["authors"] == len({c.email.casefold() for c in commits})
    assert totals["additions"] == sum(c.additions for c in commits)
    assert totals["deletions"] == sum(c.deletions for c in commits)
    assert totals["activeDays"] == len({c.local_day for c in commits})
    first, last = min(c.when for c in commits), max(c.when for c in commits)
    assert totals["firstCommitAt"] == iso_utc(first)
    assert totals["lastCommitAt"] == iso_utc(last)
    assert totals["spanDays"] == (last.astimezone(UTC) - first.astimezone(UTC)).days + 1
    assert totals["commitsPerActiveDay"] == round(len(commits) / totals["activeDays"], 2)
    binary = sum(change.binary for c in commits for change in c.changes)
    assert totals["binaryChanges"] == binary > 0


def test_the_planted_facts_are_found(metrics: dict[str, Any]) -> None:
    records, streaks = metrics["records"], metrics["streaks"]
    quiet = (GAP_LAST - GAP_FIRST).days + 1
    assert records["longestGap"] == {
        "days": quiet,
        "from": date.fromordinal(GAP_FIRST.toordinal() - 1).isoformat(),
        "to": date.fromordinal(GAP_LAST.toordinal() + 1).isoformat(),
    }
    assert streaks["longest"] == (STREAK_LAST - STREAK_FIRST).days + 1
    assert (streaks["longestFrom"], streaks["longestTo"]) == (
        STREAK_FIRST.isoformat(),
        STREAK_LAST.isoformat(),
    )
    assert records["busiestDay"] == {"date": HACK_DAY.isoformat(), "commits": HACK_DAY_COMMITS}
    assert records["biggestCommit"]["additions"] == IMPORT_LINES
    assert records["biggestCommit"]["subject"]


def test_the_streak_that_is_still_alive_on_the_fixed_clock(metrics: dict[str, Any]) -> None:
    streaks = metrics["streaks"]
    assert streaks["current"] == len(FINAL_STREAK)
    assert streaks["currentFrom"] == FINAL_STREAK[0].isoformat()
    assert streaks["daysSinceLastCommit"] == 1  # the last commit was on Nov 9, "today" is Nov 10


def test_releases_match_the_tags(metrics: dict[str, Any], synthetic: SyntheticRepo) -> None:
    releases = metrics["releases"]
    assert releases["count"] == len(synthetic.tags)
    assert releases["annotated"] == sum(t.annotated for t in synthetic.tags)
    assert releases["latest"]["name"] == max(synthetic.tags, key=lambda t: t.created_at).name
    assert sum(item["releases"] for item in releases["perYear"]) == releases["count"]


def test_every_series_adds_up_to_the_number_of_commits(metrics: dict[str, Any]) -> None:
    total = metrics["totals"]["commits"]
    activity = metrics["activity"]
    assert sum(row["commits"] for row in activity["byDay"]) == total
    assert sum(row["commits"] for row in activity["byWeek"]) == total
    assert sum(row["commits"] for row in activity["byMonth"]) == total
    assert sum(row["commits"] for row in activity["byYear"]) == total
    assert sum(sum(row) for row in activity["weekdayHour"]["matrix"]) == total
    assert metrics["chronotype"]["total"] == total
    assert sum(metrics["chronotype"]["hours"]) == total
    assert sum(item["commits"] for item in metrics["authors"]["items"]) == total
    merges = metrics["totals"]["mergeCommits"]
    assert metrics["commitSize"]["commits"] == total - merges
    assert metrics["conventional"]["considered"] == total - merges
    assert sum(b["commits"] for b in metrics["commitSize"]["buckets"]) == total - merges


def test_shares_are_fractions_that_add_up(metrics: dict[str, Any]) -> None:
    assert sum(a["share"] for a in metrics["authors"]["items"]) == pytest.approx(1, abs=0.002)
    assert sum(i["share"] for i in metrics["languages"]["items"]) == pytest.approx(1, abs=0.002)
    assert sum(t["share"] for t in metrics["conventional"]["types"]) == pytest.approx(1, abs=0.003)
    for entry in metrics["hotspots"]["files"]:
        assert 0 <= entry["share"] <= 1
        assert entry["commits"] >= metrics["hotspots"]["minCommits"]


def test_the_calendar_is_a_dense_monday_first_grid_ending_on_as_of(
    metrics: dict[str, Any],
) -> None:
    calendar = metrics["activity"]["calendar"]
    assert calendar["weekStart"] == "mon"
    assert calendar["to"] == metrics["activity"]["asOf"] == "2026-11-10"
    assert date.fromisoformat(calendar["from"]).weekday() == 0
    assert (
        len(calendar["days"])
        == (date.fromisoformat(calendar["to"]) - date.fromisoformat(calendar["from"])).days + 1
    )
    assert calendar["max"] == max(day["commits"] for day in calendar["days"])
    assert {day["level"] for day in calendar["days"]} <= {0, 1, 2, 3, 4, 5}


def test_the_synthetic_repository_produces_no_warnings(metrics: dict[str, Any]) -> None:
    assert metrics["warnings"] == []


# ------------------------------------------------------------------------- small histories


def test_an_empty_history_is_a_valid_document() -> None:
    document = compute_metrics(history([]), now=when("2024-06-30T12:00:00Z"))
    assert list(document) == SECTIONS
    assert document["totals"]["commits"] == 0
    assert document["totals"]["firstCommitAt"] is None
    assert document["warnings"] == ["No commits matched the given filters."]
    assert document["highlights"] == []
    assert document["authors"]["items"] == []
    assert document["busFactor"]["value"] == 0
    assert document["streaks"]["current"] == 0
    assert document["records"] == {"biggestCommit": None, "busiestDay": None, "longestGap": None}
    assert document["chronotype"]["label"] == "Not enough data"
    assert document["activity"]["byDay"] == []
    assert document["activity"]["calendar"]["total"] == 0
    to_json(document)


def test_a_one_shot_stream_is_consumed_exactly_once() -> None:
    commits = [commit(f"2024-06-{day:02d}T10:00:00+00:00") for day in range(1, 11)]
    repo = RepoInfo(name="demo", root="/demo", tip="f" * 40, branch="main")
    stream = History(repo=repo, commits=(item for item in commits))
    document = compute_metrics(stream, now=when("2024-06-30T12:00:00Z"))
    assert document["totals"]["commits"] == 10
    assert document["totals"]["activeDays"] == 10


def test_until_moves_the_as_of_moment_so_streaks_describe_that_window() -> None:
    commits = [commit("2024-01-11T10:00:00+00:00"), commit("2024-01-10T10:00:00+00:00")]
    now = when("2024-06-30T12:00:00Z")
    unfiltered = compute_metrics(history(commits), now=now)
    assert unfiltered["streaks"]["current"] == 0
    assert unfiltered["streaks"]["daysSinceLastCommit"] == 171
    bounded = compute_metrics(
        history(commits, filters=Filters(until=when("2024-01-12T23:59:59Z"))), now=now
    )
    assert bounded["activity"]["asOf"] == "2024-01-12"
    assert bounded["streaks"]["current"] == 2
    assert bounded["streaks"]["daysSinceLastCommit"] == 1


def test_parser_statistics_become_warnings() -> None:
    commits = [commit("2024-06-01T10:00:00+00:00")]
    repo = RepoInfo(name="demo", root="/demo", tip="f" * 40, branch="main")
    stats = ParseStats(
        malformed=2, noise_tokens=1, truncated_tokens=3, omitted_files=4, incomplete_tail=True
    )
    source = History(repo=repo, commits=commits, stats=stats, warnings=["from the collector"])
    warnings = compute_metrics(source, now=when("2024-06-30T12:00:00Z"))["warnings"]
    assert warnings[0] == "from the collector"
    text = "\n".join(warnings)
    assert "2 commit records" in text
    assert "1 unexpected values" in text
    assert "3 over-long values" in text
    assert "4 file entries" in text
    assert "ended unexpectedly" in text
    assert len(warnings) == 6


def test_the_file_tracking_limit_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    import gitstory.metrics as package

    monkeypatch.setattr(files_module, "MAX_TRACKED_FILES", 1)
    monkeypatch.setattr(package, "MAX_TRACKED_FILES", 1)
    commits = [commit("2024-06-01T10:00:00+00:00", files=[("a.py", 1, 0), ("b.py", 1, 0)])]
    document = compute_metrics(history(commits), now=when("2024-06-30T12:00:00Z"))
    assert any("tracking stopped at 1 files; 1 later" in w for w in document["warnings"])
    assert document["hotspots"]["uniqueFiles"] == 1
    assert document["totals"]["filesChanged"] == 2
