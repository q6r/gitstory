"""The dashboard's own assets: cheap static checks that need no browser.

Real rendering is checked in a headless browser (see docs/architecture.md); these tests catch the
mistakes that do not need one: dangling anchors, calls to Aurora functions that do not exist,
raw colours in the stylesheet, network access and string-built HTML in the script.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

import pytest

from gitstory.render.html import read_asset

APP_JS = read_asset("app.js")
APP_CSS = read_asset("app.css")
TEMPLATE = read_asset("report.html")
AURORA_JS = read_asset("aurora.js")
CHARTS_JS = read_asset("aurora-charts.js")
AURORA_CSS = read_asset("aurora.css")

SECTION_IDS = ["story", "activity", "people", "code", "messages", "releases", "card"]


class Ids(HTMLParser):
    """Collects the ids of the template."""

    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        identifier = dict(attrs).get("id")
        if identifier:
            self.ids.add(identifier)


def template_ids() -> set[str]:
    ids = Ids()
    ids.feed(TEMPLATE)
    return ids.ids


def sections() -> list[tuple[str, str, str]]:
    """The (id, label, icon) rows of the table in app.js that the rail, crumbs and palette read."""
    block = APP_JS.split("const SECTIONS = [", 1)[1].split("];", 1)[0]
    return re.findall(r"\['(\w+)', '([^']+)', '([a-z-]+)'\]", block)


def shell_options() -> str:
    """The text of the object passed to Aurora.shell()."""
    return APP_JS.split("A.shell({", 1)[1].split("\n  });", 1)[0]


# ------------------------------------------------------------------------------ the page


def test_every_section_in_the_rail_is_created_by_the_script() -> None:
    rows = sections()
    assert [row[0] for row in rows] == SECTION_IDS
    # the rail of the shell holds at most seven destinations, and a phone has room for just that
    assert len(rows) <= 7
    for target, _label, _icon in rows:
        assert re.search(rf"(?:section\(|id: )'{target}'", APP_JS), f"nothing creates #{target}"


def test_every_link_in_the_script_points_at_a_section() -> None:
    assert set(re.findall(r"'#(\w+)'", APP_JS)) <= set(SECTION_IDS)
    assert "href: `#${id}`" in APP_JS  # the rail links are made from the table of sections


def test_every_id_the_script_looks_up_exists_in_the_template_or_in_the_script() -> None:
    template = template_ids()
    assert {"report-data", "card-sources"} <= template
    for looked_up in re.findall(r"getElementById\('([^']+)'\)", APP_JS):
        created = re.search(rf"id: '{looked_up}'|id: `{looked_up}", APP_JS)
        assert looked_up in template or created, looked_up


def test_the_template_is_a_complete_page_and_the_script_builds_the_shell() -> None:
    assert TEMPLATE.startswith("<!doctype html>")
    assert '<html lang="en" data-theme="{{theme}}">' in TEMPLATE
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in TEMPLATE
    assert '<meta name="color-scheme" content="dark light">' in TEMPLATE
    assert '<body class="a-body" data-live="{{live}}">' in TEMPLATE
    assert "<noscript>{{noscript}}</noscript>" in TEMPLATE
    # the page is almost empty: Aurora.shell() builds the rail, the bars, <main> and the skip link
    for built_by_the_shell in ("<main", "<header", "<nav", "<footer", "<a "):
        assert built_by_the_shell not in TEMPLATE
    # the order matters: the libraries first, then the report script
    scripts = re.findall(r'<script src="([^"]+)">', TEMPLATE)
    assert scripts == ["aurora.js", "aurora-charts.js", "app.js"]
    assert re.findall(r'<link rel="stylesheet" href="([^"]+)">', TEMPLATE) == [
        "aurora.css",
        "app.css",
    ]


def test_the_script_builds_the_shell_with_everything_a_keyboard_needs() -> None:
    assert APP_JS.count("A.shell({") == 1
    options = shell_options()
    for part in ("brand:", "rail:", "crumbs:", "views:", "subbarEnd:", "status:"):
        assert part in options, part
    # the search button (it opens the command palette) and the theme toggle stay in the top bar
    assert "search: false" not in options
    assert "theme: false" not in options
    assert "cmdk: true" in options  # and the palette is a rail tool as well
    # the rail and the breadcrumbs follow the scroll, and a destination chosen by the user wins
    assert "shell.setActive(" in APP_JS
    assert "shell.setCrumbs(" in APP_JS


def test_the_script_only_calls_shell_methods_that_exist() -> None:
    called = set(re.findall(r"\bshell\.(\w+)\(", APP_JS)) | set(
        re.findall(r"\bviews\.(\w+)\(", APP_JS)
    )
    assert {"setActive", "setCrumbs"} <= called
    made = set(re.findall(r"\bapi\.(\w+) = ", AURORA_JS))
    views = {"select", "setItems", "setCount", "value"}  # what Aurora.seg() returns
    assert called <= made | views, sorted(called - made - views)


def test_every_chart_card_goes_through_the_helper_the_view_switch_reaches() -> None:
    # the Charts/Tables switch of the sub bar flips every card made by chartCard(), and only those
    assert APP_JS.count("C.card(") == 1
    assert APP_JS.count("chartCard(") >= 9


def test_the_rail_tools_make_room_for_the_sections_on_a_phone() -> None:
    # seven sections and two tools do not fit a 360px bar: below 760px the tools group is hidden
    assert "id: 'json'" in APP_JS
    assert "id: 'palette'" in APP_JS
    group = r"\.a-rail__group:has\(> \.a-rail__item\[data-id=\"json\"\]\)"
    assert re.search(
        r"@media \(max-width: 760px\) \{\s*" + group + r" \{ display: none; \}", APP_CSS
    )


def test_the_sub_bar_scrolls_and_cannot_widen_the_page() -> None:
    # The pills of the sub bar may not fit a phone (or a long branch name and a few filters): the
    # bar scrolls sideways, and each pill holds its own label. The kit hides that label below 980px
    # by positioning it absolutely; without a positioned pill, a label beyond the edge of the
    # scrolling bar makes the whole page scroll sideways.
    assert re.search(r"\.a-subbar \{[^}]*overflow-x: auto", APP_CSS)
    assert re.search(r"\.a-pill \{[^}]*position: relative", APP_CSS)
    hidden_label = r"\.a-pill__label \{[^}]*position: absolute"
    assert re.search(hidden_label, AURORA_CSS)  # why the rule above exists


# ------------------------------------------------------------------------- the UI kit calls


def aurora_names() -> tuple[set[str], set[str]]:
    toolkit = set(re.findall(r"\bAurora\.(\w+)\s*=[^=]", AURORA_JS))
    for block in re.findall(r"Object\.assign\(Aurora, \{([^}]*)\}", AURORA_JS):
        toolkit |= {name.strip() for name in block.split(",") if name.strip()}
    charts = set(re.findall(r"\bAC\.(\w+)\s*=[^=]", CHARTS_JS))
    return toolkit, charts


def test_the_script_only_calls_aurora_functions_that_exist() -> None:
    toolkit, charts = aurora_names()
    assert {
        "shell",
        "stat",
        "tabs",
        "table",
        "segbar",
        "cmdk",
        "copy",
        "kv",
        "code",
        "live",
    } <= toolkit
    assert {"card", "line", "bars", "heatmap", "ring", "spark"} <= charts
    used_toolkit = set(re.findall(r"\bA\.(\w+)", APP_JS))
    used_charts = set(re.findall(r"\bC\.(\w+)", APP_JS))
    assert used_toolkit
    assert used_charts
    assert used_toolkit <= toolkit, sorted(used_toolkit - toolkit)
    assert used_charts <= charts, sorted(used_charts - charts)


def test_the_script_uses_known_icons_only() -> None:
    icon_names = set(
        re.findall(r"^\s{4}'?([a-z-]+)'?: ", AURORA_JS.split("const ICONS = {")[1], re.M)
    )
    facts = APP_JS.split("const FACT_ICON = {")[1].split("};")[0]
    used = (
        set(re.findall(r"icon\('([a-z-]+)'", APP_JS))  # A.icon('refresh', ...)
        | set(re.findall(r"pill\('([a-z-]+)'", APP_JS))  # the sub bar pills
        | set(re.findall(r"(?:icon|mark): '([a-z-]+)'", APP_JS))  # the shell: logo, tools, views
        | {icon for _id, _label, icon in sections()}  # the rail
        | set(re.findall(r":\s*'([a-z-]+)'[,\s}]", facts))  # the highlight cards
    )
    assert len(used) > 15
    assert used <= icon_names | {"x"}, sorted(used - icon_names)


def test_the_script_never_touches_the_network_except_its_own_refresh_endpoint() -> None:
    assert not re.search(r"https?://", APP_JS)
    assert "XMLHttpRequest" not in APP_JS
    assert "WebSocket" not in APP_JS
    assert "EventSource" not in APP_JS
    assert "sendBeacon" not in APP_JS
    assert "import(" not in APP_JS
    assert re.findall(r"fetch\(([^,)]*)", APP_JS) == ["'/api/refresh'"]


def test_the_script_is_free_of_debugging_leftovers_and_global_state() -> None:
    for leftover in ("console.", "debugger", "alert(", "localStorage", "document.cookie"):
        assert leftover not in APP_JS, leftover
    assert not re.search(r"\b(?:TO" + r"DO|FIX" + r"ME)\b", APP_JS)  # no unfinished-work notes
    assert APP_JS.count("(function () {") == 1
    assert "'use strict';" in APP_JS
    assert re.search(r"^\s*var\s", APP_JS, re.M) is None  # block-scoped declarations only


# ---------------------------------------------------------------------------- the stylesheet


def test_the_stylesheet_uses_the_ui_kit_tokens_only() -> None:
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", APP_CSS), "a raw hex colour"
    assert not re.search(r"\b(?:rgb|rgba|hsl|hsla|oklch|lab)\(", APP_CSS), "a raw colour function"
    assert "url(" not in APP_CSS
    assert "@import" not in APP_CSS
    assert "@font-face" not in APP_CSS
    rules = re.sub(r"/\*.*?\*/", "", APP_CSS, flags=re.S)  # the comments may say what is not there
    assert "gradient(" not in rules
    assert "shadow" not in rules  # flat cards: no shadows of its own
    for family in re.findall(r"font-family:([^;]+);", APP_CSS):
        assert "var(--font-" in family
    for weight in re.findall(r"font:\s*(\d{3})\b", APP_CSS):
        assert int(weight) <= 600, weight  # weights 500 and 600, never heavier
    tokens = set(re.findall(r"var\((--[\w-]+)", APP_CSS))
    defined = set(re.findall(r"^\s*(--[\w-]+):", AURORA_CSS, re.M))
    assert tokens, "the stylesheet should use tokens"
    assert tokens <= defined | {"--gap"}, sorted(tokens - defined)


def test_motion_belongs_to_the_ui_kit_and_it_respects_reduced_motion() -> None:
    # the kit owns every animation, and switches them (and smooth scrolling) off when asked to
    assert "scroll-behavior: smooth" in AURORA_CSS
    assert "prefers-reduced-motion: reduce" in AURORA_CSS
    assert "scroll-behavior: auto !important" in AURORA_CSS
    # the page stylesheet adds none of its own
    for motion in ("animation", "transition", "scroll-behavior", "@keyframes"):
        assert motion not in APP_CSS, motion


def test_every_class_the_script_creates_is_styled_or_comes_from_aurora() -> None:
    own = set(re.findall(r"\b(gs-[\w-]+)", APP_JS))
    styled = set(re.findall(r"\.(gs-[\w-]+)", APP_CSS))
    template_classes = set(re.findall(r"\b(gs-[\w-]+)", TEMPLATE))
    assert own, "the script should use its own classes"
    unstyled = (own | template_classes) - styled
    assert not unstyled, sorted(unstyled)


def test_every_aurora_class_the_script_uses_exists_in_the_ui_kit() -> None:
    classes = set(re.findall(r"class: '([^']+)'", APP_JS)) | set(
        re.findall(r"class: `([^`$]+)", APP_JS)
    )
    wanted = {name for text in classes for name in text.split() if name.startswith("a-")}
    assert {
        "a-hero",
        "a-headline",
        "a-lede",
        "a-card",
        "a-grid",
        "a-pagehead",
        "a-nav-btn",
    } <= wanted
    known = set(re.findall(r"\.(a-[\w-]+)", AURORA_CSS)) | set(
        re.findall(r"'(a-[\w-]+)'", AURORA_JS)
    )
    assert wanted <= known, sorted(wanted - known)


# --------------------------------------------------------------------------- the UI kit files


@pytest.mark.parametrize("name", ["aurora.css", "aurora.js", "aurora-charts.js"])
def test_the_bundled_ui_kit_is_present_and_marked(name: str) -> None:
    text = read_asset(name)
    assert "Aurora" in text.split("\n", 3)[0] + text.split("\n", 3)[1]
    assert "MIT" in text[:400]
    assert len(text) > 20_000


def test_the_ui_kit_files_never_load_anything_from_the_network() -> None:
    for text in (AURORA_JS, CHARTS_JS):
        assert not re.search(r"fetch\(|XMLHttpRequest|WebSocket|importScripts|sendBeacon", text)
    assert "@import" not in AURORA_CSS
    assert "@font-face" not in AURORA_CSS
