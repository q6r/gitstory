"""Clocks, timestamps, filter dates, text sanitising and JSON helpers."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from gitstory.model import ConfigError
from gitstory.util import (
    ELLIPSIS,
    iso_utc,
    json_for_html,
    parse_git_timestamp,
    parse_when,
    ratio,
    sanitize_text,
    to_json,
    truncate,
    utc_now,
)

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
UTC = timezone.utc


def east(hours: float) -> timezone:
    return timezone(timedelta(hours=hours))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2024-03-05T14:03:22+02:00", datetime(2024, 3, 5, 14, 3, 22, tzinfo=east(2))),
        ("2024-01-06T08:00:00Z", datetime(2024, 1, 6, 8, 0, 0, tzinfo=UTC)),
        ("2024-01-06T08:00:00+00:00", datetime(2024, 1, 6, 8, 0, 0, tzinfo=UTC)),
        ("2024-01-02T23:30:00-03:00", datetime(2024, 1, 2, 23, 30, tzinfo=east(-3))),
        ("1999-12-31T23:59:59+14:00", datetime(1999, 12, 31, 23, 59, 59, tzinfo=east(14))),
        ("2020-02-29T00:00:00+05:30", datetime(2020, 2, 29, tzinfo=east(5.5))),
    ],
)
def test_parse_git_timestamp_accepts_strict_iso(text: str, expected: datetime) -> None:
    parsed = parse_git_timestamp(text)
    assert parsed == expected
    assert parsed is not None
    assert parsed.utcoffset() == expected.utcoffset()  # the author's offset is preserved


@pytest.mark.parametrize(
    "text",
    [
        "",
        "garbage",
        "2024-13-01T00:00:00Z",
        "2024-02-30T00:00:00Z",
        "2023-02-29T00:00:00Z",
        "2024-01-01 00:00:00 +0000",
        "2024-01-01T24:00:00Z",
        "2024-01-01T00:00:60Z",
        "0000-01-01T00:00:00Z",
        "0001-01-01T00:00:00+14:00",  # does not fit into UTC
        "9999-12-31T23:59:59-14:00",  # does not fit into UTC
        "2024-01-01T00:00:00+25:00",
        "99999-01-01T00:00:00Z",
        "2024-01-01T00:00:00Z\n",
        "2024-01-01T00:00:00",
        "\N{FULLWIDTH DIGIT TWO}024-01-01T00:00:00Z",
    ],
)
def test_parse_git_timestamp_rejects_malformed_values(text: str) -> None:
    assert parse_git_timestamp(text) is None


def test_iso_utc_normalises_offsets_and_zero_pads_old_years() -> None:
    assert iso_utc(datetime(2024, 1, 2, 23, 30, tzinfo=east(-3))) == "2024-01-03T02:30:00Z"
    assert iso_utc(datetime(2024, 1, 1, 0, 0, 0, 999999, tzinfo=UTC)) == "2024-01-01T00:00:00Z"
    assert iso_utc(datetime(999, 1, 1, tzinfo=UTC)) == "0999-01-01T00:00:00Z"


@pytest.mark.parametrize(
    ("text", "end_of_day", "expected"),
    [
        ("2024-03-05", False, datetime(2024, 3, 5, tzinfo=UTC)),
        ("2024-03-05", True, datetime(2024, 3, 5, 23, 59, 59, tzinfo=UTC)),
        ("2024-03-05T10:30", False, datetime(2024, 3, 5, 10, 30, tzinfo=UTC)),
        ("2024-03-05 10:30:15", True, datetime(2024, 3, 5, 10, 30, 15, tzinfo=UTC)),
        ("2024-03-05T10:30:00Z", False, datetime(2024, 3, 5, 10, 30, tzinfo=UTC)),
        ("2024-03-05T10:30:00-03:00", False, datetime(2024, 3, 5, 13, 30, tzinfo=UTC)),
        ("2024-03-05T10:30:00+0530", False, datetime(2024, 3, 5, 5, 0, tzinfo=UTC)),
        ("90d", False, NOW - timedelta(days=90)),
        ("12w", False, NOW - timedelta(weeks=12)),
        ("2 weeks ago", False, NOW - timedelta(weeks=2)),
        ("1 day", False, NOW - timedelta(days=1)),
        ("6m", False, datetime(2026, 5, 10, 12, 0, tzinfo=UTC)),
        ("3 months ago", False, datetime(2026, 8, 10, 12, 0, tzinfo=UTC)),
        ("1y", False, datetime(2025, 11, 10, 12, 0, tzinfo=UTC)),
        ("2 YEARS", False, datetime(2024, 11, 10, 12, 0, tzinfo=UTC)),
        ("  30d  ", False, NOW - timedelta(days=30)),
    ],
)
def test_parse_when_accepts_dates_timestamps_and_relative_values(
    text: str, end_of_day: bool, expected: datetime
) -> None:
    assert parse_when(text, now=NOW, end_of_day=end_of_day) == expected


def test_parse_when_clamps_the_day_of_month_when_subtracting_months() -> None:
    assert parse_when("1m", now=datetime(2026, 3, 31, tzinfo=UTC)) == datetime(
        2026, 2, 28, tzinfo=UTC
    )
    assert parse_when("12m", now=datetime(2024, 2, 29, tzinfo=UTC)) == datetime(
        2023, 2, 28, tzinfo=UTC
    )


@pytest.mark.parametrize(
    "text",
    ["banana", "", "2024-02-30", "2024-13-01", "-5d", "5x", "d90", "99999d", "last week", "2024"],
)
def test_parse_when_rejects_what_git_would_silently_misread(text: str) -> None:
    with pytest.raises(ConfigError, match=r"date|understand"):
        parse_when(text, now=NOW)


def test_parse_when_reports_out_of_range_relative_values() -> None:
    with pytest.raises(ConfigError, match="out of range"):
        parse_when("9999y", now=NOW)


def test_utc_now_honours_source_date_epoch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1794312000")
    assert utc_now() == NOW
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "")
    assert utc_now().tzinfo is not None


@pytest.mark.parametrize("value", ["abc", "-5", "1.5", "99999999999999999999"])
def test_utc_now_rejects_a_bad_epoch(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("SOURCE_DATE_EPOCH", value)
    with pytest.raises(ConfigError, match="SOURCE_DATE_EPOCH"):
        utc_now()


def test_sanitize_text_defuses_terminal_escape_sequences() -> None:
    cleaned = sanitize_text("\x1b[31mred\x1b[0m \x1b]0;pwned\x07 \x9b2J")
    assert "\x1b" not in cleaned
    assert "\x07" not in cleaned
    assert "\x9b" not in cleaned
    assert "red" in cleaned


def test_sanitize_text_makes_controls_visible_and_strips_bidi_overrides() -> None:
    assert (
        sanitize_text("a\tb\nc")
        == "a\N{SYMBOL FOR HORIZONTAL TABULATION}b\N{SYMBOL FOR LINE FEED}c"
    )
    assert sanitize_text("x\N{NULL}y") == "x\N{SYMBOL FOR NULL}y"
    cleaned = sanitize_text("safe\N{RIGHT-TO-LEFT OVERRIDE}evil\N{POP DIRECTIONAL FORMATTING}")
    assert "\N{RIGHT-TO-LEFT OVERRIDE}" not in cleaned
    assert "\N{POP DIRECTIONAL FORMATTING}" not in cleaned
    assert sanitize_text("a\N{LINE SEPARATOR}b") == "a b"
    assert sanitize_text("\N{ZERO WIDTH NO-BREAK SPACE}name") == "name"


def test_sanitize_text_keeps_ordinary_unicode_and_trims() -> None:
    assert sanitize_text("  Zoë Müller 李雷  ") == "Zoë Müller 李雷"
    assert sanitize_text("  keep  ", strip=False) == "  keep  "
    assert sanitize_text("abcdef", 4) == f"abc{ELLIPSIS}"


@pytest.mark.parametrize(
    ("text", "limit", "expected"), [("abc", 3, "abc"), ("abc", 0, ""), ("abcd", 1, ELLIPSIS)]
)
def test_truncate_edges(text: str, limit: int, expected: str) -> None:
    assert truncate(text, limit) == expected


def test_ratio_handles_zero_and_rounds() -> None:
    assert ratio(1, 0) == 0.0
    assert ratio(1, 3) == 0.3333
    assert ratio(2, 3, digits=2) == 0.67


def test_to_json_is_utf8_friendly_and_rejects_nan() -> None:
    assert to_json({"a": "é"}, indent=None) == '{"a":"é"}'
    assert to_json({"a": 1}).startswith('{\n  "a"')
    with pytest.raises(ValueError, match="Out of range"):
        to_json({"a": float("nan")})


def test_json_for_html_cannot_close_a_script_element() -> None:
    payload = {
        "evil": "</script><script>alert(1)</script><!-- & \N{LINE SEPARATOR}\N{PARAGRAPH SEPARATOR}"
    }
    text = json_for_html(payload)
    for dangerous in ("<", ">", "&", "\N{LINE SEPARATOR}", "\N{PARAGRAPH SEPARATOR}"):
        assert dangerous not in text
    assert json.loads(text) == payload
