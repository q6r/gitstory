"""Render the animated SVG stats card.

The card is pure SVG plus CSS: no script, no external fonts, no images and no references, so
it renders inside GitHub's ``<img>`` sandbox. Cells fade in one after another, the language bar
grows from zero, and everything is switched off under ``prefers-reduced-motion``. With
``theme="auto"`` the colours follow ``prefers-color-scheme`` inside the SVG itself.

It wears the same look as the dashboard: Prism Classic (a near-black ground, silver ink and one
indigo accent) in the dark theme and Prism Daylight (the same indigo on white) in the light one.

The output is deterministic: the same metrics and options always give the same bytes. Every
piece of text that comes from a repository is sanitised and XML-escaped.
"""

from __future__ import annotations

import html
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from gitstory.metrics.activity import heat_level, heat_thresholds
from gitstory.model import ConfigError
from gitstory.util import ELLIPSIS, sanitize_text, truncate

THEMES = ("dark", "light", "auto")
DEFAULT_WIDTH = 495
DEFAULT_HEIGHT = 195
DEFAULT_WEEKS = 20
WIDTH_RANGE = (320, 1200)
HEIGHT_RANGE = (160, 600)
WEEKS_RANGE = (8, 53)

FONT = '-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif'
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
MIDDLE_DOT = "\N{MIDDLE DOT}"
_NONCHARACTERS = {0xFFFE: None, 0xFFFF: None}  # not allowed in XML documents

PAD = 24
TITLE_Y = 34
SUBTITLE_Y = 51
MAIN_TOP = 64
BAR_H = 7
CELL_GAP = 2
MIN_PITCH = 4.0  # the smallest heatmap cell (plus gap) that still reads as a grid
LOGO = 14  # the logo tile next to the word "gitstory"
LOGO_GAP = 6
SERIES_SLOTS = 8  # language colours: fixed order, never cycled (the list is folded into "Other")


@dataclass(frozen=True, slots=True)
class Palette:
    """The colour tokens of one theme (Prism Classic when dark, Prism Daylight when light)."""

    card: str  # the ground of the card
    border: str  # its hairline
    track: str  # the empty part of the language bar
    ink1: str
    ink2: str
    ink3: str
    primary: str  # the logo tile ...
    on_primary: str  # ... and the glyph on it
    ramp: tuple[str, str, str, str, str, str]  # heatmap levels, from empty to busiest
    series: tuple[str, str, str, str, str, str, str, str]  # language colours, in slot order
    other: str  # the folded tail of the language list
    glow_a: tuple[str, str]  # colour and opacity of the soft haze behind the title
    glow_b: tuple[str, str]  # colour and opacity of the indigo haze at the top right


DARK = Palette(
    card="#0c0c10",
    border="#25262c",
    track="#17171b",
    ink1="#d8dade",
    ink2="#989aa3",
    ink3="#858791",
    primary="#d8dade",
    on_primary="#0b0b0e",
    ramp=("#1a1a22", "#3a43a0", "#4a54c4", "#6770df", "#8e96ef", "#b8bdf7"),
    series=("#6f76e6", "#d95926", "#1aa384", "#c98500", "#d55181", "#008300", "#3a9fd8", "#e66767"),
    other="#4b4c54",
    glow_a=("#d8dade", ".13"),
    glow_b=("#6f76e6", ".3"),
)
LIGHT = Palette(
    card="#fafafb",
    border="#dfe0e4",
    track="#e9eaee",
    ink1="#2b2e36",
    ink2="#5f6470",
    ink3="#6d7280",
    primary="#2b2e36",
    on_primary="#f4f5f7",
    ramp=("#eef0f8", "#a6acee", "#7f87e3", "#5b64d3", "#3f48b5", "#2a3188"),
    series=("#5b62d6", "#e8612b", "#0f9b78", "#c28000", "#d9628f", "#008300", "#2f8fd0", "#e34948"),
    other="#cdcfd3",
    glow_a=("#5b62d6", ".09"),
    glow_b=("#5b62d6", ".16"),
)


@dataclass(frozen=True, slots=True)
class CardOptions:
    """Everything that can be configured about the card."""

    theme: str = "auto"
    width: int = DEFAULT_WIDTH
    height: int = DEFAULT_HEIGHT
    weeks: int = DEFAULT_WEEKS
    title: str | None = None

    def __post_init__(self) -> None:
        if self.theme not in THEMES:
            raise ConfigError(f"theme must be one of {', '.join(THEMES)}")
        for name, value, (low, high) in (
            ("width", self.width, WIDTH_RANGE),
            ("height", self.height, HEIGHT_RANGE),
            ("weeks", self.weeks, WEEKS_RANGE),
        ):
            if not low <= value <= high:
                raise ConfigError(f"card {name} must be between {low} and {high}")


