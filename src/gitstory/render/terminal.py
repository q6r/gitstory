"""Terminal summary: plain text with a little ANSI colour, Unicode blocks and sparklines.

Colour follows the https://no-color.org convention (``NO_COLOR``), is off when output is not a
terminal, and can be forced with ``--color``. When the terminal cannot encode block characters
the output falls back to ASCII. All text that comes from a repository is sanitised again here,
so a hostile author name can never inject escape sequences into your terminal.
"""

from __future__ import annotations

import os
import shutil
import sys
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TextIO

from gitstory.util import ELLIPSIS, sanitize_text

UNICODE_SPARK = "▁▂▃▄▅▆▇█"
ASCII_SPARK = " .:-=+*#"
UNICODE_HEAT = "·░▒▓█"
ASCII_HEAT = ".:+*#"
ANSI = {"bold": "1", "dim": "2", "blue": "34", "magenta": "35", "cyan": "36", "green": "32"}
RESET = "\x1b[0m"
MIN_WIDTH, MAX_WIDTH = 80, 110  # the summary is laid out for at least a classic 80-column terminal
COLOR_MODES = ("auto", "always", "never")


@dataclass(frozen=True, slots=True)
class TermStyle:
    """How to draw: colour on/off, Unicode on/off and the terminal width in columns."""

    color: bool = False
    unicode: bool = True
    width: int = 80


def detect_style(
    stream: TextIO, *, color: str = "auto", env: Mapping[str, str] | None = None
) -> TermStyle:
    """Work out the drawing style for ``stream``.

    ``color`` is ``auto`` (a terminal, unless ``NO_COLOR`` is set or ``TERM=dumb``),
    ``always`` or ``never``.
    """
    env = os.environ if env is None else env
    if color == "always":
        use_color = True
    elif color == "never":
        use_color = False
    else:
        use_color = bool(
            stream.isatty() and not env.get("NO_COLOR") and env.get("TERM", "") != "dumb"
        )
    encoding = getattr(stream, "encoding", None) or "ascii"
    try:
        (UNICODE_SPARK + UNICODE_HEAT + "█░→·").encode(encoding)
        can_unicode = True
    except (UnicodeEncodeError, LookupError):
        can_unicode = False
    columns = shutil.get_terminal_size((80, 24)).columns
    width = max(MIN_WIDTH, min(MAX_WIDTH, columns))
    return TermStyle(color=use_color, unicode=can_unicode, width=width)


def enable_windows_vt() -> bool:
    """Turn on ANSI escape processing in a Windows console; True if colours can be used."""
    enabled = True
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        enabled = bool(kernel32.GetConsoleMode(handle, ctypes.byref(mode))) and bool(
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        )
    return enabled


