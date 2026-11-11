"""The command line: every command, exit codes, bad input and the error paths."""

from __future__ import annotations

import io
import json
import os
import runpy
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest
from make_repo import NOW, SyntheticRepo, run_git

from gitstory import __version__, cli
from gitstory.cli import ExitCode, build_parser, exit_code_for, main
from gitstory.collect import (
    EmptyRepositoryError,
    GitCommandError,
    GitNotFoundError,
    GitTimeoutError,
    NotARepositoryError,
    RevisionNotFoundError,
)
from gitstory.model import ConfigError, GitstoryError

EPOCH = str(int(NOW.timestamp()))


@pytest.fixture(autouse=True)
def _fixed_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every command in this module sees the same "now" as the synthetic repository."""
    monkeypatch.setenv("SOURCE_DATE_EPOCH", EPOCH)


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# ------------------------------------------------------------------------------ commands


def test_json_prints_the_metrics_document(
    synthetic: SyntheticRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = run(capsys, "json", str(synthetic.path))
    assert code == ExitCode.OK == 0
    assert err == ""
    document = json.loads(out)
    assert document["schema"] == "gitstory.metrics/v1"
    assert document["totals"]["commits"] == len(synthetic.commits)
    assert document["generatedAt"] == "2026-11-10T12:00:00Z"
    assert out.startswith('{\n  "schema"')
    assert out.endswith("}\n")


def test_json_can_be_compact_or_written_to_a_file(
    stub: Path, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _, compact, _ = run(capsys, "json", str(stub), "--compact")
    assert compact.count("\n") == 1
    assert json.loads(compact)["schema"] == "gitstory.metrics/v1"
    target = tmp_path / "metrics.json"
    code, out, err = run(capsys, "json", str(stub), "-o", str(target))
    assert (code, out) == (0, "")
    assert f"wrote {target}" in err
    assert json.loads(target.read_text(encoding="utf-8")) == json.loads(compact)
    assert not target.read_bytes().startswith(b"\xef\xbb\xbf")
    assert b"\r\n" not in target.read_bytes()


def test_report_writes_one_self_contained_html_file(
    stub: Path, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    target = tmp_path / "story.html"
    code, out, err = run(capsys, "report", str(stub), "-o", str(target))
    assert (code, out) == (0, "")
    assert "gitstory: wrote" in err
    page = target.read_text(encoding="utf-8")
    assert page.startswith("<!doctype html>")
    assert 'id="report-data"' in page
    assert 'data-theme="auto"' in page
    assert 'data-live="false"' in page  # a file has no server behind it
    assert "<link" not in page


def test_report_theme_and_stdout(stub: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(capsys, "report", str(stub), "-o", "-", "--theme", "light")
    assert code == 0
    assert 'data-theme="light"' in out
    assert out.startswith("<!doctype html>")


def test_card_writes_an_svg_with_the_requested_geometry(
    stub: Path, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    target = tmp_path / "card.svg"
    code, _, _ = run(
        capsys,
        "card", str(stub), "-o", str(target),
        "--theme", "dark", "--width", "600", "--height", "240", "--weeks", "30",
        "--title", "My <b>repo</b>",
    )  # fmt: skip
    assert code == 0
    root = ET.fromstring(target.read_text(encoding="utf-8"))
    assert root.get("width") == "600"
    assert root.get("height") == "240"
    assert "My &lt;b&gt;repo&lt;/b&gt;" in target.read_text(encoding="utf-8")


def test_card_options_are_validated(stub: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = run(capsys, "card", str(stub), "--width", "10")
    assert code == ExitCode.USAGE
    assert out == ""
    assert "card width must be between" in err


def test_default_output_names_land_in_the_current_directory(
    stub: Path, capsys: pytest.CaptureFixture[str], workdir: Path
) -> None:
    assert main(["report", str(stub)]) == 0
    assert main(["card", str(stub)]) == 0
    capsys.readouterr()
    assert (workdir / "gitstory.html").is_file()
    assert (workdir / "card.svg").is_file()


def test_summary_is_plain_text_when_piped(stub: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(capsys, "summary", str(stub))
    assert code == 0
    assert "\x1b" not in out  # not a terminal: no colour
    assert out.startswith("gitstory \N{MIDDLE DOT} demo")
    assert "Top authors" in out


def test_summary_colour_can_be_forced_and_forbidden(
    stub: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, forced, _ = run(capsys, "summary", str(stub), "--color", "always")
    _, never, _ = run(capsys, "summary", str(stub), "--color", "never")
    assert "\x1b[" in forced
    assert "\x1b" not in never


def test_a_run_is_reproducible_with_source_date_epoch(stub: Path, tmp_path: Path) -> None:
    first, second = tmp_path / "a.html", tmp_path / "b.html"
    assert main(["report", str(stub), "-o", str(first)]) == 0
    assert main(["report", str(stub), "-o", str(second)]) == 0
    assert first.read_bytes() == second.read_bytes()


def test_the_history_filters_reach_the_metrics(
    synthetic: SyntheticRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = run(
        capsys,
        "json", str(synthetic.path),
        "--since", "2024-01-01", "--until", "2024-12-31", "--author", "ada",
        "--path", "src", "--no-merges", "--max-commits", "20", "--branch", "main",
    )  # fmt: skip
    assert code == 0
    document = json.loads(out)
    assert document["filters"] == {
        "since": "2024-01-01T00:00:00Z",
        "until": "2024-12-31T23:59:59Z",
        "author": "ada",
        "branch": "main",
        "paths": ["src"],
        "noMerges": True,
        "maxCommits": 20,
    }
    assert 0 < document["totals"]["commits"] <= 20
    assert document["totals"]["mergeCommits"] == 0


def test_a_filter_that_matches_nothing_is_a_warning_not_an_error(
    tiny: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = run(capsys, "json", str(tiny), "--author", "nobody-at-all")
    assert code == 0
    assert json.loads(out)["totals"]["commits"] == 0
    assert "gitstory: warning: No commits matched the given filters." in err


def test_a_relative_since_is_measured_from_the_fixed_clock(
    stub: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, out, _ = run(capsys, "json", str(stub), "--since", "30d")
    assert json.loads(out)["filters"]["since"] == "2026-10-11T12:00:00Z"


# --------------------------------------------------------------------------- exit codes


def test_a_directory_that_is_not_a_repository_exits_4(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code, out, err = run(capsys, "json", str(tmp_path))
    assert code == ExitCode.NOT_FOUND == 4
    assert out == ""
    assert err.startswith("gitstory: error: ")
    assert "is not inside a git repository" in err


@pytest.mark.parametrize("name", ["missing", "a file"])
def test_paths_that_do_not_exist_or_are_files_exit_4(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, name: str
) -> None:
    path = tmp_path / name
    if name == "a file":
        path.write_text("x", encoding="utf-8")
    code, _, err = run(capsys, "summary", str(path))
    assert code == 4
    assert "gitstory: error:" in err


def test_a_repository_without_commits_exits_5(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    run_git(tmp_path, "init", "-q", "-b", "main")
    code, _, err = run(capsys, "json", str(tmp_path))
    assert code == ExitCode.NO_COMMITS == 5
    assert "no commits" in err.lower()


def test_an_unknown_branch_exits_4(tiny: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = run(capsys, "json", str(tiny), "--branch", "no-such-branch")
    assert code == 4
    assert "no-such-branch" in err


@pytest.mark.parametrize(
    "extra",
    [
        ["--since", "not-a-date"],
        ["--since", "2024-13-45"],
        ["--until", "yesterday-ish"],
        ["--since", "2025-01-01", "--until", "2024-01-01"],
        ["--branch=--upload-pack=evil"],
        ["--branch", "main..other"],
    ],
)
def test_invalid_values_exit_2_before_any_work_is_done(
    tiny: Path, capsys: pytest.CaptureFixture[str], extra: list[str]
) -> None:
    code, out, err = run(capsys, "json", str(tiny), *extra)
    assert code in (ExitCode.USAGE, ExitCode.NOT_FOUND) or code == 2
    assert out == ""
    assert "gitstory: error:" in err


def test_git_missing_from_the_path_exits_3(
    tiny: Path,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    code, _, err = run(capsys, "json", str(tiny))
    assert code == ExitCode.GIT_MISSING == 3
    assert "git was not found" in err


def test_git_failing_exits_6(
    tiny: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise GitCommandError("git exited with status 128: fatal: bad object")

    monkeypatch.setattr(cli, "collect_history", fail)
    code, _, err = run(capsys, "json", str(tiny))
    assert code == ExitCode.GIT_FAILED == 6
    assert "bad object" in err


@pytest.mark.parametrize("command", ["json", "report", "card"])
def test_an_output_that_cannot_be_written_exits_7(
    stub: Path,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    command: str,
) -> None:
    code, out, err = run(capsys, command, str(stub), "-o", str(tmp_path))  # a directory
    assert code == ExitCode.IO_ERROR == 7
    assert out == ""
    assert "gitstory: error:" in err
    code, _, _ = run(capsys, command, str(stub), "-o", str(tmp_path / "no" / "dir" / "x"))
    assert code == 7


def test_nothing_is_written_when_the_analysis_fails(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    target = tmp_path / "out.html"
    code, _, _ = run(capsys, "report", str(tmp_path), "-o", str(target))
    assert code == 4
    assert not target.exists()


def test_an_unexpected_failure_exits_1_and_says_how_to_get_a_traceback(
    stub: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(cli, "compute_metrics", boom)
    code, _, err = run(capsys, "json", str(stub))
    assert code == ExitCode.ERROR == 1
    assert "unexpected error: RuntimeError: boom (run with --debug for a traceback)" in err
    assert "Traceback" not in err
    code, _, err = run(capsys, "--debug", "json", str(stub))
    assert code == 1
    assert "Traceback (most recent call last)" in err


def test_ctrl_c_exits_130(
    tiny: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def interrupt(*_args: object, **_kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "collect_history", interrupt)
    code, _, err = run(capsys, "summary", str(tiny))
    assert code == ExitCode.INTERRUPTED == 130
    assert "interrupted" in err


def test_warnings_from_the_analysis_go_to_stderr_never_to_the_output(
    stub: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    real = cli.compute_metrics

    def with_warning(*args: Any, **kwargs: Any) -> dict[str, Any]:
        document = real(*args, **kwargs)
        document["warnings"] = ["the clone is shallow"]
        return document

    monkeypatch.setattr(cli, "compute_metrics", with_warning)
    code, out, err = run(capsys, "json", str(stub))
    assert code == 0
    assert "gitstory: warning: the clone is shallow" in err
    assert "shallow" in out  # it is part of the document, but the stream stays valid JSON
    json.loads(out)


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (ConfigError("x"), ExitCode.USAGE),
        (GitNotFoundError("x"), ExitCode.GIT_MISSING),
        (NotARepositoryError("x"), ExitCode.NOT_FOUND),
        (RevisionNotFoundError("x"), ExitCode.NOT_FOUND),
        (EmptyRepositoryError("x"), ExitCode.NO_COMMITS),
        (GitCommandError("x"), ExitCode.GIT_FAILED),
        (GitTimeoutError("x"), ExitCode.GIT_FAILED),
        (PermissionError("x"), ExitCode.IO_ERROR),
        (FileNotFoundError("x"), ExitCode.IO_ERROR),
        (GitstoryError("x"), ExitCode.ERROR),
        (ValueError("x"), ExitCode.ERROR),
    ],
)
def test_every_error_maps_to_its_documented_exit_code(error: BaseException, code: ExitCode) -> None:
    assert exit_code_for(error) == code


def test_the_exit_codes_are_the_documented_numbers() -> None:
    assert {code.name: int(code) for code in ExitCode} == {
        "OK": 0,
        "ERROR": 1,
        "USAGE": 2,
        "GIT_MISSING": 3,
        "NOT_FOUND": 4,
        "NO_COMMITS": 5,
        "GIT_FAILED": 6,
        "IO_ERROR": 7,
        "INTERRUPTED": 130,
    }


# -------------------------------------------------------------------- argument parsing


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["nope"],
        ["json", "--no-such-option"],
        ["json", "--max-commits", "0"],
        ["json", "--max-commits", "-3"],
        ["json", "--max-commits", "many"],
        ["serve", "--port", "70000"],
        ["serve", "--port", "-1"],
        ["serve", "--port", "http"],
        ["card", "--theme", "neon"],
        ["report", "--theme", "neon"],
        ["summary", "--color", "sometimes"],
        ["card", "--width", "wide"],
    ],
)
def test_bad_arguments_are_rejected_by_the_parser_with_status_2(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as stopped:
        main(argv)
    assert stopped.value.code == 2
    assert "usage: gitstory" in capsys.readouterr().err


def test_version_and_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as stopped:
        main(["--version"])
    assert stopped.value.code == 0
    assert capsys.readouterr().out == f"gitstory {__version__}\n"
    for command in ("report", "json", "card", "serve", "summary"):
        with pytest.raises(SystemExit) as stopped:
            main([command, "--help"])
        assert stopped.value.code == 0
        out = capsys.readouterr().out
        assert f"usage: gitstory {command}" in out
        assert "--since" in out
        assert "--max-commits" in out


def test_the_parser_offers_exactly_the_documented_commands() -> None:
    parser = build_parser()
    actions = [a for a in parser._actions if a.dest == "command"]
    assert sorted(actions[0].choices) == ["card", "json", "report", "serve", "summary"]


def test_serve_refuses_a_public_address_before_analysing_anything(
    synthetic: SyntheticRepo, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def must_not_run(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the history must not be read for a refused address")

    monkeypatch.setattr(cli, "collect_history", must_not_run)
    code, _, err = run(capsys, "serve", str(synthetic.path), "--host", "0.0.0.0")  # noqa: S104 - the refusal is the point
    assert code == ExitCode.USAGE
    assert "refusing to listen on '0.0.0.0'" in err
    assert "--allow-remote" in err


def test_serve_rejects_bad_filters_before_binding_a_port(
    stub: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = run(capsys, "serve", str(stub), "--since", "garbage", "--port", "0")
    assert code == 2
    assert "cannot understand date" in err


# ------------------------------------------------------------------- output encoding


def test_unencodable_names_are_replaced_instead_of_crashing_the_summary(
    synthetic: SyntheticRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = io.BytesIO()
    legacy = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", legacy)
    assert main(["summary", str(synthetic.path), "--color", "never"]) == 0
    legacy.flush()
    text = raw.getvalue().decode("cp1252")
    assert "Ada Lovelace" in text
    assert "?" in text  # the CJK author name


def test_json_to_a_stream_without_a_binary_buffer(
    stub: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sink = io.StringIO()
    monkeypatch.setattr(sys, "stdout", sink)
    assert main(["json", str(stub), "--compact"]) == 0
    assert json.loads(sink.getvalue())["schema"] == "gitstory.metrics/v1"


def test_the_dash_m_entry_point_exits_with_the_status_of_main(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["gitstory", "--version"])
    with pytest.raises(SystemExit) as stopped:
        runpy.run_module("gitstory.__main__", run_name="__main__")
    assert stopped.value.code == 0
    assert capsys.readouterr().out == f"gitstory {__version__}\n"


def test_encodable_handles_odd_streams() -> None:
    cjk = "\N{CJK UNIFIED IDEOGRAPH-674E}x"
    legacy = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    assert cli._encodable(cjk, legacy) == "?x"
    assert cli._encodable(cjk, io.StringIO()) == cjk  # no encoding attribute value: UTF-8
    assert (
        cli._encodable("caf\N{LATIN SMALL LETTER E WITH ACUTE}", object())
        == "caf\N{LATIN SMALL LETTER E WITH ACUTE}"
    )

    class Odd:
        encoding = "no-such-codec"

    assert cli._encodable("caf\N{LATIN SMALL LETTER E WITH ACUTE}", Odd()) == "caf?"


# ------------------------------------------------------------------------ real process


def gitstory(*argv: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[bytes]:
    merged = {**os.environ, "SOURCE_DATE_EPOCH": EPOCH, **(env or {})}
    return subprocess.run(
        [sys.executable, "-m", "gitstory", *argv],
        capture_output=True,
        env=merged,
        timeout=60,
        check=False,
    )


def test_python_dash_m_gitstory_works_as_a_process(synthetic: SyntheticRepo) -> None:
    version = gitstory("--version")
    assert version.returncode == 0
    assert version.stdout.decode().strip() == f"gitstory {__version__}"
    done = gitstory("json", str(synthetic.path), "--compact")
    assert done.returncode == 0
    assert json.loads(done.stdout)["totals"]["commits"] == len(synthetic.commits)
    failed = gitstory("json", str(synthetic.path.parent))
    assert failed.returncode == 4
    assert b"gitstory: error:" in failed.stderr


def test_a_legacy_console_encoding_does_not_break_the_summary(synthetic: SyntheticRepo) -> None:
    done = gitstory(
        "summary",
        str(synthetic.path),
        env={"PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"},
    )
    assert done.returncode == 0, done.stderr
    text = done.stdout.decode("cp1252")
    assert "Top authors" in text
    assert "\N{FULL BLOCK}" not in text  # the glyphs fell back to ASCII