# ---------------------------------------------------------------------------------- helpers


def _n(value: float) -> str:
    """Format a coordinate compactly and deterministically."""
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _esc(text: str) -> str:
    """Sanitise and XML-escape text for use in element content or attribute values."""
    return html.escape(sanitize_text(text).translate(_NONCHARACTERS), quote=True)


# Advance widths (1/1000 em) of Helvetica/Arial for the printable ASCII range U+0020..U+007E.
# System UI fonts differ slightly, but they are close enough to lay text out without measuring.
_ASCII_WIDTHS = (
    278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,
    556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556,
    1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778,
    667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556,
    333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556,
    556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584,
)  # fmt: skip


def _width(text: str, size: float, *, bold: bool = False) -> float:
    """Estimated rendered width of ``text`` in a system sans-serif font."""
    total = 0.0
    for char in text:
        code = ord(char)
        if 0x20 <= code <= 0x7E:
            advance = _ASCII_WIDTHS[code - 0x20] * (1.07 if bold and not char.isdigit() else 1)
        elif unicodedata.east_asian_width(char) in ("W", "F"):
            advance = 1000
        else:
            advance = 600
        total += advance
    return total * size / 1000


def _fit(text: str, size: float, limit: float, *, bold: bool = False) -> str:
    """Truncate ``text`` (with an ellipsis) so its estimated width stays within ``limit``."""
    cleaned = sanitize_text(text)
    for length in range(len(cleaned), 0, -1):
        candidate = cleaned if length == len(cleaned) else cleaned[: length - 1].rstrip() + ELLIPSIS
        if _width(candidate, size, bold=bold) <= limit:
            return candidate
    return cleaned[:1]


def _since(first_commit_at: object) -> str:
    """Return e.g. ``"Jan 2021"`` for an ISO timestamp, or an empty string."""
    text = str(first_commit_at or "")
    if len(text) < 7 or not text[:4].isdigit() or not text[5:7].isdigit():
        return ""
    month = int(text[5:7])
    return f"{MONTHS[month - 1]} {text[:4]}" if 1 <= month <= 12 else ""


def recent_levels(calendar: Mapping[str, Any] | None, weeks: int) -> list[list[int]]:
    """Return the last ``weeks`` calendar columns as lists of 7 heat levels.

    Levels are recomputed for just these weeks so a quiet recent period still uses the whole
    colour ramp. Days that do not exist yet (the rest of the current week) are ``-1``;
    missing history on the left is padded with empty columns of ``-1``.
    """
    days = list(calendar["days"]) if calendar and calendar.get("days") else []
    columns = [days[i : i + 7] for i in range(0, len(days), 7)][-weeks:]
    cuts = heat_thresholds(int(day["commits"]) for column in columns for day in column)
    grid = [
        [heat_level(int(day["commits"]), cuts) for day in column] + [-1] * (7 - len(column))
        for column in columns
    ]
    return [[-1] * 7 for _ in range(weeks - len(grid))] + grid


# ------------------------------------------------------------------------------------ css


def _palette_rules(p: Palette) -> str:
    return "\n".join(
        [
            f".card{{fill:{p.card};stroke:{p.border}}}",
            f".trk{{fill:{p.track}}}",
            f".i1{{fill:{p.ink1}}}.i2{{fill:{p.ink2}}}.i3{{fill:{p.ink3}}}",
            f".lg{{fill:{p.primary}}}.lo{{fill:{p.on_primary}}}",
            "".join(f".h{i}{{fill:{color}}}" for i, color in enumerate(p.ramp)),
            "".join(f".s{i}{{fill:{color}}}" for i, color in enumerate(p.series, start=1))
            + f".so{{fill:{p.other}}}",
            f".ga,.ga0{{stop-color:{p.glow_a[0]}}}.ga{{stop-opacity:{p.glow_a[1]}}}",
            f".gb,.gb0{{stop-color:{p.glow_b[0]}}}.gb{{stop-opacity:{p.glow_b[1]}}}",
        ]
    )


