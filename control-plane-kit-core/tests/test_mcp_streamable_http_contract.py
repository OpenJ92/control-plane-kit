import unittest

from control_plane_kit_core.operations import (
    InvalidMcpStreamableHttpContract,
    McpContentType,
    McpHttpMethod,
    McpStandardHeader,
    McpStreamableHttpContract,
)
from control_plane_kit_core.types import ApplicationProtocol, Protocol, Transport


class McpStreamableHttpContractTests(unittest.TestCase):
    def test_mcp_streamable_http_is_a_distinct_tcp_application_protocol(self) -> None:
        self.assertEqual(
            (
                Protocol.MCP_STREAMABLE_HTTP.transport,
                Protocol.MCP_STREAMABLE_HTTP.application,
                Protocol.MCP_STREAMABLE_HTTP.value,
            ),
            (
                Transport.TCP,
                ApplicationProtocol.MCP_STREAMABLE_HTTP,
                "mcp-streamable-http",
            ),
        )
        self.assertFalse(Protocol.MCP_STREAMABLE_HTTP.compatible_with(Protocol.HTTP))
        self.assertEqual(
            Protocol.MCP_STREAMABLE_HTTP.endpoint_schemes(),
            frozenset(("http", "https")),
        )

    def test_canonical_contract_names_endpoint_methods_headers_and_security(self) -> None:
        contract = McpStreamableHttpContract()

        self.assertEqual(contract.endpoint_path, "/mcp")
        self.assertEqual(contract.protocol, Protocol.MCP_STREAMABLE_HTTP)
        self.assertEqual(
            contract.methods,
            (McpHttpMethod.POST, McpHttpMethod.GET),
        )
        self.assertTrue(contract.authentication_required)
        self.assertTrue(contract.origin_validation_required)
        self.assertEqual(contract.local_bind_policy, "localhost-only")
        self.assertEqual(
            contract.accept_content_types,
            (McpContentType.APPLICATION_JSON, McpContentType.TEXT_EVENT_STREAM),
        )
        self.assertEqual(
            contract.required_post_headers,
            (
                McpStandardHeader.ACCEPT,
                McpStandardHeader.MCP_PROTOCOL_VERSION,
                McpStandardHeader.MCP_METHOD,
            ),
        )
        self.assertEqual(
            contract.name_header_methods,
            ("tools/call", "resources/read", "prompts/get"),
        )

    def test_descriptor_is_closed_and_round_trips(self) -> None:
        contract = McpStreamableHttpContract(endpoint_path="/control/mcp")

        descriptor = contract.descriptor()

        self.assertEqual(
            descriptor,
            {
                "kind": "mcp-streamable-http",
                "protocol": {
                    "transport": "tcp",
                    "application": "mcp-streamable-http",
                },
                "endpoint_path": "/control/mcp",
                "methods": ["POST", "GET"],
                "accept_content_types": ["application/json", "text/event-stream"],
                "required_post_headers": [
                    "Accept",
                    "MCP-Protocol-Version",
                    "Mcp-Method",
                ],
                "required_get_headers": ["Accept"],
                "name_header_methods": [
                    "tools/call",
                    "resources/read",
                    "prompts/get",
                ],
                "authentication_required": True,
                "origin_validation_required": True,
                "local_bind_policy": "localhost-only",
                "message_encoding": "json-rpc-utf8",
                "remote_registration": "url-plus-client-auth",
            },
        )
        self.assertEqual(McpStreamableHttpContract.from_descriptor(descriptor), contract)

        with self.assertRaises(InvalidMcpStreamableHttpContract):
            McpStreamableHttpContract.from_descriptor({**descriptor, "extra": True})
        with self.assertRaises(InvalidMcpStreamableHttpContract):
            McpStreamableHttpContract.from_descriptor(
                {**descriptor, "methods": ["POST"]}
            )

    def test_endpoint_paths_fail_closed(self) -> None:
        invalid_paths = ("", "mcp", "/", "/mcp?debug=true", "/mcp#fragment")
        for path in invalid_paths:
            with self.subTest(path=path):
                with self.assertRaises(InvalidMcpStreamableHttpContract):
                    McpStreamableHttpContract(endpoint_path=path)

    def test_contract_does_not_smuggle_hosted_process_or_stdio_state(self) -> None:
        descriptor = McpStreamableHttpContract().descriptor()
        rendered = repr(descriptor).lower()

        self.assertNotIn("fastapi", rendered)
        self.assertNotIn("uvicorn", rendered)
        self.assertNotIn("dockerfile", rendered)
        self.assertNotIn("stdio", rendered)
        self.assertNotIn("session-id", rendered)


if __name__ == "__main__":
    unittest.main()
