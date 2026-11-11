"""The animated SVG card: well-formedness, safety, layout and hostile text."""

from __future__ import annotations

import copy
import re
import xml.etree.ElementTree as ET
from typing import Any

import pytest
from builders import commit, history, when

from gitstory.metrics import compute_metrics
from gitstory.model import ConfigError
from gitstory.render import svg_card
from gitstory.render.svg_card import (
    DEFAULT_HEIGHT,
    DEFAULT_WEEKS,
    DEFAULT_WIDTH,
    HEIGHT_RANGE,
    PAD,
    THEMES,
    WEEKS_RANGE,
    WIDTH_RANGE,
    CardOptions,
    recent_levels,
    render_card,
)

SVG = "{http://www.w3.org/2000/svg}"
NOW = when("2024-06-30T12:00:00+00:00")
# the control characters and noncharacters an XML 1.0 document may not contain
XML_FORBIDDEN = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f" + chr(0xFFFE) + chr(0xFFFF) + "]")


def parse(card: str) -> ET.Element:
    return ET.fromstring(card)


def texts(card: str) -> list[str]:
    return ["".join(node.itertext()) for node in parse(card).iter(f"{SVG}text")]


def tiny_metrics(**overrides: Any) -> dict[str, Any]:
    """Metrics of a handful of commits, with selected fields replaced."""
    commits = [
        commit(f"2024-06-{day:02d}T10:00:00+00:00", files=[("a.py", 4, 1), ("b.js", 2, 0)])
        for day in range(1, 29)
    ]
    document = compute_metrics(history(commits, name="demo"), now=NOW)
    document.update(overrides)
    return document


# ---------------------------------------------------------------------------- structure


def test_the_card_is_well_formed_svg_with_the_default_size(metrics: dict[str, Any]) -> None:
    card = render_card(metrics)
    root = parse(card)
    assert root.tag == f"{SVG}svg"
    assert root.get("width") == str(DEFAULT_WIDTH)
    assert root.get("height") == str(DEFAULT_HEIGHT)
    assert root.get("viewBox") == f"0 0 {DEFAULT_WIDTH} {DEFAULT_HEIGHT}"
    assert root.get("role") == "img"
    assert card.startswith("<svg ")
    assert card.endswith("</svg>\n")
    assert "\r" not in card


def test_the_card_has_an_accessible_name_and_description(metrics: dict[str, Any]) -> None:
    root = parse(render_card(metrics))
    title = root.find(f"{SVG}title")
    desc = root.find(f"{SVG}desc")
    assert title is not None
    assert desc is not None
    assert title.text == "gitstory: story-repo"
    assert desc.text is not None
    assert f"{metrics['totals']['commits']:,} commits" in desc.text
    assert root.get("aria-labelledby") == "gs-t gs-d"
    assert title.get("id") == "gs-t"
    assert desc.get("id") == "gs-d"


def test_the_card_shows_the_headline_numbers(metrics: dict[str, Any]) -> None:
    lines = texts(render_card(metrics))
    assert f"{metrics['totals']['commits']:,}" in lines
    assert f"{metrics['totals']['activeDays']:,}" in lines
    assert f"{metrics['streaks']['current']:,}" in lines
    assert {"commits", "active days", "day streak"} <= set(lines)
    assert "story-repo" in lines
    assert any(line.startswith(f"{metrics['totals']['authors']} contributors") for line in lines)
    assert any("since Jan 2021" in line for line in lines)
    assert f"LAST {DEFAULT_WEEKS} WEEKS" in lines


def test_the_output_is_deterministic(metrics: dict[str, Any]) -> None:
    assert render_card(metrics) == render_card(copy.deepcopy(metrics))
    assert render_card(metrics, CardOptions(theme="dark")) != render_card(
        metrics, CardOptions(theme="light")
    )


# ------------------------------------------------------------------------------ safety


def test_the_card_is_inert_and_makes_no_requests(metrics: dict[str, Any]) -> None:
    """GitHub renders README images in a sandbox: nothing here may need a script or the network."""
    card = render_card(metrics)
    root = parse(card)
    tags = {element.tag.removeprefix(SVG) for element in root.iter()}
    assert tags <= {
        "svg", "title", "desc", "style", "defs", "linearGradient", "radialGradient", "stop",
        "clipPath", "rect", "g", "text", "circle",
    }  # fmt: skip
    for element in root.iter():
        for name in element.attrib:
            assert not name.startswith("on"), name
            assert "href" not in name, name
    assert not re.search(
        r"<script|<foreignObject|<image|<use\b|<a\b|@import|javascript:", card, re.I
    )
    assert set(re.findall(r"https?://[^\s\"']+", card)) == {"http://www.w3.org/2000/svg"}
    for reference in re.findall(r"url\(([^)]*)\)", card):
        assert re.fullmatch(r"#gs-[a-z-]+", reference), reference


