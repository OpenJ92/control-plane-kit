from __future__ import annotations

import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import importlib
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

from control_plane_kit_core.products import (
    ProductFamily,
    ProductDescriptorCodec,
    ProductIdentity,
    ProductInstanceConfiguration,
    instantiate_product,
)
from control_plane_kit_core.types import Protocol


ROOT = Path(__file__).resolve().parents[3]
PRODUCT = ROOT / "products" / "http_active_router"
PRODUCT_SRC = PRODUCT / "src"
DESCRIPTOR = PRODUCT / "product.cpk.json"


class HttpActiveRouterProductTests(unittest.TestCase):
    def setUp(self) -> None:
        sys.path.insert(0, str(PRODUCT_SRC))

    def tearDown(self) -> None:
        sys.path.remove(str(PRODUCT_SRC))
        for name in list(sys.modules):
            if name == "control_plane_kit_servers_http_active_router" or name.startswith(
                "control_plane_kit_servers_http_active_router."
            ):
                sys.modules.pop(name, None)

    def decode(self):
        return ProductDescriptorCodec().decode_document(DESCRIPTOR.read_bytes())

    def test_descriptor_round_trips_as_external_product_contract(self) -> None:
        document = self.decode()
        product = document.product

        self.assertEqual(
            product.identity,
            ProductIdentity("control-plane-kit", "http-active-router", 1),
        )
        self.assertEqual(product.display_name, "http-active-router")
        self.assertIs(product.product_family, ProductFamily.SERVER)
        self.assertEqual(product.image.registry, "ghcr.io")
        self.assertEqual(
            product.image.repository,
            "openj92/control-plane-kit-servers/http-active-router",
        )
        self.assertEqual(
            product.image.execution_reference,
            f"ghcr.io/openj92/control-plane-kit-servers/http-active-router@{product.image.digest}",
        )
        self.assertEqual(
            ProductDescriptorCodec().encode_document(product).content,
            DESCRIPTOR.read_bytes(),
        )

    def test_descriptor_declares_http_provider_and_active_requirement(self) -> None:
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
        self.assertEqual(sockets.requirement_names(), ("active",))
        self.assertEqual(sockets.requirement("active").protocol, Protocol.HTTP)
        self.assertEqual(
            sockets.requirement("active").env_bindings,
            ("ACTIVE_TARGET_URL",),
        )
        self.assertEqual(product.runtime_contract.public_environment, ())
        self.assertEqual(product.runtime_contract.secret_deliveries, ())
        for check in product.runtime_contract.verification.checks:
            self.assertEqual(check.policy.maximum_attempts, 5)
        self.assertIn("runtime target mutation", product.description.lower())

    def test_descriptor_instantiates_without_importing_process_code(self) -> None:
        product = self.decode().product

        block = instantiate_product(
            product,
            "router",
            ProductInstanceConfiguration.from_contract(product.runtime_contract),
        )

        self.assertEqual(block.block_id, "router")
        self.assertEqual(block.sockets.provider("internal").protocol, Protocol.HTTP)
        self.assertEqual(block.sockets.requirement("active").env_bindings, ("ACTIVE_TARGET_URL",))
        self.assertNotIn(
            "control_plane_kit_servers_http_active_router.server",
            sys.modules,
        )

    def test_process_requires_absolute_http_active_target(self) -> None:
        from control_plane_kit_servers_http_active_router import (
            RouterConfigurationError,
            RouterSettings,
        )

        with self.assertRaisesRegex(RouterConfigurationError, "ACTIVE_TARGET_URL"):
            RouterSettings.from_environment({})
        for value in ("orders", "ftp://orders", "http://"):
            with self.subTest(value=value):
                with self.assertRaises(RouterConfigurationError):
                    RouterSettings.from_environment({"ACTIVE_TARGET_URL": value})

        settings = RouterSettings.from_environment(
            {"ACTIVE_TARGET_URL": "http://orders:8000/", "PORT": "18080"}
        )
        self.assertEqual(settings.active_target_url, "http://orders:8000")
        self.assertEqual(settings.port, 18080)

    def test_process_forwards_method_path_headers_and_body_to_active_target(self) -> None:
        from control_plane_kit_servers_http_active_router.server import (
            RouterSettings,
            forward,
        )

        target = _RecordingServer(b"blue")
        try:
            status, body, content_type = forward(
                RouterSettings(active_target_url=target.url),
                "POST",
                "/wax?batch=7",
                {
                    "content-type": "text/plain",
                    "x-cpk-test": "forwarded",
                    "host": "must-not-forward",
                },
                b"payload",
            )

            self.assertEqual(status, 200)
            self.assertEqual(body, b"blue")
            self.assertEqual(content_type, "text/plain")
            self.assertEqual(
                target.requests,
                [
                    (
                        "POST",
                        "/wax?batch=7",
                        b"payload",
                        "forwarded",
                    )
                ],
            )
        finally:
            target.close()

    def test_process_failure_is_bounded_without_upstream_exception_text(self) -> None:
        from control_plane_kit_servers_http_active_router.server import (
            RouterSettings,
            forward,
        )

        with patch(
            "control_plane_kit_servers_http_active_router.server.request.build_opener"
        ) as build_opener:
            build_opener.return_value.open.side_effect = RuntimeError(
                "token=secret target=http://private.internal"
            )

            status, body, content_type = forward(
                RouterSettings(active_target_url="http://private.internal"),
                "GET",
                "/",
                {},
                b"",
            )

        self.assertEqual(status, 502)
        self.assertEqual(body, b"upstream request failed\n")
        self.assertEqual(content_type, "text/plain")
        self.assertNotIn(b"secret", body)
        self.assertNotIn(b"private.internal", body)

    def test_entrypoint_source_preserves_bounded_no_redirect_forwarding(self) -> None:
        source = (
            PRODUCT_SRC / "control_plane_kit_servers_http_active_router" / "server.py"
        ).read_text(encoding="utf-8")

        self.assertIn("ACTIVE_TARGET_URL", source)
        self.assertIn("MAX_RESPONSE_BYTES", source)
        self.assertIn("NoRedirects", source)
        self.assertIn('parsed.scheme not in {"http", "https"}', source)
        self.assertNotIn("allow_redirects=True", source)
        self.assertNotIn("subprocess", source)
        self.assertNotIn('f"upstream request failed:', source)

    def test_dockerfile_uses_product_entrypoint_and_non_root_user(self) -> None:
        dockerfile = (PRODUCT / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("USER router", dockerfile)
        self.assertIn("control_plane_kit_servers_http_active_router.server", dockerfile)
        self.assertIn("EXPOSE 8000", dockerfile)
        self.assertNotIn("ACTIVE_TARGET_URL=", dockerfile)
        self.assertNotIn("docker system prune", dockerfile)

    def test_descriptor_digest_is_catalogue_ready(self) -> None:
        content = DESCRIPTOR.read_bytes()
        digest = hashlib.sha256(content).hexdigest()

        self.assertEqual(len(digest), 64)
        self.assertEqual(ProductDescriptorCodec().decode_document(content).content_digest, digest)


class _RecordingServer:
    def __init__(self, response_body: bytes) -> None:
        self.requests: list[tuple[str, str, bytes, str | None]] = []
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("content-length", "0") or "0")
                body = self.rfile.read(length) if length else b""
                parent.requests.append(
                    (
                        self.command,
                        self.path,
                        body,
                        self.headers.get("x-cpk-test"),
                    )
                )
                self.send_response(200)
                self.send_header("content-type", "text/plain")
                self.send_header("content-length", str(len(response_body)))
                self.end_headers()
                self.wfile.write(response_body)

            def log_message(self, format: str, *args: object) -> None:
                return

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