def _css(theme: str) -> str:
    parts = []
    if theme == "light":
        parts.append(_palette_rules(LIGHT))
    else:
        parts.append(_palette_rules(DARK))
        if theme == "auto":
            parts.append(f"@media (prefers-color-scheme:light){{\n{_palette_rules(LIGHT)}\n}}")
    parts.append(
        ".nm{font-variant-numeric:tabular-nums}\n"
        "@keyframes gs-fade{from{opacity:0}}\n"
        "@keyframes gs-rise{from{opacity:0;transform:translateY(5px)}}\n"
        "@keyframes gs-grow{from{transform:scaleX(0)}}\n"
        ".fd{animation:gs-fade .45s ease-out backwards}\n"
        ".rs{animation:gs-rise .6s cubic-bezier(.2,.7,.2,1) backwards}\n"
        ".gw{animation:gs-grow 1.1s cubic-bezier(.2,.7,.2,1) .85s backwards}\n"
        "@media (prefers-reduced-motion:reduce){.fd,.rs,.gw{animation:none}}"
    )
    return "\n".join(parts)


# -------------------------------------------------------------------------------- layout


@dataclass(frozen=True, slots=True)
class _Layout:
    width: int
    height: int
    weeks: int
    bar_y: float
    legend_y: float
    main_h: float
    value_size: float
    label_size: float
    label_x: float
    pitch: float
    heat_x: float
    heat_y: float

    @property
    def cell(self) -> float:
        return self.pitch - CELL_GAP

    @property
    def heat_w(self) -> float:
        return self.weeks * self.pitch - CELL_GAP


