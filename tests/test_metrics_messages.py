"""Commit-message metrics: Conventional Commits types and word frequencies."""

from __future__ import annotations

import random
import time

import pytest
from builders import commit

from gitstory.metrics.messages import (
    CONVENTIONAL_TYPES,
    MAX_WORD_LENGTH,
    MIN_WORD_LENGTH,
    MessageAccumulator,
    parse_conventional,
    tokenize,
)
from gitstory.metrics.stopwords import STOPWORDS


def accumulate(*subjects: str, merges: int = 0) -> MessageAccumulator:
    acc = MessageAccumulator()
    for subject in subjects:
        acc.add(commit(subject=subject))
    for _ in range(merges):
        acc.add(commit(subject="Merge branch 'feature'", merge=True))
    return acc


# ------------------------------------------------------------------ parse_conventional


@pytest.mark.parametrize(
    ("subject", "expected"),
    [
        ("feat: add x", ("feat", False, "add x")),
        ("fix(parser): handle empty input", ("fix", False, "handle empty input")),
        ("feat!: drop python 3.8", ("feat", True, "drop python 3.8")),
        ("refactor(api)!: rename", ("refactor", True, "rename")),
        ("FEAT: shout", ("feat", False, "shout")),
        ("Fix(UI): caps", ("fix", False, "caps")),
        ("chore:   extra   spaces", ("chore", False, "extra   spaces")),
        (
            "wip: an unknown type is still a typed subject",
            ("wip", False, "an unknown type is still a typed subject"),
        ),
        ("fix(): empty scope", ("fix", False, "empty scope")),
        ("fix(a b/c-d): odd but fine scope", ("fix", False, "odd but fine scope")),
    ],
)
def test_conventional_subjects_are_parsed(subject: str, expected: tuple[str, bool, str]) -> None:
    assert parse_conventional(subject) == expected


@pytest.mark.parametrize(
    "subject",
    [
        "",
        "plain message",
        "feat:no space after the colon",
        "feat : space before the colon",
        "feat: ",  # nothing after the colon
        "feat:\t",
        ": leading colon",
        " feat: leading space",
        "http://example.com/x",
        "v2: digits are not letters",
        "fix(a(b)): nested parentheses",
        "fix(" + "s" * 65 + "): scope too long",
        "fix(line\nbreak): x",
        "\N{FULLWIDTH LATIN SMALL LETTER F}eat: look-alike letters",
        "Merge branch 'x': not a type",
    ],
)
def test_other_subjects_are_not_conventional(subject: str) -> None:
    assert parse_conventional(subject) is None


# ------------------------------------------------------------------------------ tokenize


def test_tokenize_lowercases_and_drops_stopwords() -> None:
    assert tokenize("Add the new Parser and fix parser") == [
        "add",
        "new",
        "parser",
        "fix",
        "parser",
    ]
    assert tokenize("The THE the And") == []


def test_tokenize_splits_on_digits_and_underscores_and_drops_short_tokens() -> None:
    assert tokenize("fix 2024 bug_report v2 go") == ["fix", "bug", "report"]


def test_tokenize_keeps_inner_apostrophes_in_both_spellings() -> None:
    assert tokenize("don't break maintainer's build") == ["break", "maintainer's", "build"]
    typographic = "maintainer\N{RIGHT SINGLE QUOTATION MARK}s"
    assert tokenize(typographic) == ["maintainer's"]


def test_tokenize_handles_non_english_text() -> None:
    assert tokenize("corrigir o bug da tela de relat\N{LATIN SMALL LETTER O WITH ACUTE}rio") == [
        "corrigir",
        "bug",
        "tela",
        "relat\N{LATIN SMALL LETTER O WITH ACUTE}rio",
    ]
    assert tokenize("Stra\N{LATIN SMALL LETTER SHARP S}e STRASSE") == ["strasse", "strasse"]
    # There is no word segmentation: a run of ideographs is one token, so a two-character
    # word falls under the minimum length and a longer phrase is kept whole.
    two = "\N{CJK UNIFIED IDEOGRAPH-674E}\N{CJK UNIFIED IDEOGRAPH-96F7}"
    three = (
        "\N{CJK UNIFIED IDEOGRAPH-65E5}\N{CJK UNIFIED IDEOGRAPH-672C}\N{CJK UNIFIED IDEOGRAPH-8A9E}"
    )
    assert tokenize(f"{two} wrote this") == ["wrote"]
    assert tokenize(f"{three} wrote this") == [three, "wrote"]


def test_tokenize_applies_the_length_bounds() -> None:
    longest = "x" * MAX_WORD_LENGTH
    assert tokenize(f"{longest} {longest}y ab abc") == [longest, "abc"]
    assert MIN_WORD_LENGTH == 3


def test_tokenize_is_linear_on_adversarial_input() -> None:
    started = time.perf_counter()
    assert tokenize("a'" * 100_000) == []  # one very long "word"
    assert tokenize("'" * 100_000 + "x") == []
    assert tokenize("ab " * 100_000) == []
    assert time.perf_counter() - started < 3


# ------------------------------------------------------------------------- accumulator


