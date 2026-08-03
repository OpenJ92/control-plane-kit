"""Small stdlib HTTP active router process."""

from __future__ import annotations

from dataclasses import dataclass
import http.server
import os
import socketserver
import sys
from typing import Mapping
from urllib import error, parse, request


MAX_RESPONSE_BYTES = 1_048_576
DEFAULT_PORT = 8000


class RouterConfigurationError(ValueError):
    """Raised when the router startup contract is invalid."""


@dataclass(frozen=True)
class RouterSettings:
    active_target_url: str
    port: int = DEFAULT_PORT

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> "RouterSettings":
        values = environment or os.environ
        active_target_url = values.get("ACTIVE_TARGET_URL", "").strip()
        if not active_target_url:
            raise RouterConfigurationError("ACTIVE_TARGET_URL is required")
        parsed = parse.urlparse(active_target_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise RouterConfigurationError("ACTIVE_TARGET_URL must be an absolute HTTP URL")
        port = int(values.get("PORT", str(DEFAULT_PORT)))
        if not 0 < port < 65536:
            raise RouterConfigurationError("PORT must be between 1 and 65535")
        return cls(active_target_url=active_target_url.rstrip("/"), port=port)


class NoRedirects(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        raise error.HTTPError(req.full_url, code, "redirects are disabled", headers, fp)


def forward(settings: RouterSettings, method: str, path: str, headers: Mapping[str, str], body: bytes) -> tuple[int, bytes, str]:
    target = settings.active_target_url + path
    opener = request.build_opener(NoRedirects)
    outbound = request.Request(target, data=body or None, method=method)
    for name, value in headers.items():
        lower = name.lower()
        if lower not in {"host", "connection", "content-length"}:
            outbound.add_header(name, value)
    try:
        with opener.open(outbound, timeout=5.0) as response:
            payload = response.read(MAX_RESPONSE_BYTES + 1)
            if len(payload) > MAX_RESPONSE_BYTES:
                return 502, b"upstream response too large\n", "text/plain"
            content_type = response.headers.get("content-type", "application/octet-stream")
            return int(response.status), payload, content_type
    except error.HTTPError as exc:
        payload = exc.read(MAX_RESPONSE_BYTES)
        return int(exc.code), payload, exc.headers.get("content-type", "text/plain")
    except Exception:  # pragma: no cover - exercised by Docker smoke.
        return 502, b"upstream request failed\n", "text/plain"


def handler(settings: RouterSettings) -> type[http.server.BaseHTTPRequestHandler]:
    class ActiveRouterHandler(http.server.BaseHTTPRequestHandler):
        server_version = "control-plane-kit-http-active-router/1"

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/health/live":
                self._send(200, b"live\n", "text/plain")
                return
            self._forward()

        def do_POST(self) -> None:  # noqa: N802
            self._forward()

        def do_PUT(self) -> None:  # noqa: N802
            self._forward()

        def do_PATCH(self) -> None:  # noqa: N802
            self._forward()

        def do_DELETE(self) -> None:  # noqa: N802
            self._forward()

        def log_message(self, format: str, *args: object) -> None:
            return

        def _forward(self) -> None:
            length = int(self.headers.get("content-length", "0") or "0")
            body = self.rfile.read(length) if length else b""
            status, payload, content_type = forward(settings, self.command, self.path, self.headers, body)
            self._send(status, payload, content_type)

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("content-type", content_type)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return ActiveRouterHandler


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


def main() -> int:
    try:
        settings = RouterSettings.from_environment()
    except RouterConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    server = ThreadingHTTPServer(("0.0.0.0", settings.port), handler(settings))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
