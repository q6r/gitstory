"""The self-contained HTML report: inlining, the content-security policy and hostile input."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from typing import Any

import pytest
from builders import commit, history, when
from builders import tag as make_tag

from gitstory.metrics import compute_metrics
from gitstory.model import ConfigError, FileChange
from gitstory.render import html as render_html
from gitstory.render.html import (
    CSP_PLACEHOLDER,
    SVG_DATA_URI,
    THEMES,
    content_security_policy,
    inline_hashes,
    read_asset,
    render_report,
)
from gitstory.render.svg_card import CardOptions, render_card

ASSETS = ("aurora.css", "aurora.js", "aurora-charts.js", "app.css", "app.js", "report.html")
PLACEHOLDERS = ("{{theme}}", "{{title}}", "{{data}}", "{{live}}", "{{noscript}}", "{{card_dark}}")
EVIL = "</script><img src=x onerror=alert(1)>"
LINE_SEPARATOR, PARAGRAPH_SEPARATOR = "\N{LINE SEPARATOR}", "\N{PARAGRAPH SEPARATOR}"
NOW = when("2024-06-30T12:00:00+00:00")


class Dom(HTMLParser):
    """A tiny recorder: start tags with their attributes, and the text of script/style blocks."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.tags: list[tuple[str, dict[str, str]]] = []
        self.blocks: list[tuple[str, dict[str, str], str]] = []
        self._open: tuple[str, dict[str, str], list[str]] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name: value or "" for name, value in attrs}
        self.tags.append((tag, attributes))
        if tag in ("script", "style"):
            self._open = (tag, attributes, [])

    def handle_data(self, data: str) -> None:
        if self._open:
            self._open[2].append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._open and self._open[0] == tag:
            name, attributes, parts = self._open
            self.blocks.append((name, attributes, "".join(parts)))
            self._open = None


def parse(page: str) -> Dom:
    dom = Dom()
    dom.feed(page)
    dom.close()
    return dom


def island(dom: Dom) -> str:
    (text,) = [body for name, attrs, body in dom.blocks if attrs.get("id") == "report-data"]
    return text


def hostile_metrics() -> dict[str, Any]:
    """Metrics from a repository in which every attacker-controlled field is a payload."""
    separators = f"a{LINE_SEPARATOR}b{PARAGRAPH_SEPARATOR}c & <!-- ]]> "
    payloads = [
        EVIL,
        separators + "{{data}} {{title}} {{live}}",
        '"><script>alert(2)</script>',
    ]
    commits = [
        commit(
            f"2024-06-2{i}T10:00:00+00:00",
            author=payloads[i % 3],
            email=f"evil{i}@example.com",
            subject=payloads[(i + 1) % 3] + " fix: parser",
            files=[FileChange(f"dir/{payloads[i % 3]}.py", 5, 1)],
        )
        for i in range(1, 6)
    ]
    tags = (make_tag(payloads[0], "2024-06-01T00:00:00+00:00"),)
    return compute_metrics(history(commits, tags=tags, name=EVIL), now=NOW)


# ------------------------------------------------------------------------- assembly


def test_the_report_is_one_self_contained_document(metrics: dict[str, Any]) -> None:
    page = render_report(metrics)
    dom = parse(page)
    assert page.startswith("<!doctype html>")
    assert not any(name == "link" for name, _ in dom.tags)
    assert not any("src" in attrs for name, attrs in dom.tags if name == "script")
    assert [name for name, _, _ in dom.blocks].count("style") == 2  # Aurora and the report
    assert [name for name, _, _ in dom.blocks].count("script") == 4  # data, two libraries, report
    assert CSP_PLACEHOLDER not in page
    assert not any(token in page for token in PLACEHOLDERS)
    assert "window.Aurora" in page
    assert "AuroraCharts" in page
    assert page.count("<script") == page.count("</script>")
    assert page.count("<style") == page.count("</style>")


def test_the_report_has_no_external_references(metrics: dict[str, Any]) -> None:
    dom = parse(render_report(metrics))
    for name, attrs in dom.tags:
        for attribute in ("src", "href", "action", "data", "poster", "srcset", "formaction"):
            value = attrs.get(attribute)
            if value is not None:
                assert value.startswith("#"), f"<{name} {attribute}={value[:40]!r}>"
    for name, _, body in dom.blocks:
        if name == "style":
            assert "@import" not in body
            for _quote, target in re.findall(r"url\(\s*(['\"]?)(.*?)\1\s*\)", body):
                assert target.startswith(("data:", "#")), f"external url() in CSS: {target[:40]}"
    namespaces = set()
    for name, attrs, body in dom.blocks:
        if name == "script" and attrs.get("type") != "application/json":
            namespaces |= set(re.findall(r"https?://[^\s'\"`)\\]+", body))
    assert namespaces <= {"http://www.w3.org/2000/svg"}  # an XML namespace, never fetched


