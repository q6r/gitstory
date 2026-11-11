"""Command line interface: ``gitstory report | json | card | serve | summary``.

Exit codes (also listed in ``docs/usage.md``):

====  ==========================================================================
0     success
1     unexpected failure (run with ``--debug`` for a traceback)
2     invalid usage: unknown option, bad value, bad date
3     ``git`` was not found on PATH
4     not a git repository, or the requested branch / revision does not exist
5     the repository has no commits yet
6     git failed or timed out while reading the history
7     the output could not be written
130   interrupted (Ctrl+C)
====  ==========================================================================
"""

from __future__ import annotations

import argparse
import sys
import traceback
import webbrowser
from collections.abc import Callable, Sequence
from enum import IntEnum
from pathlib import Path
from typing import Any

from gitstory import __version__
from gitstory.collect import (
    EmptyRepositoryError,
    GitCommandError,
    GitNotFoundError,
    NotARepositoryError,
    RevisionNotFoundError,
    collect_history,
)
from gitstory.metrics import compute_metrics
from gitstory.model import ConfigError, Filters, GitstoryError
from gitstory.render.html import THEMES as REPORT_THEMES
from gitstory.render.html import render_report
from gitstory.render.svg_card import (
    DEFAULT_HEIGHT,
    DEFAULT_WEEKS,
    DEFAULT_WIDTH,
    CardOptions,
    render_card,
)
from gitstory.render.svg_card import THEMES as CARD_THEMES
from gitstory.render.terminal import (
    COLOR_MODES,
    detect_style,
    enable_windows_vt,
    render_summary,
)
from gitstory.serve import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    MetricsService,
    check_bind_address,
    make_server,
)
from gitstory.util import WHEN_HELP, parse_when, to_json, utc_now


class ExitCode(IntEnum):
    """Process exit codes."""

    OK = 0
    ERROR = 1
    USAGE = 2
    GIT_MISSING = 3
    NOT_FOUND = 4
    NO_COMMITS = 5
    GIT_FAILED = 6
    IO_ERROR = 7
    INTERRUPTED = 130


def exit_code_for(error: BaseException) -> ExitCode:
    """Map an error raised by gitstory to its exit code."""
    if isinstance(error, ConfigError):
        return ExitCode.USAGE
    if isinstance(error, GitNotFoundError):
        return ExitCode.GIT_MISSING
    if isinstance(error, NotARepositoryError | RevisionNotFoundError):
        return ExitCode.NOT_FOUND
    if isinstance(error, EmptyRepositoryError):
        return ExitCode.NO_COMMITS
    if isinstance(error, GitCommandError):
        return ExitCode.GIT_FAILED
    if isinstance(error, OSError):
        return ExitCode.IO_ERROR
    return ExitCode.ERROR


# ------------------------------------------------------------------------------- arguments


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a whole number") from None
    if value < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return value


