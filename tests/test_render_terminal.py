"""The terminal summary: colour rules, fallbacks, widths and hostile text."""

from __future__ import annotations

import copy
import io
import re
import sys
import types
from typing import Any

import pytest
from builders import commit, history, tag, when

from gitstory.metrics import compute_metrics
from gitstory.model import FileChange
from gitstory.render import terminal
from gitstory.render.terminal import (
    ASCII_HEAT,
    ASCII_SPARK,
    MAX_WIDTH,
    MIN_WIDTH,
    UNICODE_SPARK,
    TermStyle,
    detect_style,
    display_width,
    enable_windows_vt,
    render_summary,
)

ESC = "\x1b"
SGR = re.compile(r"\x1b\[[0-9;]*m")
NOW = when("2024-06-30T12:00:00+00:00")
PLAIN = TermStyle(color=False, unicode=True, width=100)
COLOUR = TermStyle(color=True, unicode=True, width=100)
ASCII = TermStyle(color=False, unicode=False, width=100)


def strip(text: str) -> str:
    return SGR.sub("", text)


def long_name_metrics() -> dict[str, Any]:
    """A repository whose every name is as long and as nasty as the renderer allows."""
    nasty = "\x1b[31mRED\x1b[0m \x1b]0;pwned\x07 \N{RIGHT-TO-LEFT OVERRIDE}gnp.exe " + "w" * 120
    commits = [
        commit(
            f"2024-06-{day:02d}T{9 + day % 12:02d}:00:00+00:00",
            author=f"{nasty} {day % 3}",
            email=f"{day % 3}@evil.example",
            subject=f"feat(x): {nasty}",
            files=[FileChange(f"{'d' * 80}/{nasty}.py", 5, 1)],
        )
        for day in range(1, 29)
    ]
    tags = (tag("v" + "9" * 90, "2024-06-01T00:00:00+00:00"),)
    return compute_metrics(history(commits, tags=tags, name=nasty, branch=nasty), now=NOW)


class FakeStream(io.StringIO):
    """A text stream with a settable terminal flag and encoding."""

    def __init__(self, *, tty: bool, encoding: str | None = "utf-8") -> None:
        super().__init__()
        self._tty = tty
        self._encoding = encoding

    def isatty(self) -> bool:
        return self._tty

    @property
    def encoding(self) -> str | None:  # type: ignore[override]
        return self._encoding


# ----------------------------------------------------------------------------- content


def test_the_summary_has_every_section_in_order(metrics: dict[str, Any]) -> None:
    text = render_summary(metrics, PLAIN)
    headings = [
        "Activity",
        "Week x hour (local time)",
        "Top authors",
        "Languages",
        "Hot files",
        "Commit messages",
        "Records",
    ]
    positions = [text.index(f"\n{heading}\n") for heading in headings]
    assert positions == sorted(positions)
    assert text.startswith(
        f"gitstory \N{MIDDLE DOT} story-repo  (main @ {metrics['repository']['head']})"
    )
    assert text.endswith("\n")
    assert "\r" not in text


def test_the_overview_states_the_totals(metrics: dict[str, Any]) -> None:
    totals = metrics["totals"]
    text = render_summary(metrics, PLAIN)
    assert f"{totals['commits']:,} commits" in text
    assert f"{totals['authors']:,} authors" in text
    assert f"{totals['activeDays']:,} active days" in text
    assert f"+{totals['additions']:,} / -{totals['deletions']:,} lines" in text
    assert "2021-01-05 \N{RIGHTWARDS ARROW} 2026-11-09  (5y 10m)" in text


def test_the_records_name_the_planted_facts(metrics: dict[str, Any]) -> None:
    text = render_summary(metrics, PLAIN)
    assert "busiest day      2024-09-12  (17 commits)" in text
    assert "longest gap      71 days (2023-06-02 \N{RIGHTWARDS ARROW} 2023-08-13)" in text
    assert "(4,000 lines)" in text
    assert "bus factor       " in text
    assert "(a heuristic)" in text
    assert re.search(r"releases\s+14 tags, latest v\S+ \d+ days ago", text)


