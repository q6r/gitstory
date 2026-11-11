"""Property-style tests: seeded random input for the parsers, sanitisers and renderers."""

from __future__ import annotations

import json
import random
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import pytest
from builders import commit, history, when

from gitstory.metrics import compute_metrics
from gitstory.model import UTC, ConfigError, FileChange
from gitstory.render.html import render_report
from gitstory.render.svg_card import CardOptions, render_card
from gitstory.render.terminal import TermStyle, render_summary
from gitstory.util import (
    iso_utc,
    json_for_html,
    parse_git_timestamp,
    parse_when,
    ratio,
    sanitize_text,
    truncate,
)

SEEDS = range(8)
NOW = when("2024-06-30T12:00:00+00:00")
CONTROL_OR_BIDI = re.compile(
    "[\x00-\x1f\x7f-\x9f"
    "\N{LEFT-TO-RIGHT EMBEDDING}-\N{RIGHT-TO-LEFT OVERRIDE}"
    "\N{LEFT-TO-RIGHT ISOLATE}-\N{POP DIRECTIONAL ISOLATE}"
    "\N{LINE SEPARATOR}\N{PARAGRAPH SEPARATOR}\N{ZERO WIDTH NO-BREAK SPACE}]"
)
# the control characters and noncharacters an XML 1.0 document may not contain
XML_FORBIDDEN = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f" + chr(0xFFFE) + chr(0xFFFF) + "]")


def random_text(rng: random.Random, length: int) -> str:
    """Text drawn from every corner of Unicode (never lone surrogates, which cannot be stored)."""
    pools = (
        (0x20, 0x7E),  # printable ASCII
        (0x00, 0x1F),  # controls, ESC among them
        (0x7F, 0x9F),  # DEL and the C1 controls
        (0xA0, 0x24FF),  # Latin, Greek, Cyrillic, symbols
        (0x2000, 0x206F),  # general punctuation: separators, bidi marks, joiners
        (0x3000, 0x9FFF),  # CJK
        (0xE000, 0xFFFD),  # private use, specials
        (0x1F300, 0x1FAFF),  # emoji
    )
    out = []
    for _ in range(length):
        low, high = rng.choice(pools)
        out.append(chr(rng.randint(low, high)))
    return "".join(out)


HOSTILE_FRAGMENTS = (
    "</script>",
    "<!--",
    "]]>",
    "<img src=x onerror=alert(1)>",
    "&amp;",
    '"',
    "'",
    "\\",
    "\x1b[31m",
    "\x1b]0;x\x07",
    "{{data}}",
    "\N{RIGHT-TO-LEFT OVERRIDE}",
    "\N{LINE SEPARATOR}",
    "%s%d{0}",
)


def nasty_text(rng: random.Random) -> str:
    pieces = [random_text(rng, rng.randint(0, 12)), rng.choice(HOSTILE_FRAGMENTS)]
    rng.shuffle(pieces)
    return "".join([*pieces, rng.choice(HOSTILE_FRAGMENTS)])


# ------------------------------------------------------------------------- timestamps


@pytest.mark.parametrize("seed", SEEDS)
def test_every_valid_git_timestamp_round_trips(seed: int) -> None:
    rng = random.Random(seed)
    for _ in range(200):
        offset = timedelta(minutes=rng.randint(-23 * 60 - 59, 23 * 60 + 59))
        moment = datetime(
            rng.randint(1971, 2037), rng.randint(1, 12), rng.randint(1, 28),
            rng.randint(0, 23), rng.randint(0, 59), rng.randint(0, 59),
            tzinfo=timezone(offset),
        )  # fmt: skip
        parsed = parse_git_timestamp(moment.isoformat())
        assert parsed == moment
        assert parsed is not None
        assert parsed.utcoffset() == offset
        assert parse_git_timestamp(iso_utc(moment)) == moment.astimezone(UTC)
        assert iso_utc(moment).endswith("Z")


@pytest.mark.parametrize("seed", SEEDS)
def test_random_strings_never_make_the_timestamp_parser_raise(seed: int) -> None:
    rng = random.Random(seed)
    alphabet = "0123456789-:TZ+. xX\N{ARABIC-INDIC DIGIT ONE}"
    for _ in range(400):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 30)))
        result = parse_git_timestamp(text)
        assert result is None or isinstance(result, datetime)
    # near misses of a valid timestamp are all rejected, never guessed at
    good = "2024-03-05T10:20:30+01:00"
    assert parse_git_timestamp(good) is not None
    for _ in range(300):
        chars = list(good)
        chars[rng.randrange(len(chars))] = rng.choice("xZ9:-+ ")
        if "".join(chars) != good:
            result = parse_git_timestamp("".join(chars))
            assert result is None or result.tzinfo is not None


@pytest.mark.parametrize("seed", SEEDS)
def test_random_filter_dates_are_parsed_or_refused_cleanly(seed: int) -> None:
    rng = random.Random(seed)
    alphabet = "0123456789-:T Zdwmyago+"
    for _ in range(400):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 20)))
        try:
            result = parse_when(text, now=NOW)
        except ConfigError:
            continue
        assert result.tzinfo is not None
        assert result.utcoffset() == timedelta(0)  # always UTC, whatever the input zone


