"""Render the metrics into ONE self-contained HTML file.

The page template and its CSS/JS live in package data (``gitstory/assets``): the Aurora UI kit
(``aurora.css``, ``aurora.js``, ``aurora-charts.js``) plus the report's own ``app.css`` and
``app.js``. Every local stylesheet and script the template references is inlined, the metrics are
embedded as an inert JSON data block, and a hash-based Content-Security-Policy is added, so the
result works offline, from ``file://`` and under a strict CSP (no ``unsafe-inline`` for scripts,
no external requests).

Nothing from the repository is ever placed into the page except through
:func:`gitstory.util.json_for_html` (the data block), :func:`html.escape` (the title and the
no-script summary) or as base64 (the card previews).
"""

from __future__ import annotations

import base64
import hashlib
import html
import re
from collections.abc import Mapping
from importlib import resources
from typing import Any

from gitstory.model import ConfigError
from gitstory.render.svg_card import CardOptions, render_card
from gitstory.util import json_for_html

THEMES = ("auto", "dark", "light")
TEMPLATE = "report.html"
CSP_PLACEHOLDER = "<!--gitstory:csp-->"
MIDDLE_DOT = "\N{MIDDLE DOT}"
SVG_DATA_URI = "data:image/svg+xml;base64,"

_ASSET_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_STYLE_LINK = re.compile(r'<link rel="stylesheet" href="([^"]+)">')
_SCRIPT_TAG = re.compile(r'<script src="([^"]+)"></script>')
_INLINE_BLOCK = re.compile(r"<(script|style)([^>]*)>(.*?)</\1>", re.DOTALL)
_PLACEHOLDER = re.compile(r"\{\{(theme|title|data|live|noscript|card_dark|card_light)\}\}")
_UNSAFE_INLINE = re.compile(r"</(?:script|style)|<!--", re.IGNORECASE)


def read_asset(name: str) -> str:
    """Return the text of a packaged asset.

    ``name`` must be a plain file name; nothing outside ``gitstory/assets`` can be read.
    """
    if not _ASSET_NAME.fullmatch(name):
        raise ValueError(f"invalid asset name: {name!r}")
    return (resources.files("gitstory") / "assets" / name).read_text(encoding="utf-8")


def _inline(name: str, tag: str) -> str:
    body = read_asset(name).strip("\n")
    if _UNSAFE_INLINE.search(body):
        raise ValueError(f"asset {name!r} cannot be inlined safely")
    return f"<{tag}>\n{body}\n</{tag}>"


def inline_hashes(document: str) -> tuple[list[str], list[str]]:
    """Return CSP source expressions for the executable inline scripts and the inline styles.

    JSON data blocks (``type="application/json"``) are never executed and need no hash.
    """
    scripts: dict[str, None] = {}
    styles: dict[str, None] = {}
    for match in _INLINE_BLOCK.finditer(document):
        tag, attributes, body = match.groups()
        if tag == "script" and ("src=" in attributes or "application/json" in attributes):
            continue
        digest = base64.b64encode(hashlib.sha256(body.encode("utf-8")).digest()).decode("ascii")
        (scripts if tag == "script" else styles)[f"'sha256-{digest}'"] = None
    return list(scripts), list(styles)


def content_security_policy(document: str, *, header: bool = False) -> str:
    """Build the Content-Security-Policy for a rendered report.

    Only the exact inline scripts and styles of ``document`` may run, nothing is loaded from
    elsewhere (images are limited to ``data:`` URIs, the card previews), and the page may only
    talk to its own origin (the ``serve`` API). The ``frame-ancestors`` directive is only valid
    in an HTTP header, not in a ``<meta>`` tag.
    """
    scripts, styles = inline_hashes(document)
    directives = [
        "default-src 'none'",
        "script-src " + (" ".join(scripts) or "'none'"),
        "style-src " + (" ".join(styles) or "'none'"),
        "img-src data:",
        "connect-src 'self'",
        "base-uri 'none'",
        "form-action 'none'",
    ]
    if header:
        directives.append("frame-ancestors 'none'")
    return "; ".join(directives)


def _card_data_uri(metrics: Mapping[str, Any], theme: str) -> str:
    """The README card in one theme as a ``data:`` URI (pure base64, nothing to escape)."""
    svg = render_card(metrics, CardOptions(theme=theme))
    return SVG_DATA_URI + base64.b64encode(svg.encode("utf-8")).decode("ascii")


def _noscript(metrics: Mapping[str, Any]) -> str:
    """A plain-HTML summary for readers without JavaScript; every value is escaped."""
    totals, streaks = metrics["totals"], metrics["streaks"]
    facts = (
        ("Commits", totals["commits"]),
        ("Authors", totals["authors"]),
        ("Active days", totals["activeDays"]),
        ("Longest streak (days)", streaks["longest"]),
        ("Current streak (days)", streaks["current"]),
        ("Bus factor", metrics["busFactor"]["value"]),
    )
    cells = "".join(
        f"<div><dt>{html.escape(label)}</dt><dd>{int(value):,}</dd></div>" for label, value in facts
    )
    name = html.escape(str(metrics["repository"]["name"]))
    return (
        f'<section class="gs-noscript"><h1>{name}</h1>'
        "<p>The charts in this report are drawn with JavaScript. The headline numbers are "
        "listed here; the full data is embedded in the page as JSON.</p>"
        f"<dl>{cells}</dl></section>"
    )


def render_report(metrics: Mapping[str, Any], *, theme: str = "auto", live: bool = False) -> str:
    """Render ``metrics`` (a ``gitstory.metrics/v1`` document) as a single HTML string.

    ``live`` marks a page served by ``gitstory serve``: it then offers a refresh button that
    talks to the server's ``/api/refresh``. A file written by ``gitstory report`` is static.
    """
    if theme not in THEMES:
        raise ConfigError(f"theme must be one of {', '.join(THEMES)}")
    document = _STYLE_LINK.sub(lambda match: _inline(match[1], "style"), read_asset(TEMPLATE))
    document = _SCRIPT_TAG.sub(lambda match: _inline(match[1], "script"), document)
    values = {
        "theme": theme,
        "title": html.escape(f"{metrics['repository']['name']} {MIDDLE_DOT} gitstory"),
        "data": json_for_html(metrics),
        "live": "true" if live else "false",
        "noscript": _noscript(metrics),
        "card_dark": _card_data_uri(metrics, "dark"),
        "card_light": _card_data_uri(metrics, "light"),
    }
    # One pass, so substituted text (a hostile repository name, say) is never re-scanned.
    document = _PLACEHOLDER.sub(lambda match: values[match[1]], document)
    policy = html.escape(content_security_policy(document), quote=True)
    meta = f'<meta http-equiv="Content-Security-Policy" content="{policy}">'
    return document.replace(CSP_PLACEHOLDER, meta, 1)