def test_counts_agree_with_their_nouns() -> None:
    one = compute_metrics(
        history([commit("2024-06-29T10:00:00+00:00", files=[("a.py", 1, 0)])]), now=NOW
    )
    text = render_summary(one, PLAIN)
    assert "1 commit  \N{MIDDLE DOT}  1 author  \N{MIDDLE DOT}  1 active day  " in text
    assert "current 1 day, longest 1 day" in text
    assert "(1 commit)" in text
    assert "(1 line)" in text
    for wrong in ("1 authors", "1 days", "1 tags", "(1 commits)", "(1 lines)"):
        assert wrong not in text
    assert "1 commits" not in text.splitlines()[2]  # the overview; table columns keep their unit
    tagged = compute_metrics(
        history(
            [commit("2024-06-29T10:00:00+00:00")], tags=(tag("v1", "2024-06-29T00:00:00+00:00"),)
        ),
        now=when("2024-06-30T12:00:00+00:00"),
    )
    assert "1 tag, latest v1 1 day ago" in render_summary(tagged, PLAIN)


def test_a_detached_head_is_named_as_such(metrics: dict[str, Any]) -> None:
    detached = copy.deepcopy(metrics)
    detached["repository"]["branch"] = None
    assert "(detached HEAD @ " in render_summary(detached, PLAIN)


def test_the_weekday_grid_has_seven_rows_of_twenty_four_cells(metrics: dict[str, Any]) -> None:
    lines = render_summary(metrics, PLAIN).splitlines()
    start = lines.index("Week x hour (local time)")
    axis, *rows = lines[start + 1 : start + 9]
    assert [row[2:5] for row in rows[:7]] == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    assert all(len(row[7:]) == 24 for row in rows[:7])
    assert axis.split() == ["0", "6", "12", "18"]
    assert axis.index("6") - axis.index("0") == 6


def test_the_sparkline_covers_the_last_52_weeks_and_scales_to_the_peak(
    metrics: dict[str, Any],
) -> None:
    line = next(
        row for row in render_summary(metrics, PLAIN).splitlines() if "last 52 weeks" in row
    )
    spark = line.split("  ")[-1]
    assert len(spark) == 52
    assert set(spark) <= set(UNICODE_SPARK)
    assert UNICODE_SPARK[-1] in spark  # the busiest week reaches the top


def test_the_leader_of_each_ranking_fills_its_bar(metrics: dict[str, Any]) -> None:
    lines = render_summary(metrics, PLAIN).splitlines()
    first_author = lines[lines.index("Top authors") + 1]
    first_language = lines[lines.index("Languages") + 1]
    assert "\N{FULL BLOCK}" * 14 in first_author
    assert "\N{FULL BLOCK}" * 14 in first_language


def test_a_history_without_commits_says_so() -> None:
    empty = compute_metrics(history([]), now=NOW)
    text = render_summary(empty, PLAIN)
    assert text.splitlines()[2] == "No commits matched the given filters."
    assert len(text.splitlines()) == 3


def test_sections_without_data_are_left_out() -> None:
    bare = compute_metrics(history([commit("2024-06-29T10:00:00+00:00", subject="init")]), now=NOW)
    text = render_summary(bare, PLAIN)
    assert "Hot files" not in text  # no file statistics at all
    assert "Records" in text


def test_rendering_is_deterministic(metrics: dict[str, Any]) -> None:
    assert render_summary(metrics, COLOUR) == render_summary(copy.deepcopy(metrics), COLOUR)


# ------------------------------------------------------------------------- colour/ASCII


def test_colour_only_adds_escape_codes(metrics: dict[str, Any]) -> None:
    coloured = render_summary(metrics, COLOUR)
    assert ESC in coloured
    assert strip(coloured) == render_summary(metrics, PLAIN)
    assert ESC not in strip(coloured)
    assert coloured.count("\x1b[0m") >= 10


def test_no_colour_means_no_escape_at_all(metrics: dict[str, Any]) -> None:
    assert ESC not in render_summary(metrics, PLAIN)


