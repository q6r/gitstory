"""Local server mode (``gitstory serve``).

Security model
--------------
* It binds to loopback (``127.0.0.1``) unless explicitly told otherwise; a non-loopback bind
  needs ``allow_remote=True`` because the server has no authentication.
* The routes are a fixed table. **No URL path ever touches the filesystem**: nothing is read
  from disk per request, there is no static-file handler and no directory listing.
* ``Host`` is validated against the bound address (DNS-rebinding protection) and the only
  mutating endpoint (``POST /api/refresh``) additionally checks ``Origin`` and
  ``Sec-Fetch-Site``; request bodies are bounded; refreshes are rate-limited and serialised.
* Every response carries strict security headers; the report's Content-Security-Policy only
  allows the exact inline scripts and styles of the rendered page.
* Request lines are sanitised before they are logged, so a client cannot forge log output.

Routes: ``/`` (the dashboard), ``/api/metrics.json``, ``/card.svg`` (``?theme=auto|dark|light``),
``/favicon.ico`` and ``POST /api/refresh``.
"""

from __future__ import annotations

import hashlib
import json
import socket
import sys
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import TCPServer
from typing import Any, cast
from urllib.parse import parse_qs

from gitstory.model import ConfigError, GitstoryError
from gitstory.render.html import content_security_policy, render_report
from gitstory.render.svg_card import THEMES as CARD_THEMES
from gitstory.render.svg_card import CardOptions, render_card
from gitstory.util import sanitize_text, to_json

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
LOOPBACK_NAMES = ("127.0.0.1", "localhost", "::1")
MAX_BODY_BYTES = 1024
MAX_QUERY_FIELDS = 8
REFRESH_INTERVAL = 1.0
REQUEST_TIMEOUT = 10.0
API_CSP = "default-src 'none'; frame-ancestors 'none'"
# The card is an SVG document that carries its own <style> and style="" attributes; no script
# can run in it (nothing is allowed by default-src, and the sandbox removes the rest).
CARD_CSP = "default-src 'none'; style-src 'unsafe-inline'; sandbox; frame-ancestors 'none'"
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), interest-cohort=()",
}
# route -> the methods it answers
ROUTES = {
    "/": ("GET", "HEAD"),
    "/api/metrics.json": ("GET", "HEAD"),
    "/card.svg": ("GET", "HEAD"),
    "/favicon.ico": ("GET", "HEAD"),
    "/api/refresh": ("POST",),
}
Refusal = tuple[int, str, dict[str, str]]


@dataclass(frozen=True, slots=True)
class Snapshot:
    """One rendered state of the report: bytes, validators and the policy that goes with it.

    ``cards`` maps each card theme to its SVG bytes and ETag.
    """

    html: bytes
    json: bytes
    html_etag: str
    json_etag: str
    csp: str
    generated_at: str
    commits: int
    cards: dict[str, tuple[bytes, str]]


def _etag(payload: bytes) -> str:
    return '"' + hashlib.sha256(payload).hexdigest()[:24] + '"'