def test_the_page_does_not_name_the_machine_it_was_made_on(metrics: dict[str, Any]) -> None:
    page = render_report(metrics)
    for needle in ("C:\\Users", "C:/Users", "/home/", "AppData", "localhost:"):
        assert needle not in page


def test_rendering_is_deterministic_and_size_is_reasonable(metrics: dict[str, Any]) -> None:
    first, second = render_report(metrics), render_report(metrics)
    assert first == second
    assert len(first.encode("utf-8")) < 900_000


def test_the_report_of_an_empty_history_renders(metrics: dict[str, Any]) -> None:
    empty = compute_metrics(history([]), now=NOW)
    page = render_report(empty)
    assert json.loads(island(parse(page)))["totals"]["commits"] == 0
    assert "<noscript>" in page


@pytest.mark.parametrize("name", THEMES)
def test_every_theme_is_applied_to_the_root_element(metrics: dict[str, Any], name: str) -> None:
    assert f'<html lang="en" data-theme="{name}">' in render_report(metrics, theme=name)


@pytest.mark.parametrize("bad", ["neon", "", "DARK", "dark ", '"><script>alert(1)</script>'])
def test_an_unknown_theme_is_refused(metrics: dict[str, Any], bad: str) -> None:
    with pytest.raises(ConfigError, match="theme must be one of"):
        render_report(metrics, theme=bad)


def test_the_live_flag_tells_the_page_whether_a_server_is_behind_it(
    metrics: dict[str, Any],
) -> None:
    assert '<body class="a-body" data-live="false">' in render_report(metrics)
    assert '<body class="a-body" data-live="true">' in render_report(metrics, live=True)


def test_the_card_previews_are_the_real_cards_as_data_uris(metrics: dict[str, Any]) -> None:
    dom = parse(render_report(metrics))
    (sources,) = [attrs for name, attrs in dom.tags if attrs.get("id") == "card-sources"]
    for theme in ("dark", "light"):
        uri = sources[f"data-{theme}"]
        assert uri.startswith(SVG_DATA_URI)
        svg = base64.b64decode(uri.removeprefix(SVG_DATA_URI), validate=True).decode("utf-8")
        assert svg == render_card(metrics, CardOptions(theme=theme))
        ET.fromstring(svg)  # well-formed


# ---------------------------------------------------------------------------- assets


@pytest.mark.parametrize("name", ASSETS)
def test_packaged_assets_are_safe_to_inline(name: str) -> None:
    text = read_asset(name)
    if name != "report.html":  # the template itself holds the placeholders and the CSP marker
        assert not re.search(r"</(?:script|style)|<!--", text, re.IGNORECASE)
        assert not any(token in text for token in PLACEHOLDERS), "would be rewritten while inlining"
    assert "\r" not in text
    assert not text.startswith("\ufeff")


def test_the_dashboard_script_never_builds_html_from_strings() -> None:
    code = read_asset("app.js")
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert forbidden not in code
    assert "new Function" not in code
    assert "setAttribute('style'" not in code
    assert 'setAttribute("style"' not in code


@pytest.mark.parametrize(
    "name",
    [
        "../secret",
        "..\\secret",
        "/etc/passwd",
        "a/b.css",
        "",
        ".hidden",
        "x" * 65,
        "app.js\x00",
        "C:\\x",
        "%2e%2e",
    ],
)
def test_assets_are_read_by_plain_name_only(name: str) -> None:
    with pytest.raises(ValueError, match="invalid asset name"):
        read_asset(name)


def test_a_valid_looking_but_missing_asset_is_a_file_error() -> None:
    with pytest.raises(FileNotFoundError):
        read_asset("does-not-exist.js")