def test_the_type_mix_counts_known_other_and_untyped_subjects() -> None:
    acc = accumulate(
        "feat: one",
        "feat(ui): two",
        "feat!: three",
        "fix: four",
        "wip: five",
        "just a sentence",
        merges=2,
    )
    section = acc.conventional_section()
    assert section["considered"] == 6  # merges are not written by people
    assert section["typed"] == 5
    assert section["share"] == 0.8333
    assert section["breaking"] == 1
    assert [(t["type"], t["commits"]) for t in section["types"]] == [
        ("feat", 3),
        ("fix", 1),
        ("other", 1),
        ("untyped", 1),
    ]
    assert section["types"][0]["share"] == 0.5
    assert acc.merges == 2


def test_known_types_with_equal_counts_are_listed_alphabetically() -> None:
    acc = accumulate("test: a", "docs: b", "ci: c", "perf: d")
    assert [t["type"] for t in acc.conventional_section()["types"]] == [
        "ci",
        "docs",
        "perf",
        "test",
    ]


def test_every_documented_type_is_recognised() -> None:
    acc = accumulate(*[f"{kind}: something" for kind in CONVENTIONAL_TYPES])
    types = {t["type"] for t in acc.conventional_section()["types"]}
    assert types == set(CONVENTIONAL_TYPES)


def test_empty_sections_are_well_formed() -> None:
    acc = MessageAccumulator()
    assert acc.conventional_section() == {
        "considered": 0,
        "typed": 0,
        "share": 0.0,
        "breaking": 0,
        "types": [],
    }
    assert acc.words_section() == {"tokens": 0, "unique": 0, "top": []}
    only_merges = accumulate(merges=3)
    assert only_merges.conventional_section()["considered"] == 0


def test_a_repository_with_no_conventions_has_a_zero_share() -> None:
    section = accumulate("Update readme", "Fixed it").conventional_section()
    assert (section["typed"], section["share"]) == (0, 0.0)
    assert [t["type"] for t in section["types"]] == ["untyped"]


def test_the_type_prefix_is_not_counted_as_a_word() -> None:
    acc = accumulate("feat(parser): parser tokens", "fix: parser")
    words = acc.words_section()
    assert [(w["word"], w["count"]) for w in words["top"]] == [("parser", 2), ("tokens", 1)]
    assert words["tokens"] == 3
    assert words["unique"] == 2


def test_words_are_ranked_by_count_then_alphabetically_and_cut_at_top() -> None:
    acc = accumulate("zebra apple", "zebra mango", "apple cherry", "zebra")
    top = acc.words_section()["top"]
    assert [(w["word"], w["count"]) for w in top] == [
        ("zebra", 3),
        ("apple", 2),
        ("cherry", 1),
        ("mango", 1),
    ]
    assert len(acc.words_section(top=2)["top"]) == 2


def test_untyped_subjects_are_tokenised_whole() -> None:
    acc = accumulate("Refactor the parser: tidy things")
    assert [w["word"] for w in acc.words_section()["top"]] == [
        "parser",
        "refactor",
        "things",
        "tidy",
    ]


def test_merge_subjects_never_count_as_words() -> None:
    acc = accumulate(merges=5)
    assert acc.words_section()["top"] == []


# ------------------------------------------------------------------ property-style tests


def _word(rng: random.Random) -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyz"
    return "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 10)))


@pytest.mark.parametrize("seed", range(10))
def test_generated_conventional_subjects_round_trip(seed: int) -> None:
    rng = random.Random(seed)
    kinds = [*CONVENTIONAL_TYPES, "wip", "Hotfix", "RELEASE"]
    for _ in range(60):
        kind = rng.choice(kinds)
        scope = "".join(rng.choice("abc -_/.") for _ in range(rng.randint(1, 12)))
        use_scope, bang = rng.random() < 0.5, rng.random() < 0.3
        rest = " ".join([_word(rng) for _ in range(rng.randint(1, 5))])
        subject = f"{kind}{f'({scope})' if use_scope else ''}{'!' if bang else ''}: {rest}"
        assert parse_conventional(subject) == (kind.lower(), bang, rest)


@pytest.mark.parametrize("seed", range(10))
def test_tokenizer_output_always_respects_its_own_rules(seed: int) -> None:
    rng = random.Random(seed)
    pool = (
        "abcXYZ \t'\N{RIGHT SINGLE QUOTATION MARK}_-0123:;,.!?"
        "\N{LATIN SMALL LETTER E WITH ACUTE}\N{LATIN CAPITAL LETTER SHARP S}"
    )
    for _ in range(80):
        text = "".join(rng.choice(pool) for _ in range(rng.randint(0, 120)))
        for word in tokenize(text):
            assert MIN_WORD_LENGTH <= len(word) <= MAX_WORD_LENGTH
            assert word == word.casefold()
            assert word not in STOPWORDS
            assert not any(char.isdigit() or char == "_" for char in word)


def test_random_unicode_never_breaks_the_message_metrics() -> None:
    rng = random.Random(99)
    acc = MessageAccumulator()
    for _ in range(300):
        text = "".join(
            chr(rng.choice([rng.randint(0x20, 0x7E), rng.randint(0, 0x2FFF)]))
            for _ in range(rng.randint(0, 80))
        )
        acc.add(commit(subject=text))
    conventional, words = acc.conventional_section(), acc.words_section()
    assert conventional["considered"] == 300
    assert sum(t["commits"] for t in conventional["types"]) == 300
    assert words["tokens"] >= sum(w["count"] for w in words["top"])
