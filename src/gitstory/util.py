"""Shared helpers: clocks, timestamps, filter-date parsing, text sanitising and JSON output.

Everything that comes out of a repository (author names, subjects, paths, tag names) is
untrusted. :func:`sanitize_text` is the single place where such text is made safe to print
in a terminal; the HTML and SVG renderers add their own escaping on top of it.
"""

from __future__ import annotations

import calendar
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from gitstory.model import UTC, ConfigError

_GIT_TIMESTAMP = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:(Z)|([+-])(\d{2}):(\d{2}))", re.ASCII
)
_ABSOLUTE_WHEN = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})"
    r"(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?\s*(Z|[+-]\d{2}:?\d{2})?)?",
    re.IGNORECASE | re.ASCII,
)
_RELATIVE_WHEN = re.compile(
    r"(\d{1,4})\s*(d|days?|w|weeks?|m|months?|y|years?)(?:\s+ago)?", re.IGNORECASE | re.ASCII
)

WHEN_HELP = "use YYYY-MM-DD, an ISO-8601 timestamp, or a relative value such as 90d, 12w, 6m or 1y"


def utc_now() -> datetime:
    """Return the current time in UTC.

    ``SOURCE_DATE_EPOCH`` (the reproducible-builds convention) overrides the clock so that
    the same repository always yields byte-identical output.
    """
    raw = os.environ.get("SOURCE_DATE_EPOCH")
    if raw is None or raw == "":
        return datetime.now(tz=UTC)
    if not (raw.isascii() and raw.isdigit()):
        raise ConfigError("SOURCE_DATE_EPOCH must be a non-negative integer (seconds)")
    try:
        return datetime.fromtimestamp(int(raw), tz=UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise ConfigError("SOURCE_DATE_EPOCH is out of range") from exc


def parse_git_timestamp(text: str) -> datetime | None:
    """Parse the strict ISO-8601 form of ``%aI`` / ``creatordate:iso-strict``.

    Accepts both ``+00:00`` and ``Z`` for UTC (Python 3.10 cannot read ``Z`` itself).
    Returns ``None`` for anything malformed or out of range instead of raising, because
    git output is untrusted.
    """
    match = _GIT_TIMESTAMP.fullmatch(text)
    if match is None:
        return None
    year, month, day, hour, minute, second, zulu, sign, off_h, off_m = match.groups()
    try:
        if zulu:
            tz = UTC
        else:
            offset = timedelta(hours=int(off_h), minutes=int(off_m))
            tz = timezone(-offset if sign == "-" else offset)  # rejects offsets of 24h or more
        moment = datetime(
            int(year), int(month), int(day), int(hour), int(minute), int(second), tzinfo=tz
        )
        moment.astimezone(UTC)  # rejects instants that do not fit in a UTC datetime
    except (ValueError, OverflowError):
        return None
    return moment


def iso_utc(moment: datetime) -> str:
    """Format an aware datetime as ``YYYY-MM-DDTHH:MM:SSZ`` (UTC, whole seconds)."""
    return moment.astimezone(UTC).replace(microsecond=0, tzinfo=None).isoformat() + "Z"


def _shift_months(moment: datetime, months: int) -> datetime:
    total = moment.year * 12 + (moment.month - 1) - months
    year, month0 = divmod(total, 12)
    month = month0 + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


def parse_when(text: str, *, now: datetime, end_of_day: bool = False) -> datetime:
    """Parse a ``--since`` / ``--until`` value into an aware UTC datetime.

    Git's own date parser accepts almost any garbage silently, so values are validated
    here and handed to git as unambiguous ISO-8601 timestamps. Naive values are UTC. A
    date without a time means the start of that day, or its last second if ``end_of_day``.
    """
    value = text.strip()
    relative = _RELATIVE_WHEN.fullmatch(value)
    if relative is not None:
        amount = int(relative.group(1))
        unit = relative.group(2).lower()[0]
        try:
            if unit == "d":
                return now - timedelta(days=amount)
            if unit == "w":
                return now - timedelta(weeks=amount)
            return _shift_months(now, amount * (12 if unit == "y" else 1))
        except (OverflowError, ValueError) as exc:
            raise ConfigError(f"date {text!r} is out of range") from exc

    absolute = _ABSOLUTE_WHEN.fullmatch(value)
    if absolute is None:
        raise ConfigError(f"cannot understand date {text!r}: {WHEN_HELP}")
    year, month, day, hour, minute, second, zone = absolute.groups()
    tz = UTC
    if zone and zone.upper() != "Z":
        digits = zone[1:].replace(":", "")
        offset = timedelta(hours=int(digits[:2]), minutes=int(digits[2:]))
        tz = timezone(-offset if zone[0] == "-" else offset)
    try:
        if hour is None:
            moment = datetime(int(year), int(month), int(day), tzinfo=tz)
            if end_of_day:
                moment += timedelta(days=1) - timedelta(seconds=1)
        else:
            moment = datetime(
                int(year),
                int(month),
                int(day),
                int(hour),
                int(minute),
                int(second or 0),
                tzinfo=tz,
            )
        return moment.astimezone(UTC)
    except (ValueError, OverflowError) as exc:
        raise ConfigError(f"date {text!r} is not a valid calendar date or time") from exc


ELLIPSIS = "\N{HORIZONTAL ELLIPSIS}"
_REPLACEMENT = "\N{REPLACEMENT CHARACTER}"
_BIDI_CONTROLS = (*range(0x202A, 0x202F), *range(0x2066, 0x206A))  # "Trojan Source" overrides


def _build_sanitize_table() -> dict[int, str]:
    table: dict[int, str] = {}
    for code in range(0x20):
        table[code] = chr(0x2400 + code)  # visible "control pictures": ESC becomes U+241B
    table[0x7F] = "\N{SYMBOL FOR DELETE}"
    for code in range(0x80, 0xA0):  # C1 controls (8-bit CSI and friends)
        table[code] = _REPLACEMENT
    for code in _BIDI_CONTROLS:
        table[code] = _REPLACEMENT
    table[ord("\N{LINE SEPARATOR}")] = " "  # these break naive string literals
    table[ord("\N{PARAGRAPH SEPARATOR}")] = " "
    table[0xFEFF] = ""
    return table


_SANITIZE_TABLE = _build_sanitize_table()


def truncate(text: str, limit: int) -> str:
    """Shorten ``text`` to at most ``limit`` characters, ending in an ellipsis if cut."""
    if limit < 1:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + ELLIPSIS


def sanitize_text(text: str, limit: int | None = None, *, strip: bool = True) -> str:
    """Make untrusted text safe to display.

    Control characters (including ESC, which starts terminal escape sequences) become
    visible placeholders, bidirectional overrides are replaced, and the result is optionally
    trimmed and truncated to ``limit`` characters.
    """
    cleaned = text.translate(_SANITIZE_TABLE)
    if strip:
        cleaned = cleaned.strip()
    if limit is not None:
        cleaned = truncate(cleaned, limit)
    return cleaned


def ratio(part: float, whole: float, digits: int = 4) -> float:
    """Return ``part / whole`` rounded to ``digits`` places, or 0.0 when ``whole`` is 0."""
    if not whole:
        return 0.0
    return round(part / whole, digits)


def to_json(data: Any, *, indent: int | None = 2) -> str:
    """Serialise ``data`` as UTF-8-friendly JSON; NaN and Infinity are rejected."""
    separators = (",", ": ") if indent else (",", ":")
    return json.dumps(
        data, ensure_ascii=False, indent=indent, separators=separators, allow_nan=False
    )


def json_for_html(data: Any) -> str:
    """Serialise ``data`` for embedding inside a ``<script type="application/json">`` block.

    ``<``, ``>``, ``&`` and the Unicode line separators are written as ``\\uXXXX`` escapes,
    which are valid inside JSON strings, so no payload can close the script element.
    """
    text = to_json(data, indent=None)
    for char, escape in (
        ("<", "\\u003c"),
        (">", "\\u003e"),
        ("&", "\\u0026"),
        ("\N{LINE SEPARATOR}", "\\u2028"),
        ("\N{PARAGRAPH SEPARATOR}", "\\u2029"),
    ):
        text = text.replace(char, escape)
    return text