def _layout(width: int, height: int, weeks: int, rows: Sequence[tuple[int, str]]) -> _Layout:
    legend_y = height - 17
    bar_y = legend_y - 12 - BAR_H
    main_h = bar_y - 12 - MAIN_TOP
    value_size = max(15.0, min(26.0, main_h / 3 * 0.84))
    label_size = max(10.0, min(12.5, value_size * 0.52))
    value_w = max(_width(f"{value:,}", value_size, bold=True) for value, _ in rows)
    label_x = PAD + max(value_w, 40) + 12
    stats_right = label_x + max(_width(label, label_size) for _, label in rows)
    budget = (width - PAD) - stats_right - 28
    # A narrow card (or very large numbers) cannot show every requested week: show fewer
    # instead of letting the heatmap run into the statistics.
    weeks = max(1, min(weeks, int((budget + CELL_GAP) // MIN_PITCH)))
    pitch = min(budget / weeks, (main_h + CELL_GAP) / 7)
    pitch = max(MIN_PITCH, int(pitch * 2) / 2)  # half-pixel steps keep the cells crisp
    heat_w = weeks * pitch - CELL_GAP
    heat_h = 7 * pitch - CELL_GAP
    return _Layout(
        width=width,
        height=height,
        weeks=weeks,
        bar_y=bar_y,
        legend_y=legend_y,
        main_h=main_h,
        value_size=value_size,
        label_size=label_size,
        label_x=label_x,
        pitch=pitch,
        heat_x=width - PAD - heat_w,
        heat_y=MAIN_TOP + (main_h - heat_h) / 2,
    )


# ------------------------------------------------------------------------------- sections


def _defs(lay: _Layout) -> list[str]:
    """The two soft glows at the top: a silver haze behind the title and indigo at the right."""
    w, h = lay.width, lay.height
    haze = _n(min(w * 0.54, h * 1.0))
    indigo = _n(min(w * 0.4, h * 0.9))
    return [
        "<defs>",
        f'<radialGradient id="gs-glow-a" cx="{_n(w * 0.56)}" cy="-34" r="{haze}" '
        'gradientUnits="userSpaceOnUse"><stop offset="0" class="ga"/>'
        '<stop offset="1" class="ga0" stop-opacity="0"/></radialGradient>',
        f'<radialGradient id="gs-glow-b" cx="{_n(w * 0.95)}" cy="-29" r="{indigo}" '
        'gradientUnits="userSpaceOnUse"><stop offset="0" class="gb"/>'
        '<stop offset="1" class="gb0" stop-opacity="0"/></radialGradient>',
        f'<clipPath id="gs-card"><rect x="0.5" y="0.5" width="{w - 1}" height="{h - 1}" '
        'rx="14"/></clipPath>',
        f'<clipPath id="gs-bar"><rect x="{PAD}" y="{_n(lay.bar_y)}" width="{w - 2 * PAD}" '
        f'height="{BAR_H}" rx="{_n(BAR_H / 2)}"/></clipPath>',
        "</defs>",
    ]


def _background(lay: _Layout) -> list[str]:
    w, h = lay.width, lay.height
    return [
        f'<rect class="card" x="0.5" y="0.5" width="{w - 1}" height="{h - 1}" rx="14"/>',
        '<g clip-path="url(#gs-card)">',
        f'<rect width="{w}" height="{h}" fill="url(#gs-glow-a)"/>',
        f'<rect width="{w}" height="{h}" fill="url(#gs-glow-b)"/>',
        "</g>",
    ]


def _brand_width() -> float:
    """Width of the logo tile and the word "gitstory" beside it."""
    return LOGO + LOGO_GAP + _width("gitstory", 10.5, bold=True)


def _logo(x: float, y: float) -> str:
    """The logo tile: a silver square with a commit (a dot on a line) on it, from plain shapes."""
    centre = LOGO / 2
    return (
        f'<rect class="lg" x="{_n(x)}" y="{_n(y)}" width="{LOGO}" height="{LOGO}" rx="4"/>'
        f'<rect class="lo" x="{_n(x + 2.4)}" y="{_n(y + centre - 0.7)}" width="2.8" height="1.4" '
        'rx=".7"/>'
        f'<rect class="lo" x="{_n(x + LOGO - 5.2)}" y="{_n(y + centre - 0.7)}" width="2.8" '
        'height="1.4" rx=".7"/>'
        f'<circle class="lo" cx="{_n(x + centre)}" cy="{_n(y + centre)}" r="2.3"/>'
    )


def _header(lay: _Layout, title: str, subtitle: str) -> list[str]:
    right = lay.width - PAD
    out = [
        f'<text class="i1 rs" x="{PAD}" y="{TITLE_Y}" font-size="16" font-weight="600" '
        f'style="animation-delay:60ms">{_esc(title)}</text>'
    ]
    if subtitle:
        out.append(
            f'<text class="i3 rs" x="{PAD}" y="{SUBTITLE_Y}" font-size="11" '
            f'style="animation-delay:140ms">{_esc(subtitle)}</text>'
        )
    out.append(
        '<g class="rs" style="animation-delay:220ms">'
        + _logo(right - _brand_width(), TITLE_Y - 12)
        + f'<text class="i2" x="{right}" y="{TITLE_Y - 1}" font-size="10.5" font-weight="600" '
        'text-anchor="end">gitstory</text></g>'
    )
    out.append(
        f'<text class="i3 fd" x="{right}" y="{SUBTITLE_Y}" font-size="9" font-weight="600" '
        'letter-spacing="1.2" text-anchor="end" style="animation-delay:300ms">'
        f"LAST {lay.weeks} WEEKS</text>"
    )
    return out


def _stats(lay: _Layout, rows: Sequence[tuple[int, str]]) -> list[str]:
    out = []
    row_pitch = lay.main_h / 3
    for index, (value, label) in enumerate(rows):
        baseline = MAIN_TOP + row_pitch * (index + 0.5) + lay.value_size * 0.35
        out.append(
            f'<g class="rs" style="animation-delay:{260 + index * 90}ms">'
            f'<text class="i1 nm" x="{PAD}" y="{_n(baseline)}" font-size="{_n(lay.value_size)}" '
            f'font-weight="600">{value:,}</text>'
            f'<text class="i2" x="{_n(lay.label_x)}" y="{_n(baseline)}" '
            f'font-size="{_n(lay.label_size)}">{label}</text></g>'
        )
    return out


def _heatmap(lay: _Layout, calendar: Mapping[str, Any] | None) -> list[str]:
    out = ["<g>"]
    for col, column in enumerate(recent_levels(calendar, lay.weeks)):
        for row, level in enumerate(column):
            if level < 0:
                continue
            delay = 300 + (col * 7 + row) * 6
            out.append(
                f'<rect class="h{level} fd" x="{_n(lay.heat_x + col * lay.pitch)}" '
                f'y="{_n(lay.heat_y + row * lay.pitch)}" width="{_n(lay.cell)}" '
                f'height="{_n(lay.cell)}" rx="2.5" style="animation-delay:{delay}ms"/>'
            )
    out.append("</g>")
    return out


def _languages(lay: _Layout, items: Sequence[Mapping[str, Any]]) -> list[str]:
    bar_w = lay.width - 2 * PAD
    out = [
        f'<rect class="trk" x="{PAD}" y="{_n(lay.bar_y)}" width="{bar_w}" height="{BAR_H}" '
        f'rx="{_n(BAR_H / 2)}"/>'
    ]
    legend: list[tuple[str, str, float]] = []
    if items:
        usable = bar_w - 2 * (len(items) - 1)
        cursor = float(PAD)
        out.append(f'<g clip-path="url(#gs-bar)"><g class="gw" style="transform-origin:{PAD}px 0">')
        for index, item in enumerate(items):
            share = float(item["share"])
            seg_w = max(1.5, share * usable)
            # the language colours come in a fixed order; the folded tail is muted
            slot = "so" if bool(item["other"]) else f"s{min(index, SERIES_SLOTS - 1) + 1}"
            out.append(
                f'<rect class="{slot}" x="{_n(cursor)}" y="{_n(lay.bar_y)}" width="{_n(seg_w)}" '
                f'height="{BAR_H}"/>'
            )
            cursor += seg_w + 2
            legend.append((str(item["name"]), slot, share))
        out.append("</g></g>")
    out += _legend(lay, legend)
    return out


def _legend(lay: _Layout, legend: Sequence[tuple[str, str, float]]) -> list[str]:
    y = lay.legend_y
    if not legend:
        return [
            f'<text class="i3 fd" x="{PAD}" y="{y}" font-size="10.5" '
            'style="animation-delay:1000ms">No language data</text>'
        ]
    out = []
    cursor = float(PAD)
    limit = lay.width - PAD
    shown = 0
    for index, (name, slot, share) in enumerate(legend):
        label = f"{truncate(sanitize_text(name), 14)} {round(share * 100)}%"
        needed = 11 + _width(label, 10.5)
        hidden_after = len(legend) - index - 1
        reserve = 14 + _width(f"+{hidden_after} more", 10.5) if hidden_after else 0
        if cursor + needed + reserve > limit:
            break
        out.append(
            f'<g class="fd" style="animation-delay:{1150 + index * 90}ms">'
            f'<circle class="{slot}" cx="{_n(cursor + 3.5)}" cy="{_n(y - 3.5)}" r="3.5"/>'
            f'<text class="i2" x="{_n(cursor + 11)}" y="{y}" font-size="10.5">{_esc(label)}</text>'
            "</g>"
        )
        cursor += needed + 14
        shown += 1
    if shown < len(legend):
        out.append(
            f'<text class="i3 fd" x="{_n(cursor)}" y="{y}" font-size="10.5" '
            f'style="animation-delay:1500ms">+{len(legend) - shown} more</text>'
        )
    return out


# --------------------------------------------------------------------------------- render


def render_card(metrics: Mapping[str, Any], options: CardOptions | None = None) -> str:
    """Render the stats card for a ``gitstory.metrics/v1`` document as an SVG string."""
    opts = options or CardOptions()
    totals = metrics["totals"]
    commits, active = int(totals["commits"]), int(totals["activeDays"])
    streak = int(metrics["streaks"]["current"])
    rows = ((commits, "commits"), (active, "active days"), (streak, "day streak"))
    lay = _layout(opts.width, opts.height, opts.weeks, rows)

    repo_name = str(metrics["repository"]["name"])
    title_limit = lay.width - 2 * PAD - _brand_width() - 14
    title = _fit(opts.title if opts.title is not None else repo_name, 16, title_limit, bold=True)
    author = metrics["filters"].get("author")
    authors = int(totals.get("authors", 0))
    bits = []
    if author:
        bits.append(f"by {sanitize_text(str(author))}")
    elif authors:
        bits.append(f"{authors:,} {'contributor' if authors == 1 else 'contributors'}")
    since = _since(totals.get("firstCommitAt"))
    if since:
        bits.append(f"since {since}")
    subtitle = _fit(f" {MIDDLE_DOT} ".join(bits), 11, lay.width - 2 * PAD - 110)
    desc = (
        f"{commits:,} commits, {active:,} active days and a {streak:,}-day streak "
        f"in {sanitize_text(repo_name)}"
    )

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{lay.width}" height="{lay.height}" '
        f'viewBox="0 0 {lay.width} {lay.height}" role="img" aria-labelledby="gs-t gs-d" '
        f'font-family="{html.escape(FONT, quote=True)}">',
        f'<title id="gs-t">gitstory: {_esc(title)}</title>',
        f'<desc id="gs-d">{_esc(desc)}</desc>',
        f"<style>\n{_css(opts.theme)}\n</style>",
        *_defs(lay),
        *_background(lay),
        *_header(lay, title, subtitle),
        *_stats(lay, rows),
        *_heatmap(lay, metrics["activity"].get("calendar")),
        *_languages(lay, metrics["languages"]["items"]),
        "</svg>",
    ]
    return "\n".join(out) + "\n"
