from __future__ import annotations

import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib import request

from control_plane_kit_core.products import (
    ProductFamily,
    ProductDescriptorCodec,
    ProductIdentity,
    ProductInstanceConfiguration,
    instantiate_product,
)
from control_plane_kit_core.types import Protocol


ROOT = Path(__file__).resolve().parents[3]
PRODUCT = ROOT / "products" / "http_multiplexer"
PRODUCT_SRC = PRODUCT / "src"
DESCRIPTOR = PRODUCT / "product.cpk.json"


class HttpMultiplexerProductTests(unittest.TestCase):
    def setUp(self) -> None:
        sys.path.insert(0, str(PRODUCT_SRC))

    def tearDown(self) -> None:
        sys.path.remove(str(PRODUCT_SRC))
        for name in list(sys.modules):
            if name == "control_plane_kit_servers_http_multiplexer" or name.startswith(
                "control_plane_kit_servers_http_multiplexer."
            ):
                sys.modules.pop(name, None)

    def decode(self):
        return ProductDescriptorCodec().decode_document(DESCRIPTOR.read_bytes())

    def test_descriptor_round_trips_as_external_product_contract(self) -> None:
        document = self.decode()
        product = document.product

        self.assertEqual(
            product.identity,
            ProductIdentity("control-plane-kit", "http-multiplexer", 1),
        )
        self.assertEqual(product.display_name, "http-multiplexer")
        self.assertIs(product.product_family, ProductFamily.SERVER)
        self.assertEqual(product.image.registry, "ghcr.io")
        self.assertEqual(
            product.image.repository,
            "openj92/control-plane-kit-servers/http-multiplexer",
        )
        self.assertEqual(
            product.image.execution_reference,
            f"ghcr.io/openj92/control-plane-kit-servers/http-multiplexer@{product.image.digest}",
        )
        self.assertEqual(
            ProductDescriptorCodec().encode_document(product).content,
            DESCRIPTOR.read_bytes(),
        )

    def test_descriptor_declares_primary_and_optional_observer_requirements(self) -> None:
        product = self.decode().product
        sockets = product.runtime_contract.sockets

        self.assertEqual(sockets.provider("internal").protocol, Protocol.HTTP)
        self.assertEqual(
            {
                value.provider_socket: value.container_port
                for value in product.runtime_contract.provider_ports
            },
            {"internal": 8000},
        )
        self.assertEqual(
            sockets.requirement_names(),
            ("observer-a", "observer-b", "primary"),
        )
        self.assertTrue(sockets.requirement("primary").required)
        self.assertEqual(
            sockets.requirement("primary").env_bindings,
            ("MULTIPLEXER_PRIMARY_URL",),
        )
        self.assertFalse(sockets.requirement("observer-a").required)
        self.assertFalse(sockets.requirement("observer-b").required)
        self.assertEqual(
            sockets.requirement("observer-a").env_bindings,
            ("MULTIPLEXER_OBSERVER_A_URL",),
        )
        self.assertEqual(product.runtime_contract.public_environment, ())
        self.assertEqual(product.runtime_contract.secret_deliveries, ())
        for check in product.runtime_contract.verification.checks:
            self.assertEqual(check.policy.maximum_attempts, 5)
        self.assertIn("primary upstream owns the response", product.description.lower())

    def test_descriptor_instantiates_without_importing_process_code(self) -> None:
        product = self.decode().product

        block = instantiate_product(
            product,
            "multiplexer",
            ProductInstanceConfiguration.from_contract(product.runtime_contract),
        )

        self.assertEqual(block.block_id, "multiplexer")
        self.assertEqual(block.sockets.provider("internal").protocol, Protocol.HTTP)
        self.assertEqual(
            block.sockets.requirement("observer-a").env_bindings,
            ("MULTIPLEXER_OBSERVER_A_URL",),
        )
        self.assertNotIn("control_plane_kit_servers_http_multiplexer.server", sys.modules)

    def test_process_requires_primary_and_accepts_optional_observers(self) -> None:
        from control_plane_kit_servers_http_multiplexer import (
            MultiplexerConfigurationError,
            MultiplexerSettings,
        )

        with self.assertRaisesRegex(MultiplexerConfigurationError, "MULTIPLEXER_PRIMARY_URL"):
            MultiplexerSettings.from_environment({})
        for value in ("orders", "ftp://orders", "http://"):
            with self.subTest(value=value):
                with self.assertRaises(MultiplexerConfigurationError):
                    MultiplexerSettings.from_environment({"MULTIPLEXER_PRIMARY_URL": value})

        settings = MultiplexerSettings.from_environment(
            {
                "MULTIPLEXER_PRIMARY_URL": "http://primary:8000/",
                "MULTIPLEXER_OBSERVER_A_URL": "http://observer:8000/",
                "PORT": "18082",
            }
        )
        self.assertEqual(settings.primary_url, "http://primary:8000")
        self.assertEqual(settings.observer_urls, ("http://observer:8000",))
        self.assertEqual(settings.port, 18082)

    def test_primary_response_wins_and_observer_receives_copied_request(self) -> None:
        from control_plane_kit_servers_http_multiplexer.server import (
            MultiplexerSettings,
            deliver_observers,
            forward_primary,
        )

        primary = _RecordingServer(b"primary")
        observer = _RecordingServer(b"observed")
        try:
            settings = MultiplexerSettings(
                primary_url=primary.url,
                observer_urls=(observer.url,),
            )

            status, body, content_type = forward_primary(
                settings,
                "POST",
                "/wax",
                {"content-type": "text/plain"},
                b"payload",
            )
            errors = deliver_observers(
                settings,
                "POST",
                "/wax",
                {"content-type": "text/plain"},
                b"payload",
            )

            self.assertEqual(status, 200)
            self.assertEqual(body, b"primary")
            self.assertEqual(content_type, "text/plain")
            self.assertEqual(errors, ())
            self.assertEqual(primary.requests, [("POST", "/wax", b"payload")])
            self.assertEqual(observer.requests, [("POST", "/wax", b"payload")])
        finally:
            primary.close()
            observer.close()

    def test_observer_failure_is_reported_but_primary_response_can_survive(self) -> None:
        from control_plane_kit_servers_http_multiplexer.server import (
            MultiplexerSettings,
            deliver_observers,
            forward_primary,
        )

        primary = _RecordingServer(b"primary")
        try:
            settings = MultiplexerSettings(
                primary_url=primary.url,
                observer_urls=("http://127.0.0.1:1",),
            )

            _status, body, _content_type = forward_primary(settings, "GET", "/", {}, b"")
            errors = deliver_observers(settings, "GET", "/", {}, b"")

            self.assertEqual(body, b"primary")
            self.assertEqual(len(errors), 1)
            self.assertIn("observer-1", errors[0])
        finally:
            primary.close()

    def test_observer_failure_evidence_excludes_raw_exception_text(self) -> None:
        from control_plane_kit_servers_http_multiplexer.server import (
            MultiplexerSettings,
            deliver_observers,
        )

        with patch(
            "control_plane_kit_servers_http_multiplexer.server._open",
            side_effect=RuntimeError("token=secret target=http://observer.internal"),
        ):
            errors = deliver_observers(
                MultiplexerSettings(
                    primary_url="http://primary.internal",
                    observer_urls=("http://observer.internal",),
                ),
                "POST",
                "/wax",
                {},
                b"payload",
            )

        self.assertEqual(errors, ("observer-1: upstream-failure",))
        self.assertNotIn("secret", errors[0])
        self.assertNotIn("observer.internal", errors[0])

    def test_entrypoint_source_preserves_bounded_fail_open_observers(self) -> None:
        source = (
            PRODUCT_SRC / "control_plane_kit_servers_http_multiplexer" / "server.py"
        ).read_text(encoding="utf-8")

        self.assertIn("MULTIPLEXER_PRIMARY_URL", source)
        self.assertIn("MULTIPLEXER_OBSERVER_A_URL", source)
        self.assertIn("MAX_RESPONSE_BYTES", source)
        self.assertIn("MAX_OBSERVER_RESPONSE_BYTES", source)
        self.assertIn("NoRedirects", source)
        self.assertIn("observers are explicitly fail-open", source)
        self.assertNotIn("allow_redirects=True", source)
        self.assertNotIn('f"primary request failed:', source)
        self.assertNotIn('errors.append(f"observer-{index}: {exc}")', source)

    def test_dockerfile_uses_product_entrypoint_and_non_root_user(self) -> None:
        dockerfile = (PRODUCT / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("USER multiplexer", dockerfile)
        self.assertIn("control_plane_kit_servers_http_multiplexer.server", dockerfile)
        self.assertIn("EXPOSE 8000", dockerfile)
        self.assertNotIn("MULTIPLEXER_PRIMARY_URL=", dockerfile)
        self.assertNotIn("docker system prune", dockerfile)

    def test_descriptor_digest_is_catalogue_ready(self) -> None:
        content = DESCRIPTOR.read_bytes()
        digest = hashlib.sha256(content).hexdigest()

        self.assertEqual(len(digest), 64)
        self.assertEqual(ProductDescriptorCodec().decode_document(content).content_digest, digest)


class _RecordingServer:
    def __init__(self, response_body: bytes) -> None:
        self.requests: list[tuple[str, str, bytes]] = []
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                self._record()

            def do_POST(self) -> None:  # noqa: N802
                self._record()

            def log_message(self, format: str, *args: object) -> None:
                return

            def _record(self) -> None:
                length = int(self.headers.get("content-length", "0") or "0")
                body = self.rfile.read(length) if length else b""
                parent.requests.append((self.command, self.path, body))
                self.send_response(200)
                self.send_header("content-type", "text/plain")
                self.send_header("content-length", str(len(response_body)))
                self.end_headers()
                self.wfile.write(response_body)

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def close(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()


if __name__ == "__main__":
    unittest.main()