def test_an_asset_that_could_close_its_element_is_refused(
    metrics: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    real = render_html.read_asset
    monkeypatch.setattr(
        render_html,
        "read_asset",
        lambda name: "alert('</script><script>')" if name == "app.js" else real(name),
    )
    with pytest.raises(ValueError, match="cannot be inlined safely"):
        render_report(metrics)


# ------------------------------------------------------------------------------- CSP


def test_the_policy_lists_exactly_the_inline_blocks_of_the_page(metrics: dict[str, Any]) -> None:
    page = render_report(metrics)
    dom = parse(page)
    expected: dict[str, set[str]] = {"script": set(), "style": set()}
    for name, attrs, body in dom.blocks:
        if name == "script" and attrs.get("type") == "application/json":
            continue
        digest = base64.b64encode(hashlib.sha256(body.encode("utf-8")).digest()).decode("ascii")
        expected[name].add(f"'sha256-{digest}'")
    (meta,) = [a for n, a in dom.tags if n == "meta" and a.get("http-equiv")]
    assert meta["http-equiv"] == "Content-Security-Policy"
    directives = dict(
        part.strip().split(" ", 1)
        for part in meta["content"].replace("&#x27;", "'").split(";")
        if part.strip()
    )
    assert set(directives["script-src"].split()) == expected["script"]
    assert set(directives["style-src"].split()) == expected["style"]
    assert len(expected["script"]) == 3
    assert directives["default-src"] == "'none'"
    assert directives["connect-src"] == "'self'"
    assert directives["img-src"] == "data:"
    assert directives["base-uri"] == "'none'"
    assert directives["form-action"] == "'none'"
    assert "frame-ancestors" not in directives  # not allowed in a <meta> element
    assert "unsafe" not in meta["content"]


def test_the_header_policy_adds_frame_ancestors() -> None:
    page = "<script>a()</script>"
    assert "frame-ancestors" not in content_security_policy(page)
    assert content_security_policy(page, header=True).endswith("frame-ancestors 'none'")


def test_inline_hashes_cover_executable_blocks_only() -> None:
    document = (
        '<script>a()</script><script>a()</script><script src="x.js"></script>'
        '<script type="application/json">{"k":1}</script><style>b{}</style>'
    )
    scripts, styles = inline_hashes(document)
    digest = base64.b64encode(hashlib.sha256(b"a()").digest()).decode("ascii")
    assert scripts == [f"'sha256-{digest}'"]  # duplicates collapse, data and external are skipped
    assert len(styles) == 1
    assert content_security_policy("") == (
        "default-src 'none'; script-src 'none'; style-src 'none'; img-src data:; "
        "connect-src 'self'; base-uri 'none'; form-action 'none'"
    )


def test_a_tampered_script_would_not_match_the_policy(metrics: dict[str, Any]) -> None:
    page = render_report(metrics)
    tampered = page.replace("\n</script>", "\n;alert(1)\n</script>", 1)  # end of the first library
    assert tampered != page
    assert inline_hashes(tampered) != inline_hashes(page)


# ------------------------------------------------------------------------ hostile data


def test_a_hostile_repository_cannot_break_out_of_the_data_block() -> None:
    """Payloads that close the script element must arrive as inert, escaped JSON."""
    metrics = hostile_metrics()
    page = render_report(metrics, live=True)
    dom = parse(page)
    text = island(dom)

    # The browser's JSON.parse sees exactly the original strings ...
    assert json.loads(text) == json.loads(json.dumps(metrics))
    assert EVIL in json.dumps(metrics)

    # ... but the raw characters that could end the element or the string literal are gone,
    for dangerous in ("<", ">", "&", LINE_SEPARATOR, PARAGRAPH_SEPARATOR):
        assert dangerous not in text
    # ... replaced by JSON escapes (written with a doubled backslash on purpose: these are the
    # six characters backslash, u, 0, 0, 3, c and so on, not the characters they stand for).
    for escape in ("\\u003c", "\\u003e", "\\u0026", "\\u2028", "\\u2029"):
        assert escape in text
    assert "\\u003c/script\\u003e\\u003cimg src=x onerror=alert(1)\\u003e" in text

    # The parsed page has no element or handler that came from the repository.
    names = [name for name, _ in dom.tags]
    assert "img" not in names
    assert names.count("script") == 4
    for _, attrs in dom.tags:
        assert not any(attribute.startswith("on") for attribute in attrs)
    assert "<img src=x" not in page
    assert "</script><" not in page  # inline blocks are always followed by a line break
    assert page.count("<script") == page.count("</script>") == 4


def test_placeholders_in_repository_text_are_not_expanded_twice() -> None:
    name = "{{data}}{{live}}{{noscript}}{{card_dark}}"
    metrics = compute_metrics(history([commit("2024-06-29T10:00:00+00:00")], name=name), now=NOW)
    page = render_report(metrics)
    text = island(parse(page))
    assert f"<title>{name} \N{MIDDLE DOT} gitstory</title>" in page  # still the literal text
    assert page.count(text) == 1  # the data block is not copied into the title or anywhere else
    assert 'data-live="false"' in page
    assert f"<h1>{name}</h1>" in page


def test_the_title_and_the_summary_are_html_escaped() -> None:
    metrics = hostile_metrics()
    page = render_report(metrics)
    assert (
        "<title>&lt;/script&gt;&lt;img src=x onerror=alert(1)&gt; \N{MIDDLE DOT} gitstory</title>"
        in page
    )
    summary = re.search(r"<noscript>(.*?)</noscript>", page, re.DOTALL)
    assert summary is not None
    assert "<img" not in summary[1]
    assert "&lt;/script&gt;" in summary[1]


def test_the_summary_for_readers_without_javascript_has_the_headline_numbers(
    metrics: dict[str, Any],
) -> None:
    page = render_report(metrics)
    summary = re.search(r"<noscript>(.*?)</noscript>", page, re.DOTALL)
    assert summary is not None
    body = summary[1]
    assert f"<dd>{metrics['totals']['commits']:,}</dd>" in body
    assert f"<dd>{metrics['totals']['authors']:,}</dd>" in body
    assert f"<h1>{metrics['repository']['name']}</h1>" in body
    assert "<dt>Longest streak (days)</dt>" in body