def test_motion_is_switched_off_for_people_who_ask_for_it(metrics: dict[str, Any]) -> None:
    card = render_card(metrics)
    assert "@media (prefers-reduced-motion:reduce){.fd,.rs,.gw{animation:none}}" in card
    assert card.count("animation:") >= 3


@pytest.mark.parametrize(
    "payload",
    [
        "</svg><script>alert(1)</script>",
        '"><image href="https://evil.example/x.png"/>',
        "a & b < c > d 'e' \"f\"",
        "\x1b[31mred\x1b[0m \x1b]0;pwned\x07",
        "\N{RIGHT-TO-LEFT OVERRIDE}gnp.exe",
        "nul\x00byte " + chr(0xFFFE) + chr(0xFFFF) + " end",  # characters XML forbids
        "]]> <![CDATA[ x ]]>",
        "\N{LINE SEPARATOR}\N{PARAGRAPH SEPARATOR}",
        "{{title}} {{data}}",
    ],
)
def test_hostile_text_never_breaks_the_document(metrics: dict[str, Any], payload: str) -> None:
    hostile = copy.deepcopy(metrics)
    hostile["repository"]["name"] = payload
    hostile["filters"]["author"] = payload
    hostile["languages"]["items"][0]["name"] = payload
    for options in (CardOptions(), CardOptions(title=payload)):
        card = render_card(hostile, options)
        root = parse(card)  # raises if the XML is broken
        assert not any(el.tag.endswith(("script", "image", "foreignObject")) for el in root.iter())
        assert "<script" not in card
        assert "\x1b" not in card
        assert "\x00" not in card
        assert "\N{RIGHT-TO-LEFT OVERRIDE}" not in card
        assert not XML_FORBIDDEN.search(card)


def test_a_title_option_replaces_the_repository_name(metrics: dict[str, Any]) -> None:
    card = render_card(metrics, CardOptions(title="My <b>profile</b>"))
    assert "My &lt;b&gt;profile&lt;/b&gt;" in card
    assert "story-repo" not in texts(card)[0:3]


def test_an_author_filter_is_named_in_the_subtitle(metrics: dict[str, Any]) -> None:
    filtered = copy.deepcopy(metrics)
    filtered["filters"]["author"] = "ada"
    assert any(line.startswith("by ada") for line in texts(render_card(filtered)))


# --------------------------------------------------------------------------- options


@pytest.mark.parametrize(
    "options",
    [
        {"theme": "neon"},
        {"theme": ""},
        {"width": WIDTH_RANGE[0] - 1},
        {"width": WIDTH_RANGE[1] + 1},
        {"height": HEIGHT_RANGE[0] - 1},
        {"height": HEIGHT_RANGE[1] + 1},
        {"weeks": WEEKS_RANGE[0] - 1},
        {"weeks": WEEKS_RANGE[1] + 1},
        {"width": -5},
    ],
)
def test_options_outside_their_range_are_refused(options: dict[str, Any]) -> None:
    with pytest.raises(ConfigError):
        CardOptions(**options)


def test_the_documented_limits_are_accepted() -> None:
    for width, height, weeks in (
        (WIDTH_RANGE[0], HEIGHT_RANGE[0], WEEKS_RANGE[0]),
        (WIDTH_RANGE[1], HEIGHT_RANGE[1], WEEKS_RANGE[1]),
    ):
        card = render_card(tiny_metrics(), CardOptions(width=width, height=height, weeks=weeks))
        assert parse(card).get("width") == str(width)


@pytest.mark.parametrize("theme", THEMES)
def test_every_theme_renders_with_its_palette(theme: str) -> None:
    card = render_card(tiny_metrics(), CardOptions(theme=theme))
    parse(card)
    has_media = "@media (prefers-color-scheme:light)" in card
    assert has_media == (theme == "auto")
    assert (svg_card.DARK.card in card) == (theme in ("dark", "auto"))
    assert (svg_card.LIGHT.card in card) == (theme in ("light", "auto"))