def _port(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a port number") from None
    if not 0 <= value <= 65535:
        raise argparse.ArgumentTypeError("must be between 0 and 65535")
    return value


def _filter_options() -> argparse.ArgumentParser:
    parent = argparse.ArgumentParser(add_help=False)
    group = parent.add_argument_group("history filters")
    group.add_argument("--since", metavar="DATE", help=f"only commits from DATE on ({WHEN_HELP})")
    group.add_argument("--until", metavar="DATE", help="only commits up to DATE (same formats)")
    group.add_argument(
        "--author",
        metavar="REGEX",
        help="only commits whose author name or e-mail matches REGEX (case-insensitive)",
    )
    group.add_argument(
        "--branch", metavar="REV", help="branch, tag or commit to analyse (default: HEAD)"
    )
    group.add_argument(
        "--path",
        dest="paths",
        action="append",
        default=[],
        metavar="PATHSPEC",
        help="only commits touching PATHSPEC, relative to the repository root (repeatable)",
    )
    group.add_argument("--no-merges", action="store_true", help="leave merge commits out")
    group.add_argument(
        "--max-commits",
        type=_positive_int,
        metavar="N",
        help="analyse at most the N most recent matching commits",
    )
    return parent


def _path_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="any directory inside the repository (default: the current directory)",
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser (exposed for tests and for the docs)."""
    parser = argparse.ArgumentParser(
        prog="gitstory",
        description="Your repository's story, animated: an HTML dashboard, JSON metrics and an "
        "animated SVG card from any git repository.",
        epilog="Run 'gitstory <command> --help' for the options of one command.",
    )
    parser.add_argument("--version", action="version", version=f"gitstory {__version__}")
    parser.add_argument(
        "--debug", action="store_true", help="show a traceback on unexpected errors"
    )
    commands = parser.add_subparsers(dest="command", metavar="<command>", required=True)
    filters = _filter_options()

    report = commands.add_parser(
        "report",
        parents=[filters],
        help="write a self-contained HTML dashboard",
        description="Write one self-contained HTML file (no network needed to view it).",
    )
    _path_argument(report)
    report.add_argument(
        "-o", "--output", default="gitstory.html", help="file to write ('-' for stdout)"
    )
    report.add_argument("--theme", choices=REPORT_THEMES, default="auto", help="colour theme")
    report.set_defaults(handler=_cmd_report)

    as_json = commands.add_parser(
        "json",
        parents=[filters],
        help="print machine-readable metrics (schema gitstory.metrics/v1)",
        description="Print the metrics as JSON; docs/metrics.md describes every field.",
    )
    _path_argument(as_json)
    as_json.add_argument("-o", "--output", default="-", help="file to write (default: stdout)")
    as_json.add_argument("--compact", action="store_true", help="no indentation")
    as_json.set_defaults(handler=_cmd_json)

    card = commands.add_parser(
        "card",
        parents=[filters],
        help="write an animated SVG stats card for a README",
        description="Write an animated SVG card (pure SVG + CSS, safe for GitHub README images).",
    )
    _path_argument(card)
    card.add_argument("-o", "--output", default="card.svg", help="file to write ('-' for stdout)")
    card.add_argument(
        "--theme",
        choices=CARD_THEMES,
        default="auto",
        help="dark, light, or follow the viewer's setting",
    )
    card.add_argument("--width", type=int, default=DEFAULT_WIDTH, help="card width in pixels")
    card.add_argument("--height", type=int, default=DEFAULT_HEIGHT, help="card height in pixels")
    card.add_argument("--weeks", type=int, default=DEFAULT_WEEKS, help="weeks shown in the heatmap")
    card.add_argument("--title", help="title to show instead of the repository name")
    card.set_defaults(handler=_cmd_card)

    serve = commands.add_parser(
        "serve",
        parents=[filters],
        help="serve the dashboard on a local web server",
        description="Serve the dashboard and the metrics API on 127.0.0.1 (loopback only).",
    )
    _path_argument(serve)
    serve.add_argument("--host", default=DEFAULT_HOST, help="address to bind (loopback only)")
    serve.add_argument("--port", type=_port, default=DEFAULT_PORT, help="port to listen on")
    serve.add_argument("--open", action="store_true", help="open the dashboard in a browser")
    serve.add_argument("--theme", choices=REPORT_THEMES, default="auto", help="colour theme")
    serve.add_argument(
        "--allow-remote",
        action="store_true",
        help="allow binding a non-loopback address (there is no authentication!)",
    )
    serve.add_argument("-v", "--verbose", action="store_true", help="log every request")
    serve.set_defaults(handler=_cmd_serve)

    summary = commands.add_parser(
        "summary",
        parents=[filters],
        help="print a summary in the terminal",
        description="Print a compact summary with sparklines and bars (honours NO_COLOR).",
    )
    _path_argument(summary)
    summary.add_argument("--color", choices=COLOR_MODES, default="auto", help="when to use colour")
    summary.set_defaults(handler=_cmd_summary)
    return parser


def _filters(args: argparse.Namespace, now: Any) -> Filters:
    since = parse_when(args.since, now=now) if args.since else None
    until = parse_when(args.until, now=now, end_of_day=True) if args.until else None
    return Filters(
        since=since,
        until=until,
        author=args.author,
        branch=args.branch,
        paths=tuple(args.paths),
        no_merges=args.no_merges,
        max_commits=args.max_commits,
    )


# --------------------------------------------------------------------------------- helpers


def _analyze(args: argparse.Namespace, *, warn: bool = True) -> dict[str, Any]:
    now = utc_now()
    history = collect_history(args.path, _filters(args, now))
    metrics = compute_metrics(history, now=now)
    if warn:
        for warning in metrics["warnings"]:
            print(f"gitstory: warning: {warning}", file=sys.stderr)
    return metrics


def _emit(text: str, destination: str) -> None:
    """Write ``text`` as UTF-8 to ``destination`` (``-`` means stdout)."""
    data = text.encode("utf-8")
    if destination == "-":
        buffer = getattr(sys.stdout, "buffer", None)
        if buffer is None:
            sys.stdout.write(text)
        else:
            sys.stdout.flush()
            buffer.write(data)
            buffer.flush()
        return
    target = Path(destination)
    target.write_bytes(data)
    print(f"gitstory: wrote {target} ({len(data):,} bytes)", file=sys.stderr)


# -------------------------------------------------------------------------------- commands


def _cmd_report(args: argparse.Namespace) -> int:
    _emit(render_report(_analyze(args), theme=args.theme), args.output)
    return ExitCode.OK


def _cmd_json(args: argparse.Namespace) -> int:
    text = to_json(_analyze(args), indent=None if args.compact else 2) + "\n"
    _emit(text, args.output)
    return ExitCode.OK


def _cmd_card(args: argparse.Namespace) -> int:
    options = CardOptions(
        theme=args.theme,
        width=args.width,
        height=args.height,
        weeks=args.weeks,
        title=args.title,
    )
    _emit(render_card(_analyze(args), options), args.output)
    return ExitCode.OK


def _encodable(text: str, stream: Any) -> str:
    """``text`` with every character the stream's encoding cannot represent replaced by ``?``.

    Author names and paths come from the repository and may hold characters (say, CJK) that a
    legacy console code page cannot show; the summary must still print.
    """
    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        return text.encode(encoding, errors="replace").decode(encoding)
    except LookupError:
        return text.encode("ascii", errors="replace").decode("ascii")


def _cmd_summary(args: argparse.Namespace) -> int:
    metrics = _analyze(args)
    style = detect_style(sys.stdout, color=args.color)
    # Only a real console needs its escape processing switched on (and may refuse); when the
    # output is a pipe or a file, ``--color always`` is an explicit request for the codes.
    if style.color and sys.stdout.isatty() and not enable_windows_vt():
        style = type(style)(color=False, unicode=style.unicode, width=style.width)
    sys.stdout.write(_encodable(render_summary(metrics, style), sys.stdout))
    return ExitCode.OK


def _cmd_serve(args: argparse.Namespace) -> int:
    # Reject bad options before doing any work.
    _filters(args, utc_now())
    check_bind_address(args.host, allow_remote=args.allow_remote)
    first = [True]

    def compute() -> dict[str, Any]:
        warn, first[0] = first[0], False
        return _analyze(args, warn=warn)

    print(f"gitstory: analysing {args.path} ...", file=sys.stderr)
    service = MetricsService(compute, theme=args.theme)
    server = make_server(
        service,
        host=args.host,
        port=args.port,
        allow_remote=args.allow_remote,
        verbose=args.verbose,
    )
    if args.allow_remote:
        print(
            "gitstory: warning: --allow-remote serves your repository data without authentication",
            file=sys.stderr,
        )
    print(f"gitstory: serving {server.url} (Ctrl+C to stop)", file=sys.stderr)
    if args.open:
        webbrowser.open(server.url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("gitstory: stopped", file=sys.stderr)
    finally:
        server.server_close()
    return ExitCode.OK


# ----------------------------------------------------------------------------------- entry


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line interface and return the process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    handler: Callable[[argparse.Namespace], int] = args.handler
    try:
        return int(handler(args))
    except KeyboardInterrupt:
        print("gitstory: interrupted", file=sys.stderr)
        return int(ExitCode.INTERRUPTED)
    except (GitstoryError, OSError) as error:
        print(f"gitstory: error: {error}", file=sys.stderr)
        return int(exit_code_for(error))
    except Exception as error:  # last resort: report instead of a raw traceback
        if args.debug:
            traceback.print_exc()
        print(
            f"gitstory: unexpected error: {type(error).__name__}: {error} "
            "(run with --debug for a traceback)",
            file=sys.stderr,
        )
        return int(ExitCode.ERROR)
