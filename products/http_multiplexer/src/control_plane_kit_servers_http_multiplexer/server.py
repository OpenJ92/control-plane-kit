"""Small stdlib HTTP multiplexer process."""

from __future__ import annotations

from dataclasses import dataclass
import http.server
import os
import socketserver
import sys
from typing import Mapping
from urllib import error, parse, request


MAX_RESPONSE_BYTES = 1_048_576
MAX_OBSERVER_RESPONSE_BYTES = 16_384
DEFAULT_PORT = 8000
OBSERVER_ENVIRONMENTS = (
    "MULTIPLEXER_OBSERVER_A_URL",
    "MULTIPLEXER_OBSERVER_B_URL",
)


class MultiplexerConfigurationError(ValueError):
    """Raised when the multiplexer startup contract is invalid."""


@dataclass(frozen=True)
class MultiplexerSettings:
    primary_url: str
    observer_urls: tuple[str, ...] = ()
    port: int = DEFAULT_PORT

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> "MultiplexerSettings":
        values = environment or os.environ
        primary_url = _required_url(values.get("MULTIPLEXER_PRIMARY_URL", ""), "MULTIPLEXER_PRIMARY_URL")
        observers = tuple(
            _optional_url(values.get(name, ""), name)
            for name in OBSERVER_ENVIRONMENTS
            if values.get(name, "").strip()
        )
        port = int(values.get("PORT", str(DEFAULT_PORT)))
        if not 0 < port < 65536:
            raise MultiplexerConfigurationError("PORT must be between 1 and 65535")
        return cls(primary_url=primary_url, observer_urls=observers, port=port)


class NoRedirects(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        raise error.HTTPError(req.full_url, code, "redirects are disabled", headers, fp)


def forward_primary(
    settings: MultiplexerSettings,
    method: str,
    path: str,
    headers: Mapping[str, str],
    body: bytes,
) -> tuple[int, bytes, str]:
    return _open(settings.primary_url + path, method, headers, body, MAX_RESPONSE_BYTES)


def deliver_observers(
    settings: MultiplexerSettings,
    method: str,
    path: str,
    headers: Mapping[str, str],
    body: bytes,
) -> tuple[str, ...]:
    errors: list[str] = []
    for index, observer_url in enumerate(settings.observer_urls, start=1):
        try:
            _open(observer_url + path, method, headers, body, MAX_OBSERVER_RESPONSE_BYTES)
        except Exception:  # noqa: BLE001 - observers are explicitly fail-open.
            errors.append(f"observer-{index}: upstream-failure")
    return tuple(errors)


def _open(
    url: str,
    method: str,
    headers: Mapping[str, str],
    body: bytes,
    max_response_bytes: int,
) -> tuple[int, bytes, str]:
    opener = request.build_opener(NoRedirects)
    outbound = request.Request(url, data=body or None, method=method)
    for name, value in headers.items():
        lower = name.lower()
        if lower not in {"host", "connection", "content-length"}:
            outbound.add_header(name, value)
    with opener.open(outbound, timeout=5.0) as response:
        payload = response.read(max_response_bytes + 1)
        if len(payload) > max_response_bytes:
            raise RuntimeError("upstream response too large")
        content_type = response.headers.get("content-type", "application/octet-stream")
        return int(response.status), payload, content_type


def handler(settings: MultiplexerSettings) -> type[http.server.BaseHTTPRequestHandler]:
    class MultiplexerHandler(http.server.BaseHTTPRequestHandler):
        server_version = "control-plane-kit-http-multiplexer/1"

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/health/live":
                self._send(200, b"live\n", "text/plain")
                return
            self._multiplex()

        def do_POST(self) -> None:  # noqa: N802
            self._multiplex()

        def do_PUT(self) -> None:  # noqa: N802
            self._multiplex()

        def do_PATCH(self) -> None:  # noqa: N802
            self._multiplex()

        def do_DELETE(self) -> None:  # noqa: N802
            self._multiplex()

        def log_message(self, format: str, *args: object) -> None:
            return

        def _multiplex(self) -> None:
            length = int(self.headers.get("content-length", "0") or "0")
            body = self.rfile.read(length) if length else b""
            try:
                status, payload, content_type = forward_primary(
                    settings,
                    self.command,
                    self.path,
                    self.headers,
                    body,
                )
            except Exception:
                self._send(502, b"primary request failed\n", "text/plain")
                return
            deliver_observers(settings, self.command, self.path, self.headers, body)
            self._send(status, payload, content_type)

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("content-type", content_type)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return MultiplexerHandler


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


def main() -> int:
    try:
        settings = MultiplexerSettings.from_environment()
    except MultiplexerConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    server = ThreadingHTTPServer(("0.0.0.0", settings.port), handler(settings))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    return 0


def _required_url(value: str, name: str) -> str:
    if not value.strip():
        raise MultiplexerConfigurationError(f"{name} is required")
    return _validate_url(value, name)


def _optional_url(value: str, name: str) -> str:
    return _validate_url(value, name)


def _validate_url(value: str, name: str) -> str:
    candidate = value.strip().rstrip("/")
    parsed = parse.urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise MultiplexerConfigurationError(f"{name} must be an absolute HTTP URL")
    return candidate


if __name__ == "__main__":
    raise SystemExit(main())