def test_every_colour_comes_from_the_theme_so_the_auto_card_can_switch(
    metrics: dict[str, Any],
) -> None:
    """No element carries a colour of its own: the theme rules (and the media query) decide."""
    card = render_card(metrics, CardOptions(theme="auto"))
    assert not re.search(r'(?:fill|stroke|stop-color)="#', card)
    assert not re.search(r"<linearGradient", card)  # flat: no brand gradient, no rainbow
    # both themes define every class the elements use
    used = {name for value in re.findall(r'class="([^"]+)"', card) for name in value.split()}
    css = card.split("<style>")[1].split("</style>")[0]
    for name in used:
        assert f".{name}{{" in css or f".{name}," in css or f",.{name}{{" in css, name


def test_the_themes_have_eight_distinct_language_colours() -> None:
    for palette in (svg_card.DARK, svg_card.LIGHT):
        assert len(palette.series) == svg_card.SERIES_SLOTS
        assert len(set(palette.series)) == svg_card.SERIES_SLOTS  # a slot is never reused
        assert palette.other not in palette.series
        assert len(set(palette.ramp)) == len(palette.ramp)


def test_the_language_bar_uses_the_colour_slots_in_order(metrics: dict[str, Any]) -> None:
    """Fixed order, never cycled; the folded tail ("Other") is muted. The legend repeats it."""
    root = parse(render_card(metrics))
    slot = re.compile(r"s[1-8]|so")
    segments = [
        rect.get("class", "")
        for rect in root.iter(f"{SVG}rect")
        if slot.fullmatch(rect.get("class", ""))
    ]
    items = metrics["languages"]["items"]
    expected = ["so" if item["other"] else f"s{index + 1}" for index, item in enumerate(items)]
    assert segments == expected
    dots = [
        dot.get("class", "")
        for dot in root.iter(f"{SVG}circle")
        if slot.fullmatch(dot.get("class", ""))  # the logo's dot is not a legend entry
    ]
    assert dots
    assert dots == expected[: len(dots)]  # a narrow card may leave the last entries out


# ---------------------------------------------------------------------------- layout


@pytest.mark.parametrize("width", [320, 400, 495, 640, 900, 1200])
@pytest.mark.parametrize("height", [160, 195, 300, 600])
def test_everything_stays_inside_the_card(width: int, height: int) -> None:
    card = render_card(tiny_metrics(), CardOptions(width=width, height=height))
    root = parse(card)
    for rect in root.iter(f"{SVG}rect"):
        x, y = float(rect.get("x", 0)), float(rect.get("y", 0))
        right, bottom = x + float(rect.get("width", 0)), y + float(rect.get("height", 0))
        assert x >= -0.01, rect.attrib
        assert right <= width + 0.01, rect.attrib
        assert y >= -0.01, rect.attrib
        assert bottom <= height + 0.01, rect.attrib
    for node in root.iter(f"{SVG}text"):
        x, y = float(node.get("x", 0)), float(node.get("y", 0))
        assert 0 <= x <= width
        assert 0 < y <= height


@pytest.mark.parametrize("weeks", [8, 20, 53])
@pytest.mark.parametrize("width", [320, 495, 1200])
def test_the_statistics_never_overlap_the_heatmap(width: int, weeks: int) -> None:
    rows = ((999_999_999, "commits"), (12_345, "active days"), (365, "day streak"))
    lay = svg_card._layout(width, DEFAULT_HEIGHT, weeks, rows)
    label_size = lay.label_size
    stats_right = lay.label_x + max(svg_card._width(label, label_size) for _, label in rows)
    assert stats_right <= lay.heat_x, (
        f"stats end at {stats_right}, the heatmap starts at {lay.heat_x}"
    )
    assert lay.heat_x + lay.heat_w <= width - PAD + 0.01


def test_a_narrow_card_shows_fewer_weeks_instead_of_overlapping_its_numbers() -> None:
    big = tiny_metrics()
    big["totals"]["commits"] = big["totals"]["activeDays"] = 999_999_999
    lines = texts(render_card(big, CardOptions(width=320, weeks=53)))
    (heading,) = [line for line in lines if line.startswith("LAST ")]
    shown = int(heading.split()[1])
    assert 1 <= shown < 53
    roomy = texts(render_card(big, CardOptions(width=1200, weeks=53)))
    assert "LAST 53 WEEKS" in roomy


def test_long_titles_are_shortened_to_fit() -> None:
    fitted = svg_card._fit("x" * 500, 16, 200, bold=True)
    assert fitted.endswith("\N{HORIZONTAL ELLIPSIS}")
    assert svg_card._width(fitted, 16, bold=True) <= 200
    assert svg_card._fit("short", 16, 200) == "short"
    assert svg_card._fit("anything", 16, 1) == "a"  # never returns nothing
    wide = "\N{CJK UNIFIED IDEOGRAPH-65E5}" * 80
    assert svg_card._width(svg_card._fit(wide, 16, 200), 16) <= 200