def test_the_ascii_fallback_draws_with_ascii_only(metrics: dict[str, Any]) -> None:
    plain_names = compute_metrics(
        history(
            [
                commit(f"2024-06-{day:02d}T10:00:00+00:00", files=[("a.py", day, 1)])
                for day in range(1, 29)
            ]
        ),
        now=NOW,
    )
    text = render_summary(plain_names, ASCII)
    assert text.isascii()
    assert "->" in text
    assert "-" * 20 in text
    assert set(ASCII_SPARK + ASCII_HEAT + "#-") & set(text)
    # names from the repository are data: they are kept even when the glyphs fall back
    assert "\N{CJK UNIFIED IDEOGRAPH-674E}" in render_summary(metrics, ASCII)


def test_unicode_names_survive_in_the_unicode_style(metrics: dict[str, Any]) -> None:
    text = render_summary(metrics, PLAIN)
    zoe = "Zo\N{LATIN SMALL LETTER E WITH DIAERESIS} M\N{LATIN SMALL LETTER U WITH DIAERESIS}ller"
    assert zoe in text
    assert "\N{CJK UNIFIED IDEOGRAPH-674E}\N{CJK UNIFIED IDEOGRAPH-96F7}" in text


# --------------------------------------------------------------------------- hostile data


@pytest.mark.parametrize("style", [PLAIN, COLOUR, ASCII])
def test_repository_text_cannot_inject_terminal_escape_sequences(style: TermStyle) -> None:
    text = render_summary(long_name_metrics(), style)
    leftover = SGR.sub("", text)
    assert ESC not in leftover  # every escape that remains is one of ours
    assert "\x07" not in text
    assert "\N{RIGHT-TO-LEFT OVERRIDE}" not in text
    assert not re.search(r"[\x00-\x08\x0b-\x1a\x1c-\x1f\x7f]", leftover)
    assert "\N{SYMBOL FOR ESCAPE}" in text  # the escape was made visible instead


@pytest.mark.parametrize("width", [MIN_WIDTH, 90, MAX_WIDTH])
def test_no_line_is_wider_than_the_terminal(width: int, metrics: dict[str, Any]) -> None:
    style = TermStyle(color=True, unicode=True, width=width)
    for document in (metrics, long_name_metrics()):
        for line in render_summary(document, style).splitlines():
            assert display_width(strip(line)) <= width, strip(line)


def test_wide_characters_keep_the_columns_aligned(metrics: dict[str, Any]) -> None:
    lines = render_summary(metrics, PLAIN).splitlines()
    rows = lines[lines.index("Top authors") + 1 : lines.index("Languages") - 1]
    bar_starts = {display_width(row[: row.index("\N{FULL BLOCK}")]) for row in rows}
    assert len(bar_starts) == 1  # the CJK name does not push its bar out of line


# ----------------------------------------------------------------------------- helpers


@pytest.mark.parametrize(
    ("text", "width"),
    [
        ("", 0),
        ("abc", 3),
        ("\N{CJK UNIFIED IDEOGRAPH-674E}\N{CJK UNIFIED IDEOGRAPH-96F7}", 4),
        ("e\N{COMBINING ACUTE ACCENT}", 1),
        ("a\N{ZERO WIDTH JOINER}b", 2),
        ("\N{FULLWIDTH LATIN SMALL LETTER A}", 2),
        ("\N{LATIN SMALL LETTER E WITH ACUTE}", 1),
    ],
)
def test_display_width_counts_terminal_columns(text: str, width: int) -> None:
    assert display_width(text) == width


@pytest.mark.parametrize(
    ("days", "text"),
    [(0, "0d"), (5, "5d"), (29, "29d"), (30, "1m"), (364, "12m"), (365, "1y"), (395, "1y 1m"),
     (730, "2y"), (2134, "5y 10m")],
)  # fmt: skip
def test_span_is_a_short_human_duration(days: int, text: str) -> None:
    assert terminal._span(days) == text


def test_clean_shortens_by_columns_and_never_returns_more_than_the_limit() -> None:
    assert terminal._clean("short", 10) == "short"
    cut = terminal._clean("x" * 100, 10)
    assert cut == "x" * 9 + "\N{HORIZONTAL ELLIPSIS}"
    assert display_width(cut) == 10
    wide = terminal._clean("\N{CJK UNIFIED IDEOGRAPH-674E}" * 30, 11)
    assert display_width(wide) <= 11
    assert wide.endswith("\N{HORIZONTAL ELLIPSIS}")
    assert terminal._clean("a\x1bb", 10) == "a\N{SYMBOL FOR ESCAPE}b"


