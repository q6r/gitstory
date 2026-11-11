"""The local server: loopback only, DNS-rebinding and traversal defences, read-only, shutdown."""

from __future__ import annotations

import _thread
import copy
import http.client
import json
import socket
import struct
import threading
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import partial
from pathlib import Path
from typing import Any

import pytest
from make_repo import NOW, SyntheticRepo

from gitstory import cli, serve
from gitstory.model import ConfigError, GitstoryError
from gitstory.render.svg_card import THEMES as CARD_THEMES
from gitstory.render.svg_card import CardOptions, render_card
from gitstory.serve import (
    API_CSP,
    CARD_CSP,
    GitstoryServer,
    ListenError,
    MetricsService,
    check_bind_address,
    make_server,
)

FAST = {"poll_interval": 0.02}  # how quickly serve_forever notices shutdown() in the tests

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}


class Clock:
    """A clock that only moves when told to."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Source:
    """A metrics source that counts how often it is asked and can be made to fail."""

    def __init__(self, metrics: dict[str, Any]) -> None:
        self.metrics = metrics
        self.calls = 0
        self.error: Exception | None = None

    def __call__(self) -> dict[str, Any]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return copy.deepcopy(self.metrics)


@pytest.fixture
def source(metrics: dict[str, Any]) -> Source:
    return Source(metrics)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def service(source: Source, clock: Clock) -> MetricsService:
    return MetricsService(source, min_interval=1.0, clock=clock)


class Running:
    """A server in a background thread plus a few request helpers."""

    def __init__(self, server: GitstoryServer, thread: threading.Thread) -> None:
        self.server = server
        self.thread = thread
        self.port = server.server_address[1]

    def request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        host: str | None = None,
    ) -> tuple[int, http.client.HTTPMessage, bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            # `skip_host` lets the tests send a Host header of their own choosing
            connection.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            connection.putheader("Host", host if host is not None else f"127.0.0.1:{self.port}")
            for name, value in (headers or {}).items():
                connection.putheader(name, value)
            if body is not None and "Content-Length" not in (headers or {}):
                connection.putheader("Content-Length", str(len(body)))
            connection.endheaders(body)
            response = connection.getresponse()
            return response.status, response.msg, response.read()
        finally:
            connection.close()

    def get(self, path: str, **kwargs: Any) -> tuple[int, http.client.HTTPMessage, bytes]:
        return self.request("GET", path, **kwargs)

    def raw(self, payload: bytes, *, wait: float = 3.0) -> bytes:
        """Send bytes as they are and return everything the server answers."""
        with socket.create_connection(("127.0.0.1", self.port), timeout=wait) as sock:
            sock.sendall(payload)
            chunks = []
            try:
                while chunk := sock.recv(65536):
                    chunks.append(chunk)
            except TimeoutError:
                pass
            return b"".join(chunks)


@contextmanager
def running(service: MetricsService, port: int, **options: Any) -> Iterator[Running]:
    server = make_server(service, host="127.0.0.1", port=port, **options)
    thread = threading.Thread(target=server.serve_forever, kwargs=FAST, daemon=True)
    thread.start()
    try:
        yield Running(server, thread)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


@pytest.fixture
def live(service: MetricsService, free_port: int) -> Iterator[Running]:
    with running(service, free_port) as server:
        yield server


def body_json(body: bytes) -> dict[str, Any]:
    return json.loads(body)  # type: ignore[no-any-return]


# ------------------------------------------------------------------------ the service


def test_a_snapshot_holds_the_page_the_data_and_every_card(
    service: MetricsService, metrics: dict[str, Any]
) -> None:
    snapshot = service.snapshot
    page = snapshot.html.decode("utf-8")
    assert page.startswith("<!doctype html>")
    assert 'data-live="true"' in page  # the served page may talk to /api/refresh
    assert json.loads(snapshot.json) == metrics
    assert snapshot.json.endswith(b"}\n")
    assert snapshot.generated_at == metrics["generatedAt"]
    assert snapshot.commits == metrics["totals"]["commits"]
    assert "frame-ancestors 'none'" in snapshot.csp
    assert snapshot.csp.count("'sha256-") == 5  # three scripts and two styles
    assert set(snapshot.cards) == set(CARD_THEMES)
    for theme, (svg, etag) in snapshot.cards.items():
        assert svg.decode("utf-8") == render_card(metrics, CardOptions(theme=theme))
        assert etag.startswith('"')
        assert etag.endswith('"')
    assert len({etag for _, etag in snapshot.cards.values()}) == len(CARD_THEMES)


def test_the_theme_choice_reaches_the_served_page(source: Source, clock: Clock) -> None:
    dark = MetricsService(source, theme="dark", clock=clock)
    assert b'data-theme="dark"' in dark.snapshot.html


def test_refreshing_is_rate_limited_and_only_recomputes_when_due(
    service: MetricsService, source: Source, clock: Clock
) -> None:
    first = service.snapshot
    assert source.calls == 1
    same, recomputed = service.refresh()
    assert (same is first, recomputed) == (True, False)
    assert source.calls == 1  # nothing was computed
    clock.now += 0.99
    assert service.refresh()[1] is False
    clock.now += 0.02
    fresh, recomputed = service.refresh()
    assert recomputed is True
    assert fresh is not first
    assert service.snapshot is fresh
    assert source.calls == 2
    assert service.refresh()[1] is False  # the interval restarts after every recompute


def test_a_failed_refresh_keeps_the_previous_snapshot(
    service: MetricsService, source: Source, clock: Clock
) -> None:
    before = service.snapshot
    source.error = GitstoryError("git went away")
    clock.now += 5
    with pytest.raises(GitstoryError, match="git went away"):
        service.refresh()
    assert service.snapshot is before


def test_refreshes_never_overlap(source: Source, clock: Clock, metrics: dict[str, Any]) -> None:
    gate, inside = threading.Event(), threading.Event()
    running_now, peak = [0], [0]

    def slow() -> dict[str, Any]:
        running_now[0] += 1
        peak[0] = max(peak[0], running_now[0])
        inside.set()
        gate.wait(2)
        running_now[0] -= 1
        return copy.deepcopy(metrics)

    service = MetricsService(source, min_interval=0.0, clock=clock)
    service._compute = slow
    clock.now += 10
    workers = [threading.Thread(target=service.refresh) for _ in range(4)]
    for worker in workers:
        worker.start()
    inside.wait(2)
    gate.set()
    for worker in workers:
        worker.join(5)
    assert peak[0] == 1


# --------------------------------------------------------------------- bind addresses


@pytest.mark.parametrize(
    ("host", "expected"),
    [("localhost", "127.0.0.1"), ("127.0.0.1", "127.0.0.1"), ("::1", "::1")],
)
def test_loopback_addresses_are_accepted(host: str, expected: str) -> None:
    assert check_bind_address(host) == expected


@pytest.mark.parametrize(
    "host",
    ["0.0.0.0", "::", "192.168.1.20", "10.0.0.5", "example.com", "", "127.0.0.2", "LOCALHOST"],  # noqa: S104
)
def test_anything_else_needs_an_explicit_opt_in(host: str) -> None:
    with pytest.raises(ConfigError, match="refusing to listen"):
        check_bind_address(host)
    assert check_bind_address(host, allow_remote=True) == host


def test_a_refused_address_is_never_bound(service: MetricsService, free_port: int) -> None:
    with pytest.raises(ConfigError):
        make_server(service, host="0.0.0.0", port=free_port)  # noqa: S104
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", free_port))  # still free


def lan_addresses() -> list[str]:
    """Non-loopback IPv4 addresses of this machine (empty when it cannot tell)."""
    try:
        found = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        return []
    return sorted(
        {str(address[0]) for *_rest, address in found if not address[0].startswith("127.")}
    )


def test_the_server_listens_on_loopback_only(live: Running) -> None:
    assert live.server.server_address[0] == "127.0.0.1"
    assert live.server.url == f"http://127.0.0.1:{live.port}/"
    for address in lan_addresses()[:1]:  # a LAN address of this machine must not answer
        with socket.socket() as client:
            client.settimeout(0.5)
            assert client.connect_ex((address, live.port)) != 0


def test_a_second_server_cannot_take_the_same_port(service: MetricsService, live: Running) -> None:
    with pytest.raises(ListenError, match=f"cannot listen on 127.0.0.1:{live.port}") as taken:
        make_server(service, port=live.port)
    assert "--port" in str(taken.value)
    assert isinstance(taken.value, OSError)  # the CLI reports it as an I/O error


def test_ipv6_loopback_is_supported_when_the_machine_has_it(
    service: MetricsService, free_port: int
) -> None:
    try:
        with socket.socket(socket.AF_INET6) as probe:
            probe.bind(("::1", free_port))
    except OSError:
        pytest.skip("no IPv6 loopback on this machine")
    server = make_server(service, host="::1", port=free_port)
    thread = threading.Thread(target=server.serve_forever, kwargs=FAST, daemon=True)
    thread.start()
    try:
        assert server.url == f"http://[::1]:{free_port}/"
        connection = http.client.HTTPConnection("::1", free_port, timeout=5)
        connection.putrequest("GET", "/", skip_host=True)
        connection.putheader("Host", f"[::1]:{free_port}")
        connection.endheaders()
        assert connection.getresponse().status == 200
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


# ------------------------------------------------------------------------- the routes


def test_the_dashboard_is_served_at_the_root(live: Running, metrics: dict[str, Any]) -> None:
    status, headers, body = live.get("/")
    assert status == 200
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    assert headers["Content-Length"] == str(len(body))
    assert headers["Cache-Control"] == "no-cache"
    assert headers["ETag"].startswith('"')
    assert headers["Server"] == "gitstory"
    page = body.decode("utf-8")
    assert page.startswith("<!doctype html>")
    assert 'data-live="true"' in page
    assert metrics["repository"]["name"] in page
    assert headers["Content-Security-Policy"] == live.server.service.snapshot.csp


def test_the_metrics_are_served_as_json(live: Running, metrics: dict[str, Any]) -> None:
    status, headers, body = live.get("/api/metrics.json")
    assert status == 200
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    assert headers["Content-Security-Policy"] == API_CSP
    assert json.loads(body) == metrics


def test_the_page_and_the_api_describe_the_same_data(live: Running) -> None:
    _, _, page = live.get("/")
    _, _, api = live.get("/api/metrics.json")
    island = page.decode("utf-8").split('id="report-data" type="application/json">', 1)[1]
    assert json.loads(island.split("</script>", 1)[0]) == json.loads(api)


def test_the_card_is_served_as_a_sandboxed_svg(live: Running, metrics: dict[str, Any]) -> None:
    status, headers, body = live.get("/card.svg")
    assert status == 200
    assert headers["Content-Type"] == "image/svg+xml; charset=utf-8"
    assert headers["Content-Security-Policy"] == CARD_CSP
    assert "sandbox" in CARD_CSP
    assert "script" not in CARD_CSP.replace("default-src 'none'", "")
    assert body.decode("utf-8") == render_card(metrics, CardOptions(theme="auto"))
    ET.fromstring(body)


@pytest.mark.parametrize("theme", CARD_THEMES)
def test_the_card_theme_can_be_chosen(live: Running, metrics: dict[str, Any], theme: str) -> None:
    status, _, body = live.get(f"/card.svg?theme={theme}")
    assert status == 200
    assert body.decode("utf-8") == render_card(metrics, CardOptions(theme=theme))


@pytest.mark.parametrize(
    "query",
    [
        "theme=neon",
        "theme=",
        "theme=dark&theme=light",
        "theme=DARK",
        "theme=dark%00",
        "theme=%3Cb%3E",
    ],
)
def test_a_bad_card_theme_is_a_400(live: Running, query: str) -> None:
    status, headers, body = live.get(f"/card.svg?{query}")
    assert status == 400
    assert body_json(body) == {"error": "theme must be one of dark, light, auto"}
    assert headers["Connection"] == "close"


def test_unknown_query_parameters_are_ignored_and_too_many_are_refused(live: Running) -> None:
    assert live.get("/card.svg?width=99999&x=1")[0] == 200
    assert live.get("/api/metrics.json?pretty=1&callback=alert")[0] == 200
    many = "&".join(f"k{i}=1" for i in range(50))
    assert live.get(f"/card.svg?{many}")[0] == 400


def test_conditional_requests_answer_304_without_a_body(live: Running) -> None:
    for path in ("/", "/api/metrics.json", "/card.svg"):
        _, headers, _ = live.get(path)
        etag = headers["ETag"]
        for validator in (etag, f"W/{etag}", f'"other", {etag}', "*"):
            status, again, body = live.get(path, headers={"If-None-Match": validator})
            assert status == 304, (path, validator)
            assert body == b""
            assert again["ETag"] == etag
            assert "Content-Length" not in again
            assert "Content-Type" not in again
            assert again["Content-Security-Policy"]
        assert live.get(path, headers={"If-None-Match": '"stale"'})[0] == 200


def test_head_has_the_headers_of_get_and_no_body(live: Running) -> None:
    for path in ("/", "/api/metrics.json", "/card.svg"):
        _, get_headers, get_body = live.get(path)
        status, head_headers, head_body = live.request("HEAD", path)
        assert status == 200
        assert head_body == b""
        assert head_headers["Content-Length"] == get_headers["Content-Length"] == str(len(get_body))
        assert head_headers["ETag"] == get_headers["ETag"]


def test_the_favicon_request_is_answered_with_no_content(live: Running) -> None:
    status, headers, body = live.get("/favicon.ico")
    assert status == 204
    assert body == b""
    assert "Content-Type" not in headers


def test_every_response_carries_the_security_headers(live: Running) -> None:
    probes = [
        live.get("/"),
        live.get("/api/metrics.json"),
        live.get("/card.svg"),
        live.get("/favicon.ico"),
        live.get("/nope"),
        live.request("PUT", "/"),
        live.get("/", host="evil.example"),
        live.request("POST", "/api/refresh", headers={"Origin": "http://evil.example"}),
    ]
    for status, headers, _ in probes:
        for name, value in SECURITY_HEADERS.items():
            assert headers[name] == value, (status, name)
        assert "camera=()" in headers["Permissions-Policy"]
        assert headers["Content-Security-Policy"]
        assert headers["Cache-Control"] in ("no-store", "no-cache")


def test_the_server_does_not_reveal_what_it_runs_on(live: Running) -> None:
    _, headers, _ = live.get("/")
    banner = " ".join(f"{name}: {value}" for name, value in headers.items())
    assert "Python" not in banner
    assert "BaseHTTP" not in banner


def test_keep_alive_serves_several_requests_on_one_connection(live: Running) -> None:
    connection = http.client.HTTPConnection("127.0.0.1", live.port, timeout=5)
    try:
        for path in ("/", "/api/metrics.json", "/card.svg", "/"):
            connection.request("GET", path)
            response = connection.getresponse()
            response.read()
            assert response.status == 200
    finally:
        connection.close()


def test_errors_close_the_connection(live: Running) -> None:
    for status, headers, _ in (live.get("/nope"), live.request("DELETE", "/")):
        assert status in (404, 405)
        assert headers["Connection"] == "close"


# ----------------------------------------------------------- unknown routes and traversal


@pytest.mark.parametrize(
    "path",
    [
        "/nope",
        "/index.html",
        "/api",
        "/api/",
        "/api/metrics",
        "/api/metrics.json/",
        "/api/metrics.json/extra",
        "/CARD.SVG",
        "/card.svg/",
        "/app.js",
        "/assets/app.js",
        "/static/aurora.css",
        "/report.html",
        "/pyproject.toml",
        "/src/gitstory/cli.py",
        "/.git/config",
        "/.env",
    ],
)
def test_only_the_fixed_routes_exist(live: Running, path: str) -> None:
    status, _, body = live.get(path)
    assert status == 404, path
    assert body_json(body) == {"error": "not found"}


def test_a_leading_double_slash_is_never_an_absolute_uri_or_a_redirect(live: Running) -> None:
    # The standard library reduces "//x" to "/x" (so "//" is the root); nothing is redirected.
    assert live.get("//")[0] == 200
    status, headers, body = live.get("//evil.example/api/metrics.json")
    assert status == 404
    assert "Location" not in headers
    assert body_json(body) == {"error": "not found"}


def test_a_query_string_does_not_change_the_route(live: Running) -> None:
    assert live.get("/?x=1")[0] == 200
    assert live.get("/?")[0] == 200
    assert live.get("/nope?x=/")[0] == 404
    assert live.get("/api/metrics.json?/../../etc/passwd")[0] == 200  # data, not a path


@pytest.mark.parametrize(
    "path",
    [
        "/../../etc/passwd",
        "/..%2f..%2fetc%2fpasswd",
        "/%2e%2e/%2e%2e/windows/win.ini",
        "/%2E%2E%5C%2E%2E%5Cwindows%5Cwin.ini",
        "/..\\..\\windows\\win.ini",
        "/api/metrics.json/../../../pyproject.toml",
        "/static/../../pyproject.toml",
        "/.%00/",
        "/%00",
        "/..;/..;/",
        "/C:/Windows/win.ini",
        "/file:///etc/passwd",
        "/....//....//etc/passwd",
        "/%252e%252e/%252e%252e/etc/passwd",
    ],
)
def test_no_path_can_reach_the_file_system(live: Running, path: str, tmp_path: Path) -> None:
    status, _, body = live.get(path)
    assert status == 404
    assert body_json(body) == {"error": "not found"}
    for leaked in (b"root:", b"[fonts]", b"[tool.", b"[build-system]", b"gitstory"):
        assert leaked not in body


def test_an_absolute_uri_in_the_request_line_does_not_change_the_route(live: Running) -> None:
    status, _, body = live.get("http://evil.example/api/metrics.json")
    assert status == 404  # the target is compared as given, never parsed as a URL
    assert body_json(body) == {"error": "not found"}


# ---------------------------------------------------------------------------- read-only


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
@pytest.mark.parametrize("path", ["/", "/api/metrics.json", "/card.svg", "/favicon.ico"])
def test_the_server_only_reads(live: Running, method: str, path: str) -> None:
    before = live.server.service.snapshot
    status, headers, body = live.request(method, path, body=b"x=1" if method != "OPTIONS" else None)
    assert status == 405
    assert headers["Allow"] == "GET, HEAD"
    assert body_json(body) == {"error": "method not allowed"}
    assert live.server.service.snapshot is before


def test_the_refresh_endpoint_is_post_only(live: Running) -> None:
    for method in ("GET", "HEAD", "PUT", "DELETE", "PATCH", "OPTIONS"):
        status, headers, _ = live.request(method, "/api/refresh")
        assert status == 405, method
        assert headers["Allow"] == "POST"


def test_unusual_methods_are_refused_not_crashed_on(live: Running) -> None:
    for verb in (b"TRACE", b"CONNECT", b"PROPFIND", b"BREW", b"get"):
        reply = live.raw(verb + b" / HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n\r\n" % live.port)
        assert reply.split(b" ", 2)[1] in (b"405", b"501", b"404", b"400")


# ------------------------------------------------------------------ DNS rebinding guard


def test_the_host_header_must_name_this_server(live: Running) -> None:
    port = live.port
    for host in (f"127.0.0.1:{port}", f"localhost:{port}", f"LOCALHOST:{port}", f"[::1]:{port}"):
        assert live.get("/", host=host)[0] == 200, host
    for host in (
        "evil.example",
        f"evil.example:{port}",
        "127.0.0.1",  # no port: not what the browser sends for this origin
        f"127.0.0.1:{port + 1}",
        f"127.0.0.1:{port}.evil.example",
        f"x.127.0.0.1:{port}",
        f"127.0.0.1:{port}@evil.example",
        f"192.168.0.1:{port}",
        f"0.0.0.0:{port}",
        "",
        " ",
    ):
        status, _, body = live.get("/", host=host)
        assert status == 403, host
        assert body_json(body) == {"error": "host not allowed"}


@pytest.mark.parametrize("path", ["/", "/api/metrics.json", "/card.svg", "/nope", "/favicon.ico"])
def test_the_host_check_comes_before_everything_else(live: Running, path: str) -> None:
    status, _, body = live.get(path, host="rebind.attacker.example")
    assert status == 403
    assert body_json(body) == {"error": "host not allowed"}  # even a 404 path leaks nothing


def test_a_post_with_a_foreign_host_is_refused_before_it_is_read(live: Running) -> None:
    status, _, _ = live.request("POST", "/api/refresh", host="evil.example")
    assert status == 403


def test_a_request_without_a_host_header_is_refused(live: Running) -> None:
    reply = live.raw(b"GET / HTTP/1.0\r\n\r\n")
    assert reply.startswith(b"HTTP/1.1 403") or reply.startswith(b"HTTP/1.0 403")
    assert b"host not allowed" in reply
    assert b"report-data" not in reply


# --------------------------------------------------------------------------- refreshing


def test_refresh_recomputes_once_the_interval_has_passed(
    live: Running, source: Source, clock: Clock, metrics: dict[str, Any]
) -> None:
    status, headers, body = live.request("POST", "/api/refresh")
    assert status == 200
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    reply = body_json(body)
    assert reply["ok"] is True
    assert reply["refreshed"] is False  # just built
    assert reply["generatedAt"] == metrics["generatedAt"]
    assert reply["commits"] == metrics["totals"]["commits"]
    assert reply["etag"] == live.get("/api/metrics.json")[1]["ETag"]

    changed = copy.deepcopy(metrics)
    changed["generatedAt"] = "2030-01-01T00:00:00Z"
    source.metrics = changed
    clock.now += 5
    _, _, body = live.request("POST", "/api/refresh")
    after = body_json(body)
    assert after["refreshed"] is True
    assert after["generatedAt"] == "2030-01-01T00:00:00Z"
    assert after["etag"] != reply["etag"]
    assert live.get("/api/metrics.json")[1]["ETag"] == after["etag"]
    assert b"2030-01-01T00:00:00Z" in live.get("/")[2]


def test_a_failing_refresh_is_a_500_with_a_clean_message(
    live: Running, source: Source, clock: Clock
) -> None:
    source.error = GitstoryError("git exploded \x1b[31m red \x1b]0;pwned\x07 " + "x" * 500)
    clock.now += 5
    status, _, body = live.request("POST", "/api/refresh")
    assert status == 500
    message = body_json(body)["error"]
    assert "\x1b" not in message
    assert "\x07" not in message
    assert len(message) <= 300
    assert live.get("/")[0] == 200  # the old snapshot is still being served


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({}, 200),  # curl and friends send no Origin
        ({"Origin": "http://127.0.0.1:{port}"}, 200),
        ({"Origin": "http://localhost:{port}", "Host": "localhost:{port}"}, 200),
        ({"Sec-Fetch-Site": "same-origin"}, 200),
        ({"Sec-Fetch-Site": "none"}, 200),
        ({"Origin": "http://evil.example"}, 403),
        ({"Origin": "https://127.0.0.1:{port}"}, 403),
        ({"Origin": "null"}, 403),
        ({"Origin": "http://127.0.0.1:{port}.evil.example"}, 403),
        ({"Sec-Fetch-Site": "cross-site"}, 403),
        ({"Sec-Fetch-Site": "same-site"}, 403),
        ({"Sec-Fetch-Site": "cross-site", "Origin": "http://127.0.0.1:{port}"}, 403),
    ],
)
def test_the_refresh_endpoint_refuses_cross_origin_requests(
    live: Running, headers: dict[str, str], status: int
) -> None:
    filled = {name: value.format(port=live.port) for name, value in headers.items()}
    host = filled.pop("Host", None)
    code, _, body = live.request("POST", "/api/refresh", headers=filled, host=host)
    assert code == status, headers
    if status == 403:
        assert body_json(body) == {"error": "cross-origin request refused"}


def test_a_cross_origin_page_cannot_trigger_a_refresh(
    live: Running, source: Source, clock: Clock
) -> None:
    clock.now += 5
    calls = source.calls
    live.request("POST", "/api/refresh", headers={"Origin": "http://evil.example"})
    live.request("POST", "/api/refresh", headers={"Sec-Fetch-Site": "cross-site"})
    assert source.calls == calls  # the expensive work was never started


def test_request_bodies_are_bounded(live: Running) -> None:
    ok = live.request("POST", "/api/refresh", body=b"x" * 100)
    assert ok[0] == 200  # a small body is read and ignored
    big = live.request("POST", "/api/refresh", headers={"Content-Length": "2000"})
    assert big[0] == 413
    assert body_json(big[2]) == {"error": "request body too large"}
    for length in ("abc", "-5", "1e3", "\N{ARABIC-INDIC DIGIT ONE}", "99999999999999999999"):
        reply = live.raw(
            b"POST /api/refresh HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nContent-Length: %s\r\n\r\n"
            % (live.port, length.encode("utf-8"))
        )
        assert b" 413 " in reply.split(b"\r\n", 1)[0], length


def test_chunked_request_bodies_are_not_accepted(live: Running) -> None:
    reply = live.raw(
        b"POST /api/refresh HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nTransfer-Encoding: chunked\r\n\r\n"
        b"5\r\nhello\r\n0\r\n\r\n" % live.port
    )
    assert b" 411 " in reply.split(b"\r\n", 1)[0]


# ---------------------------------------------------------------------- hostile requests


def test_garbage_gets_a_json_error_not_a_traceback(live: Running) -> None:
    # A line that is not even a request is answered the way HTTP/0.9 would: the bare body.
    reply = live.raw(b"GARBAGE\r\n\r\n")
    assert b"Traceback" not in reply
    assert b"Python" not in reply
    assert "error" in json.loads(reply[reply.index(b"{") :])
    assert live.get("/")[0] == 200  # and the server carries on


def test_an_unsupported_protocol_version_is_refused(live: Running) -> None:
    reply = live.raw(b"GET / HTTP/9.9\r\nHost: 127.0.0.1:%d\r\n\r\n" % live.port)
    assert reply.split(b" ", 2)[1] == b"505"


def test_an_overlong_request_line_is_refused(live: Running) -> None:
    # exactly one byte over the 64 KiB limit, so that the server has read all of it
    request = b"GET /" + b"a" * (65_537 - len(b"GET /"))
    reply = live.raw(request)
    assert reply.split(b" ", 2)[1] == b"414"
    assert live.get("/")[0] == 200


def test_a_header_flood_is_refused(live: Running) -> None:
    # 101 header lines (the limit is 100) and no blank line: nothing is left unread
    headers = b"Host: 127.0.0.1:%d\r\n" % live.port + b"".join(
        b"X-%d: y\r\n" % i for i in range(100)
    )
    reply = live.raw(b"GET / HTTP/1.1\r\n" + headers)
    assert reply.split(b" ", 2)[1] == b"431"
    assert live.get("/")[0] == 200


def test_a_silent_client_is_dropped_after_the_timeout(
    service: MetricsService, free_port: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(serve.GitstoryHandler, "timeout", 0.3)
    with running(service, free_port) as server:
        started = time.monotonic()
        with socket.create_connection(("127.0.0.1", server.port), timeout=5) as sock:
            assert sock.recv(10) == b""  # the server gave up and closed the connection
        assert time.monotonic() - started < 3
        assert server.get("/")[0] == 200  # and kept serving everybody else


def test_many_clients_at_once(live: Running) -> None:
    results: list[int] = []

    def fetch() -> None:
        results.append(live.get("/api/metrics.json")[0])

    workers = [threading.Thread(target=fetch) for _ in range(24)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(10)
    assert results == [200] * 24


def test_request_lines_cannot_forge_log_entries(
    service: MetricsService, free_port: int, capsys: pytest.CaptureFixture[str]
) -> None:
    with running(service, free_port, verbose=True) as server:
        server.raw(
            b"GET /\x1b[2J\x1b]0;pwned\x07 HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n\r\n" % server.port
        )
        server.get("/ok")
    err = capsys.readouterr().err
    assert "gitstory: 127.0.0.1" in err
    assert "GET /" in err
    assert "\x1b" not in err
    assert "\x07" not in err
    assert "\N{SYMBOL FOR ESCAPE}" in err


def test_a_client_that_resets_the_connection_is_not_reported(
    live: Running, capsys: pytest.CaptureFixture[str]
) -> None:
    for _ in range(3):
        with socket.socket() as sock:
            sock.connect(("127.0.0.1", live.port))
            # SO_LINGER with a zero timeout makes close() send a reset instead of a clean FIN
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    assert live.get("/")[0] == 200  # still serving
    time.sleep(0.1)
    assert capsys.readouterr().err == ""


def test_an_unexpected_handler_failure_is_one_clean_line(
    live: Running, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(self: object) -> None:
        raise RuntimeError("boom \x1b[31m " + "x" * 500)

    monkeypatch.setattr(serve.GitstoryHandler, "do_GET", explode)
    with pytest.raises((http.client.RemoteDisconnected, ConnectionError)):
        live.get("/")
    time.sleep(0.1)
    err = capsys.readouterr().err
    assert err.startswith("gitstory: error while serving 127.0.0.1: RuntimeError: boom")
    assert err.count("\n") == 1  # no traceback unless --verbose
    assert "\x1b" not in err
    assert len(err) < 300
    monkeypatch.undo()
    assert live.get("/")[0] == 200  # the server survived


def test_nothing_is_logged_unless_asked(live: Running, capsys: pytest.CaptureFixture[str]) -> None:
    live.get("/")
    live.get("/nope")
    assert capsys.readouterr().err == ""


# --------------------------------------------------------------------------- shutdown


def test_shutdown_stops_the_loop_and_frees_the_port(
    service: MetricsService, free_port: int
) -> None:
    server = make_server(service, port=free_port)
    thread = threading.Thread(target=server.serve_forever, kwargs=FAST, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", free_port, timeout=5)
    connection.request("GET", "/")
    connection.getresponse().read()  # the connection stays open (keep-alive) and idle
    started = time.monotonic()
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert time.monotonic() - started < 5  # an idle client cannot hold the server up
    connection.close()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", free_port))  # the port is free again
    with socket.socket() as client:
        client.settimeout(1)
        assert client.connect_ex(("127.0.0.1", free_port)) != 0  # and nobody is listening


def test_a_request_in_flight_neither_blocks_shutdown_nor_gets_cut_off(
    service: MetricsService, free_port: int, clock: Clock
) -> None:
    started, release = threading.Event(), threading.Event()
    real = service._compute

    def slow() -> dict[str, Any]:
        started.set()
        release.wait(5)
        return real()

    service._compute = slow
    clock.now += 10
    server = make_server(service, port=free_port)
    loop = threading.Thread(target=server.serve_forever, kwargs=FAST, daemon=True)
    loop.start()
    answers: list[int] = []
    client = Running(server, loop)
    worker = threading.Thread(
        target=lambda: answers.append(client.request("POST", "/api/refresh")[0])
    )
    worker.start()
    assert started.wait(5)
    began = time.monotonic()
    server.shutdown()  # returns although a refresh is still running ...
    server.server_close()
    loop.join(5)
    assert time.monotonic() - began < 3
    release.set()
    worker.join(5)
    assert answers == [200]  # ... and that request still gets its answer


# -------------------------------------------------------------- the command, end to end


def serve_until_interrupted(
    monkeypatch: pytest.MonkeyPatch, port: int, client: Callable[[Running], None]
) -> None:
    """Make ``gitstory serve`` run ``client`` against itself and then see a Ctrl+C."""
    real_serve_forever = GitstoryServer.serve_forever

    def patched(self: GitstoryServer, poll_interval: float = 0.5) -> None:
        def work() -> None:
            try:
                client(Running(self, threading.current_thread()))
            finally:
                _thread.interrupt_main()  # what Ctrl+C does

        threading.Thread(target=work, daemon=True).start()
        real_serve_forever(self, poll_interval)

    monkeypatch.setattr(GitstoryServer, "serve_forever", patched)


def test_the_serve_command_serves_then_stops_on_ctrl_c(
    synthetic: SyntheticRepo,
    free_port: int,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOURCE_DATE_EPOCH", str(int(NOW.timestamp())))
    seen: dict[str, Any] = {}

    def client(server: Running) -> None:
        for path in ("/", "/api/metrics.json", "/card.svg"):
            status, _, body = server.get(path)
            seen[path] = (status, body)

    serve_until_interrupted(monkeypatch, free_port, client)
    code = cli.main(["serve", str(synthetic.path), "--port", str(free_port), "--allow-remote"])
    err = capsys.readouterr().err
    assert code == 0
    assert f"serving http://127.0.0.1:{free_port}/" in err
    assert "gitstory: stopped" in err
    assert "analysing" in err
    assert "--allow-remote serves your repository data without authentication" in err
    assert [seen[path][0] for path in ("/", "/api/metrics.json", "/card.svg")] == [200, 200, 200]
    assert json.loads(seen["/api/metrics.json"][1])["totals"]["commits"] == len(synthetic.commits)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", free_port))  # the server released the port on the way out


def test_a_refresh_reanalyses_the_repository_but_warns_only_once(
    stub: Path,
    free_port: int,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = cli.compute_metrics
    runs: list[int] = []

    def with_warning(*args: Any, **kwargs: Any) -> dict[str, Any]:
        runs.append(1)
        document = real(*args, **kwargs)
        document["warnings"] = ["the history is incomplete"]
        return document

    monkeypatch.setattr(cli, "compute_metrics", with_warning)
    monkeypatch.setattr(cli, "MetricsService", partial(MetricsService, min_interval=0.0))
    answers: list[dict[str, Any]] = []
    serve_until_interrupted(
        monkeypatch,
        free_port,
        lambda server: answers.append(body_json(server.request("POST", "/api/refresh")[2])),
    )
    assert cli.main(["serve", str(stub), "--port", str(free_port)]) == 0
    err = capsys.readouterr().err
    assert answers[0]["refreshed"] is True
    assert len(runs) == 2  # once at start-up, once for the refresh
    assert err.count("gitstory: warning: the history is incomplete") == 1


def test_the_open_flag_asks_the_browser_for_the_served_address(
    stub: Path,
    free_port: int,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[str] = []
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: opened.append(url) or True)

    def stop_at_once(self: GitstoryServer, poll_interval: float = 0.5) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(GitstoryServer, "serve_forever", stop_at_once)
    assert cli.main(["serve", str(stub), "--port", str(free_port), "--open"]) == 0
    capsys.readouterr()
    assert opened == [f"http://127.0.0.1:{free_port}/"]


def test_verbose_logs_every_request_to_stderr(
    stub: Path,
    free_port: int,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    serve_until_interrupted(monkeypatch, free_port, lambda server: server.get("/nope"))
    assert cli.main(["serve", str(stub), "--port", str(free_port), "-v"]) == 0
    err = capsys.readouterr().err
    assert 'GET /nope HTTP/1.1" 404' in err


def test_a_port_that_is_taken_is_a_clean_error(
    stub: Path,
    service: MetricsService,
    free_port: int,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with running(service, free_port):
        code = cli.main(["serve", str(stub), "--port", str(free_port)])
    err = capsys.readouterr().err
    assert code == 7  # an I/O error: the listening socket could not be opened
    assert f"gitstory: error: cannot listen on 127.0.0.1:{free_port}" in err
    assert "--port" in err