@pytest.mark.parametrize("seed", SEEDS)
def test_relative_dates_are_monotonic_and_absolute_dates_are_exact(seed: int) -> None:
    rng = random.Random(seed)
    for unit in "dwmy":
        previous = NOW
        for amount in sorted(rng.sample(range(1, 400), 12)):
            moment = parse_when(f"{amount}{unit}", now=NOW)
            assert moment < previous or moment == previous
            previous = moment
    for _ in range(100):
        year, month, day = rng.randint(1971, 2037), rng.randint(1, 12), rng.randint(1, 28)
        start = parse_when(f"{year:04d}-{month:02d}-{day:02d}", now=NOW)
        end = parse_when(f"{year:04d}-{month:02d}-{day:02d}", now=NOW, end_of_day=True)
        assert start == datetime(year, month, day, tzinfo=UTC)
        assert end - start == timedelta(hours=23, minutes=59, seconds=59)


# --------------------------------------------------------------------------- sanitising


@pytest.mark.parametrize("seed", SEEDS)
def test_sanitised_text_is_safe_idempotent_and_bounded(seed: int) -> None:
    rng = random.Random(seed)
    for _ in range(200):
        text = random_text(rng, rng.randint(0, 60))
        cleaned = sanitize_text(text)
        assert not CONTROL_OR_BIDI.search(cleaned)
        assert sanitize_text(cleaned) == cleaned
        limit = rng.randint(1, 30)
        short = sanitize_text(text, limit)
        assert len(short) <= limit
        assert not CONTROL_OR_BIDI.search(short)
        if len(cleaned) <= limit:
            assert short == cleaned


@pytest.mark.parametrize("seed", SEEDS)
def test_truncation_never_exceeds_the_limit_and_keeps_a_prefix(seed: int) -> None:
    rng = random.Random(seed)
    for _ in range(200):
        text = random_text(rng, rng.randint(0, 80)) or "x"
        limit = rng.randint(0, 40)
        shortened = truncate(text, limit)
        assert len(shortened) <= max(limit, 0)
        if len(text) > limit > 0:
            assert shortened.endswith("\N{HORIZONTAL ELLIPSIS}")
            assert (
                text.startswith(shortened[:-1].rstrip()[:1])
                or shortened == "\N{HORIZONTAL ELLIPSIS}"
            )
        elif limit > 0:
            assert shortened == text


@pytest.mark.parametrize("seed", SEEDS)
def test_json_for_html_round_trips_any_text_and_never_leaks_markup(seed: int) -> None:
    rng = random.Random(seed)
    for _ in range(200):
        payload = {
            nasty_text(rng): [nasty_text(rng), {"deep": nasty_text(rng)}],
            "n": rng.randint(-5, 5),
        }
        text = json_for_html(payload)
        for dangerous in ("<", ">", "&", "\N{LINE SEPARATOR}", "\N{PARAGRAPH SEPARATOR}"):
            assert dangerous not in text
        assert json.loads(text) == payload


@pytest.mark.parametrize("seed", SEEDS)
def test_ratio_stays_between_zero_and_one(seed: int) -> None:
    rng = random.Random(seed)
    for _ in range(300):
        whole = rng.randint(0, 10_000)
        part = rng.randint(0, whole)
        value = ratio(part, whole)
        assert 0.0 <= value <= 1.0
        if whole:
            assert abs(value - part / whole) < 0.00006
        else:
            assert value == 0.0


# --------------------------------------------------------------------------- renderers


def hostile_document(rng: random.Random) -> dict[str, object]:
    commits = []
    for day in range(1, rng.randint(2, 28)):
        author = nasty_text(rng)
        commits.append(
            commit(
                f"2024-06-{day:02d}T{rng.randint(0, 23):02d}:00:00+00:00",
                author=author,
                email=f"{day % 4}@example.com",
                subject=nasty_text(rng),
                files=[FileChange(nasty_text(rng) or "f", rng.randint(0, 50), rng.randint(0, 50))],
            )
        )
    return compute_metrics(history(commits, name=nasty_text(rng) or "repo"), now=NOW)


@pytest.mark.parametrize("seed", SEEDS)
def test_whatever_a_repository_contains_the_outputs_stay_valid_and_inert(seed: int) -> None:
    rng = random.Random(seed)
    for _ in range(5):
        document = hostile_document(rng)

        card = render_card(document, CardOptions(theme=rng.choice(["dark", "light", "auto"])))
        root = ET.fromstring(card)  # well-formed XML
        assert not any(el.tag.endswith(("script", "image", "foreignObject")) for el in root.iter())
        assert not XML_FORBIDDEN.search(card)

        page = render_report(document)
        island = page.split('id="report-data" type="application/json">', 1)[1].split("</script>")[0]
        assert json.loads(island) == json.loads(json.dumps(document))
        assert page.count("<script") == page.count("</script>") == 4

        text = render_summary(document, TermStyle(color=False, unicode=True, width=100))
        assert "\x1b" not in text
        assert "\x07" not in text