def test_wrap_breaks_between_parts_and_indents_continuation_lines() -> None:
    parts = ["alpha 10%", "beta 20%", "gamma 30%", "delta 40%"]
    assert terminal._wrap(parts, ", ", "  ", 80) == ["  alpha 10%, beta 20%, gamma 30%, delta 40%"]
    narrow = terminal._wrap(parts, ", ", "  ", 25)
    assert narrow == ["  alpha 10%, beta 20%", "  gamma 30%, delta 40%"]
    assert terminal._wrap(["x" * 50], ", ", "  ", 10) == ["  " + "x" * 50]  # never splits a part
    assert terminal._wrap([], ", ", "  ", 10) == []


# --------------------------------------------------------------------- style detection


def test_colour_follows_the_terminal_and_the_no_color_convention() -> None:
    tty, pipe = FakeStream(tty=True), FakeStream(tty=False)
    assert detect_style(tty, env={}).color is True
    assert detect_style(pipe, env={}).color is False
    assert detect_style(tty, env={"NO_COLOR": "1"}).color is False
    assert detect_style(tty, env={"NO_COLOR": ""}).color is True  # an empty value does not count
    assert detect_style(tty, env={"TERM": "dumb"}).color is False
    assert detect_style(pipe, color="always", env={"NO_COLOR": "1"}).color is True
    assert detect_style(tty, color="never", env={}).color is False


def test_unicode_needs_a_stream_that_can_encode_the_glyphs() -> None:
    assert detect_style(FakeStream(tty=True, encoding="utf-8"), env={}).unicode is True
    assert detect_style(FakeStream(tty=True, encoding="ascii"), env={}).unicode is False
    assert detect_style(FakeStream(tty=True, encoding="cp1252"), env={}).unicode is False
    assert detect_style(FakeStream(tty=True, encoding=None), env={}).unicode is False
    assert detect_style(FakeStream(tty=True, encoding="no-such-codec"), env={}).unicode is False


@pytest.mark.parametrize(
    ("columns", "expected"), [(10, MIN_WIDTH), (80, 80), (95, 95), (400, MAX_WIDTH)]
)
def test_the_width_is_clamped(columns: int, expected: int, monkeypatch: pytest.MonkeyPatch) -> None:
    size = types.SimpleNamespace(columns=columns)
    monkeypatch.setattr(terminal.shutil, "get_terminal_size", lambda _fallback: size)
    assert detect_style(FakeStream(tty=True), env={}).width == expected


def test_windows_console_colour_is_switched_on_through_the_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, int]] = []

    class Kernel:
        def __init__(self, ok: bool) -> None:
            self.ok = ok

        def GetStdHandle(self, which: int) -> int:  # noqa: N802
            calls.append(("handle", which))
            return 7

        def GetConsoleMode(self, handle: int, mode: Any) -> int:  # noqa: N802
            mode.value = 3
            return int(self.ok)

        def SetConsoleMode(self, handle: int, mode: int) -> int:  # noqa: N802
            calls.append(("set", mode))
            return 1

    def fake_ctypes(ok: bool) -> types.ModuleType:
        module = types.ModuleType("ctypes")
        module.windll = types.SimpleNamespace(kernel32=Kernel(ok))  # type: ignore[attr-defined]
        module.c_uint32 = type("c_uint32", (), {"value": 0})  # type: ignore[attr-defined]
        module.byref = lambda obj: obj  # type: ignore[attr-defined]
        return module

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes(ok=True))
    assert enable_windows_vt() is True
    assert ("set", 3 | 0x0004) in calls  # ENABLE_VIRTUAL_TERMINAL_PROCESSING added to the mode
    monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes(ok=False))
    assert enable_windows_vt() is False  # not a console (redirected output): no colour


def test_other_platforms_need_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    assert enable_windows_vt() is True
