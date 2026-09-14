Source: [control-plane-kit-core/src/control_plane_kit_core/operations/mcp.py](../../../../../../control-plane-kit-core/src/control_plane_kit_core/operations/mcp.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# MCP transport declaration

McpStreamableHttpContract describes CPK's hosted MCP transport contract as a frozen value. It does not implement an MCP server, register a client, parse JSON-RPC, authenticate a request or validate an Origin. Its required security and transport fields are obligations for a concrete consumer, not observations that a running endpoint satisfies them. This is the contract encoded by this source revision, not an independent claim about every MCP implementation or specification version.

The default path is /mcp. Canonical ordered tuples require POST then GET, JSON then event-stream response types, POST Accept/protocol-version/method headers and GET Accept. Mcp-Name is declared for tools/call, resources/read and prompts/get. Authentication and Origin validation must be literal True; localhost-only, json-rpc-utf8 and url-plus-client-auth are fixed labels. The value carries no credential, session identifier or process configuration.

The protocol check uses identity with Protocol.MCP_STREAMABLE_HTTP. An independently constructed equal Protocol value therefore does not meet this constructor check; the [Protocol decoder](../../../../../../control-plane-kit-core/src/control_plane_kit_core/types.py) returns canonical values. This MCP protocol is a distinct TCP/application pair from HTTP even though both permit http/https endpoint schemes.

Path validation requires a slash-prefixed non-root string and rejects query, fragment and whitespace. It imposes no length limit, general control-character rejection or path normalization. Enum tuples are compared by equality rather than element type: matching plain strings can compare equal to StrEnum members in direct construction, while descriptor() later expects their .value attributes. Use typed constructor inputs or the descriptor decoder; frozen annotations alone do not establish universal runtime type admission.

The exact-key decoder requires list-of-string collections, converts enum elements, decodes the protocol and then applies constructor laws. It rejects extra keys and wrong kind; it does not accept arbitrary iterable collections or impose a serialized-byte limit. ValueError text is re-raised with its cause, so invalid enum/protocol input can remain in error text. This is not a redaction boundary.

The [process contract](process.py.md) composes this declaration with readiness dependencies; its actual MCP dependency guard checks this type. Neither that composition nor exporting the contract establishes a live transport. Full 269-line owner and full 124-line test read, with selected actual Protocol canonicalization/compatibility and process consumer branches. [Tests](../../../tests/test_mcp_streamable_http_contract.py.md) cover the canonical declaration and selected rejection laws. No external transport or executable validation was performed.