def test_text_widths_follow_the_font_metrics() -> None:
    assert svg_card._width("", 12) == 0
    assert svg_card._width("iii", 12) < svg_card._width("WWW", 12)
    assert svg_card._width("abc", 24) == pytest.approx(2 * svg_card._width("abc", 12))
    assert svg_card._width("abc", 12, bold=True) > svg_card._width("abc", 12)
    assert svg_card._width("123", 12, bold=True) == svg_card._width("123", 12)  # digits keep width
    assert svg_card._width("\N{CJK UNIFIED IDEOGRAPH-65E5}", 10) == 10
    assert svg_card._width("\N{LATIN SMALL LETTER E WITH ACUTE}", 10) == 6


def test_the_language_legend_says_how_many_entries_do_not_fit() -> None:
    items = [
        {"name": f"Language{i}", "changes": 100 - i, "share": 0.1, "other": False}
        for i in range(12)
    ]
    narrow = tiny_metrics(languages={"basis": "changedLines", "total": 1000, "items": items})
    lines = texts(render_card(narrow, CardOptions(width=320)))
    more = [line for line in lines if re.fullmatch(r"\+\d+ more", line)]
    assert len(more) == 1
    shown = sum(1 for line in lines if re.fullmatch(r"Language\d+ 10%", line))
    assert shown + int(more[0][1:].split()[0]) == 12
    wide = texts(render_card(narrow, CardOptions(width=1200)))
    assert sum(1 for line in wide if re.fullmatch(r"Language\d+ 10%", line)) > shown


def test_no_languages_says_so() -> None:
    empty = tiny_metrics(languages={"basis": "changedLines", "total": 0, "items": []})
    assert "No language data" in texts(render_card(empty))


def test_an_empty_history_still_makes_a_card() -> None:
    empty = compute_metrics(history([]), now=NOW)
    lines = texts(render_card(empty))
    assert lines.count("0") == 3
    assert "demo" in lines


# ------------------------------------------------------------------------------ helpers


def test_recent_levels_takes_the_last_columns_and_pads_the_left() -> None:
    days = [{"date": f"d{i}", "commits": i % 4, "level": 0} for i in range(21)]  # three weeks
    grid = recent_levels({"days": days}, 5)
    assert len(grid) == 5
    assert grid[:2] == [[-1] * 7, [-1] * 7]
    assert all(len(column) == 7 for column in grid)
    assert [level for column in grid[2:] for level in column if level < 0] == []
    last_two = recent_levels({"days": days}, 2)
    assert len(last_two) == 2
    assert all(len(column) == 7 for column in last_two)  # only the newest columns are kept


def test_the_unfinished_current_week_is_padded_with_not_yet() -> None:
    days = [{"date": f"d{i}", "commits": 1, "level": 1} for i in range(10)]  # 1 week + 3 days
    grid = recent_levels({"days": days}, 2)
    assert grid[1][:3] != [-1, -1, -1]
    assert grid[1][3:] == [-1, -1, -1, -1]


@pytest.mark.parametrize("calendar", [None, {}, {"days": []}])
def test_a_missing_calendar_is_all_empty_columns(calendar: dict[str, Any] | None) -> None:
    assert recent_levels(calendar, 4) == [[-1] * 7 for _ in range(4)]


def test_the_levels_are_recomputed_for_the_visible_weeks_only() -> None:
    # The "level" stored in the metrics is ignored: even a quiet window spans the whole ramp.
    days = [{"date": f"d{i}", "commits": i, "level": 0} for i in range(14)]
    levels = {level for column in recent_levels({"days": days}, 2) for level in column}
    assert levels == {0, 1, 2, 3, 4, 5}


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (3.0, "3"),
        (3.5, "3.5"),
        (3.25, "3.25"),
        (0, "0"),
        (-0.001, "0"),
        (100.0, "100"),
        (-2.5, "-2.5"),
    ],
)
def test_coordinates_are_compact(value: float, text: str) -> None:
    assert svg_card._n(value) == text


@pytest.mark.parametrize(
    ("timestamp", "expected"),
    [
        ("2021-01-04T10:00:00Z", "Jan 2021"),
        ("2026-12-31T23:59:59Z", "Dec 2026"),
        ("2021-13-04T10:00:00Z", ""),
        ("2021-00-04T10:00:00Z", ""),
        ("21-01", ""),
        ("", ""),
        (None, ""),
        ("abcd-ef-gh", ""),
    ],
)
def test_the_since_label(timestamp: object, expected: str) -> None:
    assert svg_card._since(timestamp) == expected