class MetricsService:
    """Holds the current :class:`Snapshot` and recomputes it on demand.

    ``compute`` returns a fresh metrics document. Refreshes are serialised and rate-limited
    (``min_interval`` seconds), so repeated requests cannot keep the machine busy.
    """

    def __init__(
        self,
        compute: Callable[[], dict[str, Any]],
        *,
        theme: str = "auto",
        min_interval: float = REFRESH_INTERVAL,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._compute = compute
        self._theme = theme
        self._min_interval = min_interval
        self._clock = clock
        self._lock = threading.Lock()
        self._snapshot = self._build()
        self._built_at = clock()

    def _build(self) -> Snapshot:
        metrics = self._compute()
        page = render_report(metrics, theme=self._theme, live=True)
        payload = (to_json(metrics, indent=2) + "\n").encode("utf-8")
        body = page.encode("utf-8")
        cards = {}
        for theme in CARD_THEMES:
            svg = render_card(metrics, CardOptions(theme=theme)).encode("utf-8")
            cards[theme] = (svg, _etag(svg))
        return Snapshot(
            html=body,
            json=payload,
            html_etag=_etag(body),
            json_etag=_etag(payload),
            csp=content_security_policy(page, header=True),
            generated_at=str(metrics["generatedAt"]),
            commits=int(metrics["totals"]["commits"]),
            cards=cards,
        )

    @property
    def snapshot(self) -> Snapshot:
        """The current snapshot (always complete; replaced atomically on refresh)."""
        return self._snapshot

    def refresh(self) -> tuple[Snapshot, bool]:
        """Recompute unless one just happened; returns ``(snapshot, recomputed)``."""
        with self._lock:
            if self._clock() - self._built_at < self._min_interval:
                return self._snapshot, False
            self._snapshot = self._build()
            self._built_at = self._clock()
            return self._snapshot, True


def _host_text(address: object) -> str:
    if isinstance(address, bytes | bytearray):
        return address.decode("ascii", "replace")
    return str(address)


def _authority(host: str, port: int) -> str:
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


class GitstoryServer(ThreadingHTTPServer):
    """A threaded HTTP server bound to one address, serving one :class:`MetricsService`."""

    daemon_threads = True
    # On Windows SO_REUSEADDR lets another process bind the same port (port hijacking), so an
    # exclusive bind is used there instead.
    allow_reuse_address = sys.platform != "win32"

    def __init__(
        self,
        address: tuple[str, int],
        service: MetricsService,
        *,
        allow_remote: bool = False,
        verbose: bool = False,
    ) -> None:
        if ":" in address[0]:
            self.address_family = socket.AF_INET6
        super().__init__(address, GitstoryHandler)
        self.service = service
        self.allow_remote = allow_remote
        self.verbose = verbose
        port = self.server_address[1]
        bound = _host_text(self.server_address[0])
        self.allowed_hosts = {_authority(name, port) for name in (*LOOPBACK_NAMES, bound)}

    def server_bind(self) -> None:
        """Bind the socket without HTTPServer's reverse-DNS lookup of the address."""
        if sys.platform == "win32":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        TCPServer.server_bind(self)
        self.server_name = _host_text(self.server_address[0])
        self.server_port = self.server_address[1]

    @property
    def url(self) -> str:
        """The URL the server answers on."""
        host = _host_text(self.server_address[0])
        return f"http://{_authority(host, self.server_address[1])}/"

    def handle_error(self, request: Any, client_address: Any) -> None:  # noqa: ARG002
        """Stay quiet about clients that hang up and report other failures on one line.

        The default implementation prints a full traceback for every connection a browser
        resets or abandons, which drowns the real messages.
        """
        error = sys.exc_info()[1]
        if error is None or isinstance(error, ConnectionError | TimeoutError):
            return
        who = _host_text(client_address[0]) if client_address else "client"
        detail = sanitize_text(f"{type(error).__name__}: {error}", 200)
        print(f"gitstory: error while serving {who}: {detail}", file=sys.stderr)
        if self.verbose:
            traceback.print_exc()


def check_bind_address(host: str, *, allow_remote: bool = False) -> str:
    """Validate the address to bind and return it (``localhost`` becomes ``127.0.0.1``).

    Raises :class:`ConfigError` for a non-loopback ``host`` unless ``allow_remote`` is set.
    """
    if host == "localhost":
        return DEFAULT_HOST
    if host not in LOOPBACK_NAMES and not allow_remote:
        raise ConfigError(
            f"refusing to listen on {host!r}: gitstory has no authentication, so it only serves "
            "loopback addresses (127.0.0.1, ::1) unless you pass --allow-remote"
        )
    return host


class ListenError(GitstoryError, OSError):
    """The server could not bind its address (usually: the port is already in use)."""


def make_server(
    service: MetricsService,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    allow_remote: bool = False,
    verbose: bool = False,
) -> GitstoryServer:
    """Create (and bind) the server. ``port=0`` picks a free port.

    Raises :class:`ConfigError` for a non-loopback ``host`` without ``allow_remote`` and
    :class:`ListenError` when the address cannot be bound.
    """
    bind = check_bind_address(host, allow_remote=allow_remote)
    try:
        return GitstoryServer((bind, port), service, allow_remote=allow_remote, verbose=verbose)
    except OSError as error:
        raise ListenError(
            f"cannot listen on {_authority(bind, port)}: {error.strerror or error}. "
            "Is another program using this port? Choose a different one with --port."
        ) from error


class GitstoryHandler(BaseHTTPRequestHandler):
    """Request handler with a fixed route table (see the module docstring)."""

    protocol_version = "HTTP/1.1"
    timeout = REQUEST_TIMEOUT

    @property
    def app(self) -> GitstoryServer:
        """The owning server."""
        return cast("GitstoryServer", self.server)

    # ---- plumbing ------------------------------------------------------------------------

    def version_string(self) -> str:
        """Identify as ``gitstory`` without leaking interpreter versions."""
        return "gitstory"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - base class signature
        """Log requests only in verbose mode, with client-controlled text sanitised."""
        if self.app.verbose:
            text = sanitize_text(format % args, 300)
            print(f"gitstory: {self.address_string()} {text}", file=sys.stderr)

    def _send(
        self,
        status: int,
        body: bytes = b"",
        *,
        content_type: str = "application/json; charset=utf-8",
        extra: dict[str, str] | None = None,
        csp: str = API_CSP,
        cache: str = "no-store",
    ) -> None:
        bodiless = status in (204, 304)  # these statuses never carry a body or a length
        self.send_response(status)
        headers = {
            **SECURITY_HEADERS,
            "Content-Security-Policy": csp,
            "Cache-Control": cache,
            **(extra or {}),
        }
        if not bodiless:
            headers["Content-Type"] = content_type
            headers["Content-Length"] = str(len(body))
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        if body and not bodiless and self.command != "HEAD":
            self.wfile.write(body)

    def _fail(self, status: int, message: str, extra: dict[str, str] | None = None) -> None:
        self.close_connection = True
        headers = {"Connection": "close", **(extra or {})}
        self._send(status, json.dumps({"error": message}).encode("utf-8"), extra=headers)

    def send_error(
        self,
        code: int,
        message: str | None = None,
        explain: str | None = None,  # noqa: ARG002 - part of the base class signature
    ) -> None:
        """Answer protocol-level errors with a small JSON body instead of an HTML error page."""
        self._fail(code, sanitize_text(message or HTTPStatus(code).phrase, 120))

    # ---- request checks ------------------------------------------------------------------

    def _route(self) -> str:
        # The path is only ever compared to fixed strings; it is never decoded or opened.
        return self.path.split("?", 1)[0].split("#", 1)[0]

    def _card_theme(self) -> str | None:
        """The theme asked for in ``?theme=``: ``auto`` by default, ``None`` when invalid."""
        query = self.path.partition("?")[2].split("#", 1)[0]
        try:
            fields = parse_qs(query, keep_blank_values=True, max_num_fields=MAX_QUERY_FIELDS)
        except ValueError:
            return None
        themes = fields.get("theme", ["auto"])
        return themes[0] if len(themes) == 1 and themes[0] in CARD_THEMES else None

    def _same_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is not None and origin != f"http://{self.headers.get('Host', '')}":
            return False
        return self.headers.get("Sec-Fetch-Site", "same-origin") in ("same-origin", "none")

    def _refusal(self) -> Refusal | None:
        """Return ``(status, message, headers)`` if the request must be refused, else ``None``."""
        host = self.headers.get("Host", "").lower()
        if not self.app.allow_remote and host not in self.app.allowed_hosts:
            return 403, "host not allowed", {}
        methods = ROUTES.get(self._route())
        if methods is None:
            return 404, "not found", {}
        if self.command not in methods:
            return 405, "method not allowed", {"Allow": ", ".join(methods)}
        if self.command != "POST":
            return None
        if not self._same_origin():
            return 403, "cross-origin request refused", {}
        if self.headers.get("Transfer-Encoding"):
            return 411, "chunked request bodies are not supported", {}
        length = self.headers.get("Content-Length", "0")
        if not length.isascii() or not length.isdigit() or int(length) > MAX_BODY_BYTES:
            return 413, "request body too large", {}
        return None

    # ---- verbs -----------------------------------------------------------------------------

    def _drain(self) -> None:
        """Read and drop a small request body.

        A refused request is answered and the connection is closed; closing a socket that still
        holds unread bytes makes the operating system reset it, and the client may then never see
        the answer. Large or unlabelled bodies are not read (413/411) and the connection just ends.
        """
        length = self.headers.get("Content-Length", "0")
        if length.isascii() and length.isdigit() and 0 < int(length) <= MAX_BODY_BYTES:
            self.rfile.read(int(length))

    def _dispatch(self) -> None:
        """Every verb comes through here: refuse first, then answer from memory."""
        refusal = self._refusal()
        if refusal is not None:
            status, message, extra = refusal
            if status in (403, 404, 405):
                self._drain()
            self._fail(status, message, extra)
        elif self._route() == "/api/refresh":
            self._refresh()
        else:
            self._read()

    do_GET = do_HEAD = do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _dispatch  # noqa: N815

    def _read(self) -> None:
        snapshot = self.app.service.snapshot
        route = self._route()
        if route == "/":
            html_type = "text/html; charset=utf-8"
            self._conditional(snapshot.html, snapshot.html_etag, html_type, snapshot.csp)
        elif route == "/api/metrics.json":
            json_type = "application/json; charset=utf-8"
            self._conditional(snapshot.json, snapshot.json_etag, json_type, API_CSP)
        elif route == "/card.svg":
            theme = self._card_theme()
            if theme is None:
                self._fail(400, f"theme must be one of {', '.join(CARD_THEMES)}")
                return
            svg, etag = snapshot.cards[theme]
            self._conditional(svg, etag, "image/svg+xml; charset=utf-8", CARD_CSP)
        else:  # /favicon.ico
            self._send(204)

    def _conditional(self, body: bytes, etag: str, content_type: str, csp: str) -> None:
        offered = self.headers.get("If-None-Match", "").split(",")
        tokens = [token.strip().removeprefix("W/") for token in offered]
        if etag in tokens or "*" in tokens:
            self._send(304, extra={"ETag": etag, "Cache-Control": "no-cache"}, csp=csp)
        else:
            extra = {"ETag": etag}
            self._send(200, body, content_type=content_type, extra=extra, csp=csp, cache="no-cache")

    def _refresh(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        try:
            snapshot, refreshed = self.app.service.refresh()
        except GitstoryError as exc:
            self._fail(500, sanitize_text(str(exc), 300))
            return
        reply = {
            "ok": True,
            "refreshed": refreshed,
            "generatedAt": snapshot.generated_at,
            "commits": snapshot.commits,
            "etag": snapshot.json_etag,
        }
        self._send(200, json.dumps(reply).encode("utf-8"))
