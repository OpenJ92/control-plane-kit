"""Pure MCP Streamable HTTP contract values."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping

from control_plane_kit_core.types import Protocol


class InvalidMcpStreamableHttpContract(ValueError):
    """Raised when an MCP Streamable HTTP contract is incoherent."""


class McpHttpMethod(StrEnum):
    """Closed HTTP methods for the MCP Streamable HTTP endpoint."""

    POST = "POST"
    GET = "GET"


class McpContentType(StrEnum):
    """Closed content types required by MCP Streamable HTTP."""

    APPLICATION_JSON = "application/json"
    TEXT_EVENT_STREAM = "text/event-stream"


class McpStandardHeader(StrEnum):
    """Closed standard headers for MCP Streamable HTTP requests."""

    ACCEPT = "Accept"
    MCP_PROTOCOL_VERSION = "MCP-Protocol-Version"
    MCP_METHOD = "Mcp-Method"
    MCP_NAME = "Mcp-Name"


_DESCRIPTOR_KEYS = {
    "kind",
    "protocol",
    "endpoint_path",
    "methods",
    "accept_content_types",
    "required_post_headers",
    "required_get_headers",
    "name_header_methods",
    "authentication_required",
    "origin_validation_required",
    "local_bind_policy",
    "message_encoding",
    "remote_registration",
}


@dataclass(frozen=True)
class McpStreamableHttpContract:
    """Transport contract for a hosted remote MCP endpoint."""

    endpoint_path: str = "/mcp"
    protocol: Protocol = Protocol.MCP_STREAMABLE_HTTP
    methods: tuple[McpHttpMethod, ...] = (
        McpHttpMethod.POST,
        McpHttpMethod.GET,
    )
    accept_content_types: tuple[McpContentType, ...] = (
        McpContentType.APPLICATION_JSON,
        McpContentType.TEXT_EVENT_STREAM,
    )
    required_post_headers: tuple[McpStandardHeader, ...] = (
        McpStandardHeader.ACCEPT,
        McpStandardHeader.MCP_PROTOCOL_VERSION,
        McpStandardHeader.MCP_METHOD,
    )
    required_get_headers: tuple[McpStandardHeader, ...] = (
        McpStandardHeader.ACCEPT,
    )
    name_header_methods: tuple[str, ...] = (
        "tools/call",
        "resources/read",
        "prompts/get",
    )
    authentication_required: bool = True
    origin_validation_required: bool = True
    local_bind_policy: str = "localhost-only"
    message_encoding: str = "json-rpc-utf8"
    remote_registration: str = "url-plus-client-auth"

    def __post_init__(self) -> None:
        _validate_endpoint_path(self.endpoint_path)
        if self.protocol is not Protocol.MCP_STREAMABLE_HTTP:
            raise InvalidMcpStreamableHttpContract(
                "MCP Streamable HTTP requires Protocol.MCP_STREAMABLE_HTTP"
            )
        if self.methods != (McpHttpMethod.POST, McpHttpMethod.GET):
            raise InvalidMcpStreamableHttpContract(
                "MCP endpoint must support POST and GET in canonical order"
            )
        if self.accept_content_types != (
            McpContentType.APPLICATION_JSON,
            McpContentType.TEXT_EVENT_STREAM,
        ):
            raise InvalidMcpStreamableHttpContract(
                "MCP clients must accept JSON and event-stream responses"
            )
        if self.required_post_headers != (
            McpStandardHeader.ACCEPT,
            McpStandardHeader.MCP_PROTOCOL_VERSION,
            McpStandardHeader.MCP_METHOD,
        ):
            raise InvalidMcpStreamableHttpContract(
                "MCP POST requests require Accept, protocol version, and method headers"
            )
        if self.required_get_headers != (McpStandardHeader.ACCEPT,):
            raise InvalidMcpStreamableHttpContract(
                "MCP GET requests require the Accept header"
            )
        if self.name_header_methods != (
            "tools/call",
            "resources/read",
            "prompts/get",
        ):
            raise InvalidMcpStreamableHttpContract(
                "Mcp-Name applies to the closed named MCP request methods"
            )
        if self.authentication_required is not True:
            raise InvalidMcpStreamableHttpContract(
                "remote MCP endpoints require authentication"
            )
        if self.origin_validation_required is not True:
            raise InvalidMcpStreamableHttpContract(
                "remote MCP endpoints require Origin validation"
            )
        if self.local_bind_policy != "localhost-only":
            raise InvalidMcpStreamableHttpContract(
                "local MCP endpoints must bind localhost-only"
            )
        if self.message_encoding != "json-rpc-utf8":
            raise InvalidMcpStreamableHttpContract(
                "MCP messages must be JSON-RPC encoded as UTF-8"
            )
        if self.remote_registration != "url-plus-client-auth":
            raise InvalidMcpStreamableHttpContract(
                "remote registration must preserve URL and client auth separately"
            )

    def descriptor(self) -> dict[str, object]:
        return {
            "kind": "mcp-streamable-http",
            "protocol": self.protocol.descriptor(),
            "endpoint_path": self.endpoint_path,
            "methods": [method.value for method in self.methods],
            "accept_content_types": [
                content_type.value for content_type in self.accept_content_types
            ],
            "required_post_headers": [
                header.value for header in self.required_post_headers
            ],
            "required_get_headers": [
                header.value for header in self.required_get_headers
            ],
            "name_header_methods": list(self.name_header_methods),
            "authentication_required": self.authentication_required,
            "origin_validation_required": self.origin_validation_required,
            "local_bind_policy": self.local_bind_policy,
            "message_encoding": self.message_encoding,
            "remote_registration": self.remote_registration,
        }

    @classmethod
    def from_descriptor(
        cls,
        value: Mapping[str, object],
    ) -> "McpStreamableHttpContract":
        if set(value) != _DESCRIPTOR_KEYS:
            raise InvalidMcpStreamableHttpContract(
                "MCP Streamable HTTP descriptor has unexpected keys"
            )
        if value["kind"] != "mcp-streamable-http":
            raise InvalidMcpStreamableHttpContract(
                "MCP Streamable HTTP descriptor has wrong kind"
            )
        protocol_value = value["protocol"]
        if not isinstance(protocol_value, Mapping):
            raise InvalidMcpStreamableHttpContract("protocol must be a descriptor")
        try:
            protocol = Protocol.from_descriptor(protocol_value)
            return cls(
                endpoint_path=_text(value["endpoint_path"], "endpoint_path"),
                protocol=protocol,
                methods=tuple(
                    McpHttpMethod(method)
                    for method in _string_list(value["methods"], "methods")
                ),
                accept_content_types=tuple(
                    McpContentType(content_type)
                    for content_type in _string_list(
                        value["accept_content_types"],
                        "accept_content_types",
                    )
                ),
                required_post_headers=tuple(
                    McpStandardHeader(header)
                    for header in _string_list(
                        value["required_post_headers"],
                        "required_post_headers",
                    )
                ),
                required_get_headers=tuple(
                    McpStandardHeader(header)
                    for header in _string_list(
                        value["required_get_headers"],
                        "required_get_headers",
                    )
                ),
                name_header_methods=tuple(
                    _string_list(value["name_header_methods"], "name_header_methods")
                ),
                authentication_required=_bool(
                    value["authentication_required"],
                    "authentication_required",
                ),
                origin_validation_required=_bool(
                    value["origin_validation_required"],
                    "origin_validation_required",
                ),
                local_bind_policy=_text(
                    value["local_bind_policy"],
                    "local_bind_policy",
                ),
                message_encoding=_text(value["message_encoding"], "message_encoding"),
                remote_registration=_text(
                    value["remote_registration"],
                    "remote_registration",
                ),
            )
        except ValueError as error:
            raise InvalidMcpStreamableHttpContract(str(error)) from error


def _validate_endpoint_path(value: str) -> None:
    if not isinstance(value, str) or not value.startswith("/") or value == "/":
        raise InvalidMcpStreamableHttpContract(
            "endpoint_path must be an absolute non-root path"
        )
    if "?" in value or "#" in value or any(character.isspace() for character in value):
        raise InvalidMcpStreamableHttpContract(
            "endpoint_path must not include query, fragment, or whitespace"
        )


def _text(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise InvalidMcpStreamableHttpContract(f"{field} must be text")
    return value


def _bool(value: object, field: str) -> bool:
    if type(value) is not bool:
        raise InvalidMcpStreamableHttpContract(f"{field} must be bool")
    return value


def _string_list(value: object, field: str) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(element, str) for element in value
    ):
        raise InvalidMcpStreamableHttpContract(f"{field} must be a string list")
    return value