class _Painter:
    """Colour and glyph choices for one :class:`TermStyle`."""

    def __init__(self, style: TermStyle) -> None:
        self.style = style
        self.spark = UNICODE_SPARK if style.unicode else ASCII_SPARK
        self.heat = UNICODE_HEAT if style.unicode else ASCII_HEAT
        self.full, self.empty = ("█", "░") if style.unicode else ("#", "-")
        self.arrow = "→" if style.unicode else "->"
        self.dot = "·" if style.unicode else "-"

    def paint(self, text: str, *names: str) -> str:
        if not self.style.color or not names:
            return text
        codes = ";".join(ANSI[name] for name in names)
        return f"\x1b[{codes}m{text}{RESET}"

    def bar(self, share: float, width: int) -> str:
        filled = max(0, min(width, round(share * width)))
        return self.paint(self.full * filled, "cyan") + self.paint(
            self.empty * (width - filled), "dim"
        )

    def sparkline(self, values: Sequence[int]) -> str:
        peak = max(values, default=0)
        top = len(self.spark) - 1
        glyphs = [
            self.spark[0]
            if peak == 0 or value <= 0
            else self.spark[1 + (value * (top - 1)) // peak]
            for value in values
        ]
        return self.paint("".join(glyphs), "cyan")


def display_width(text: str) -> int:
    """Number of terminal columns ``text`` occupies (wide CJK characters take two)."""
    total = 0
    for char in text:
        if unicodedata.combining(char) or unicodedata.category(char) in ("Mn", "Me", "Cf"):
            continue
        total += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
    return total


def _clean(text: object, limit: int) -> str:
    """Sanitise ``text`` and shorten it to at most ``limit`` terminal columns."""
    cleaned = sanitize_text(str(text))
    if display_width(cleaned) <= limit:
        return cleaned
    while cleaned and display_width(cleaned) + 1 > limit:
        cleaned = cleaned[:-1]
    return cleaned.rstrip() + ELLIPSIS


def _pad(text: str, width: int) -> str:
    """Left-align ``text`` in ``width`` terminal columns."""
    return text + " " * max(0, width - display_width(text))


def _span(days: int) -> str:
    years, rest = divmod(days, 365)
    months = rest // 30
    if years:
        return f"{years}y {months}m" if months else f"{years}y"
    return f"{months}m" if months else f"{days}d"


def _percent(share: float) -> int:
    return round(share * 100)


def _count(number: int, noun: str) -> str:
    """``1 commit``, ``2 commits``: the noun agrees with the (thousands-separated) number."""
    return f"{number:,} {noun}" if number == 1 else f"{number:,} {noun}s"


def _wrap(parts: Sequence[str], separator: str, indent: str, width: int) -> list[str]:
    """Join ``parts`` with ``separator`` into lines of at most ``width`` columns.

    Every line starts with ``indent``. A single part wider than the line is kept whole.
    """
    lines: list[str] = []
    current = ""
    for part in parts:
        candidate = f"{current}{separator}{part}" if current else indent + part
        if current and display_width(candidate) > width:
            lines.append(current)
            candidate = indent + part
        current = candidate
    if current:
        lines.append(current)
    return lines


# ---------------------------------------------------------------------------- sections


def _title(p: _Painter, metrics: Mapping[str, Any]) -> list[str]:
    repo = metrics["repository"]
    branch = repo["branch"] or "detached HEAD"
    room = max(10, p.style.width - 30)  # what is left of the line besides the fixed words
    name_limit = min(40, room * 3 // 5)
    branch_limit = min(30, room - name_limit)
    rule = (p.full if p.style.unicode else "-") * min(p.style.width, 72)
    return [
        p.paint(f"gitstory {p.dot} {_clean(repo['name'], name_limit)}", "bold", "magenta")
        + p.paint(f"  ({_clean(branch, branch_limit)} @ {_clean(repo['head'], 12)})", "dim"),
        p.paint(rule, "dim"),
    ]


def _overview(p: _Painter, metrics: Mapping[str, Any]) -> list[str]:
    totals = metrics["totals"]
    first, last = str(totals["firstCommitAt"])[:10], str(totals["lastCommitAt"])[:10]
    facts = [
        _count(totals["commits"], "commit"),
        _count(totals["authors"], "author"),
        _count(totals["activeDays"], "active day"),
        f"+{totals['additions']:,} / -{totals['deletions']:,} lines",
    ]
    return [
        *_wrap(facts, f"  {p.dot}  ", "", p.style.width),
        p.paint(f"{first} {p.arrow} {last}  ({_span(int(totals['spanDays']))})", "dim"),
    ]


def _activity(p: _Painter, metrics: Mapping[str, Any]) -> list[str]:
    streaks, chrono = metrics["streaks"], metrics["chronotype"]
    weeks = [int(week["commits"]) for week in metrics["activity"]["byWeek"][-52:]]
    streak = (
        f"current {_count(streaks['current'], 'day')}, longest {_count(streaks['longest'], 'day')}"
    )
    if streaks["longestFrom"]:
        streak += f" ({streaks['longestFrom']} {p.arrow} {streaks['longestTo']})"
    shares = [f"{w['label'].lower()} {_percent(w['share'])}%" for w in chrono["windows"]]
    indent = " " * 15
    return [
        p.paint("Activity", "bold"),
        f"  last {_count(len(weeks), 'week')}  {p.sparkline(weeks)}",
        f"  streak       {streak}",
        f"  chronotype   {p.paint(chrono['label'], 'bold')}",
        *_wrap(shares, ", ", indent, p.style.width),
    ]


def _week_hour(p: _Painter, metrics: Mapping[str, Any]) -> list[str]:
    grid = metrics["activity"]["weekdayHour"]
    peak = int(grid["max"]) or 1
    levels = len(p.heat) - 1
    axis = [" "] * 24
    for hour in (0, 6, 12, 18):
        axis[hour : hour + len(str(hour))] = str(hour)
    rows = []
    for name, row in zip(grid["weekdays"], grid["matrix"], strict=True):
        cells = "".join(p.heat[(int(count) * levels + peak - 1) // peak] for count in row)
        rows.append(f"  {name}  {p.paint(cells, 'blue')}")
    return [
        p.paint("Week x hour (local time)", "bold"),
        p.paint("       " + "".join(axis), "dim"),
        *rows,
    ]


def _relative(share: float, items: Sequence[Mapping[str, Any]]) -> float:
    """Bar length relative to the leading item, so the leader always fills the bar."""
    peak = max((float(item["share"]) for item in items), default=0.0)
    return share / peak if peak else 0.0


def _authors(p: _Painter, metrics: Mapping[str, Any]) -> list[str]:
    items = metrics["authors"]["items"][:6]
    rows = [
        f"  {_pad(_clean(a['name'], 22), 22)} {p.bar(_relative(a['share'], items), 14)} "
        f"{a['commits']:>6,}  {_percent(a['share']):>3}%"
        for a in items
    ]
    return [p.paint("Top authors", "bold"), *rows]


def _languages(p: _Painter, metrics: Mapping[str, Any]) -> list[str]:
    items = metrics["languages"]["items"][:6]
    rows = [
        f"  {_pad(_clean(lang['name'], 22), 22)} {p.bar(_relative(lang['share'], items), 14)} "
        f"{_percent(lang['share']):>9}%"
        for lang in items
    ]
    return [p.paint("Languages", "bold"), *rows]


def _hot_files(p: _Painter, metrics: Mapping[str, Any]) -> list[str]:
    files = metrics["hotspots"]["files"][:5]
    if not files:
        return []
    room = max(20, p.style.width - 34)
    rows = [
        f"  {_pad(_clean(f['path'], room), room)} "
        f"{f['commits']:>5,} commits {f['changes']:>8,} lines"
        for f in files
    ]
    return [p.paint("Hot files", "bold"), *rows]


def _messages(p: _Painter, metrics: Mapping[str, Any]) -> list[str]:
    kinds = ", ".join(
        f"{t['type']} {_percent(t['share'])}%" for t in metrics["conventional"]["types"][:6]
    )
    words = ", ".join(w["word"] for w in metrics["words"]["top"][:8])
    return [
        p.paint("Commit messages", "bold"),
        f"  types   {kinds}",
        f"  words   {_clean(words, p.style.width - 12)}",
    ]


def _records(p: _Painter, metrics: Mapping[str, Any]) -> list[str]:
    records, bus, releases = metrics["records"], metrics["busFactor"], metrics["releases"]
    lines = [p.paint("Records", "bold")]
    if records["busiestDay"]:
        day = records["busiestDay"]
        lines.append(f"  busiest day      {day['date']}  ({_count(day['commits'], 'commit')})")
    if records["biggestCommit"]:
        big = records["biggestCommit"]
        lines.append(
            f"  biggest commit   {_clean(big['subject'], min(44, max(16, p.style.width - 36)))}  "
            f"({_count(big['additions'] + big['deletions'], 'line')})"
        )
    if records["longestGap"]:
        gap = records["longestGap"]
        lines.append(
            f"  longest gap      {_count(gap['days'], 'day')} ({gap['from']} {p.arrow} {gap['to']})"
        )
    lines.append(f"  bus factor       {bus['value']} {p.paint('(a heuristic)', 'dim')}")
    if releases["count"]:
        latest = releases["latest"]
        name = _clean(latest["name"], max(8, p.style.width - 52))
        lines.append(
            f"  releases         {_count(releases['count'], 'tag')}, latest {name} "
            f"{_count(latest['daysAgo'], 'day')} ago"
        )
    return lines


def render_summary(metrics: Mapping[str, Any], style: TermStyle | None = None) -> str:
    """Render a compact terminal summary of a ``gitstory.metrics/v1`` document."""
    p = _Painter(style or TermStyle())
    lines = _title(p, metrics)
    if not metrics["totals"]["commits"]:
        lines.append("No commits matched the given filters.")
        return "\n".join(lines) + "\n"
    for section in (
        _overview,
        _activity,
        _week_hour,
        _authors,
        _languages,
        _hot_files,
        _messages,
        _records,
    ):
        block = section(p, metrics)
        if block:
            lines += ["", *block] if section is not _overview else block
    return "\n".join(lines) + "\n"
