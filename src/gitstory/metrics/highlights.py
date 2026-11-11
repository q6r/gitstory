"""The "wrapped" highlights: a short, ordered list of headline facts for the story view.

Each highlight is ``{"id", "title", "value", "caption"}``. ``value`` is a number when the
fact is a count (so a UI can animate it) and text otherwise. Highlights whose data is
missing (no tags, no streak, no commits) are simply left out.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _quoted(text: str) -> str:
    return f"\N{LEFT DOUBLE QUOTATION MARK}{text}\N{RIGHT DOUBLE QUOTATION MARK}"


def _percent(share: float) -> str:
    return f"{round(share * 100)}%"


def _window_text(start: int, end: int) -> str:
    return f"{start:02d}:00 and {(end - 1) % 24:02d}:59"


def _plural(count: int, word: str) -> str:
    return f"{count:,} {word}" if count == 1 else f"{count:,} {word}s"


def build_highlights(metrics: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Derive the highlights from an assembled metrics document."""
    totals = metrics["totals"]
    if not totals["commits"]:
        return []
    highlights: list[dict[str, Any]] = []

    def add(ident: str, title: str, value: int | str, caption: str) -> None:
        highlights.append({"id": ident, "title": title, "value": value, "caption": caption})

    first, last = str(totals["firstCommitAt"])[:10], str(totals["lastCommitAt"])[:10]
    add(
        "commits",
        "Commits",
        totals["commits"],
        f"from {first} to {last}, by {_plural(totals['authors'], 'author')}",
    )
    active_share = totals["activeDays"] / totals["spanDays"] if totals["spanDays"] else 0
    add(
        "activeDays",
        "Active days",
        totals["activeDays"],
        f"{_percent(active_share)} of the days in the range had a commit",
    )

    streaks = metrics["streaks"]
    if streaks["longest"]:
        add(
            "longestStreak",
            "Longest streak",
            streaks["longest"],
            f"days in a row, from {streaks['longestFrom']} to {streaks['longestTo']}",
        )
        if streaks["current"]:
            caption = f"days in a row, since {streaks['currentFrom']}"
        else:
            caption = f"the last commit was {_plural(streaks['daysSinceLastCommit'], 'day')} ago"
        add("currentStreak", "Current streak", streaks["current"], caption)

    busiest = metrics["records"]["busiestDay"]
    if busiest:
        add("busiestDay", "Busiest day", busiest["commits"], f"commits on {busiest['date']}")

    chrono = metrics["chronotype"]
    if chrono["type"]:
        window = next(w for w in chrono["windows"] if w["id"] == chrono["type"])
        add(
            "chronotype",
            "Chronotype",
            chrono["label"],
            f"{_percent(window['share'])} of commits land between "
            f"{_window_text(window['from'], window['to'])}",
        )

    leaders = metrics["authors"]["items"]
    if leaders and not leaders[0]["other"]:
        top = leaders[0]
        add(
            "topAuthor",
            "Top author",
            top["name"],
            f"{_plural(top['commits'], 'commit')}, {_percent(top['share'])} of the total",
        )
    bus = metrics["busFactor"]
    if bus["value"]:
        noun = "author wrote" if bus["value"] == 1 else "authors wrote"
        add(
            "busFactor",
            "Bus factor",
            bus["value"],
            f"{noun} at least half of all commits (a heuristic)",
        )

    biggest = metrics["records"]["biggestCommit"]
    if biggest and biggest["additions"] + biggest["deletions"]:
        add(
            "biggestCommit",
            "Biggest commit",
            biggest["additions"] + biggest["deletions"],
            f"changed lines: {_quoted(biggest['subject'])} by {biggest['author']}",
        )

    languages = metrics["languages"]["items"]
    if languages and not languages[0]["other"]:
        add(
            "topLanguage",
            "Top language",
            languages[0]["name"],
            f"{_percent(languages[0]['share'])} of the changed lines",
        )
    hotspots = metrics["hotspots"]["files"]
    if hotspots:
        hot = hotspots[0]
        add(
            "hotFile",
            "Hottest file",
            hot["path"],
            f"{_plural(hot['commits'], 'commit')} and {_plural(hot['changes'], 'changed line')}",
        )

    gap = metrics["records"]["longestGap"]
    if gap:
        add(
            "longestGap",
            "Longest quiet spell",
            gap["days"],
            f"days without a commit, between {gap['from']} and {gap['to']}",
        )

    style = metrics["conventional"]
    if style["typed"]:
        add(
            "commitStyle",
            "Commit style",
            _percent(style["share"]),
            "of commit subjects follow Conventional Commits",
        )

    releases = metrics["releases"]
    if releases["count"]:
        cadence = (
            f", one every {releases['meanIntervalDays']:g} days on average"
            if releases["meanIntervalDays"] is not None
            else ""
        )
        add(
            "releases",
            "Releases",
            releases["count"],
            f"tagged, the latest {releases['latest']['name']}{cadence}",
        )

    words = metrics["words"]["top"]
    if words:
        add(
            "signatureWord",
            "Signature word",
            words[0]["word"],
            f"used {_plural(words[0]['count'], 'time')} in commit subjects",
        )
    return highlights
