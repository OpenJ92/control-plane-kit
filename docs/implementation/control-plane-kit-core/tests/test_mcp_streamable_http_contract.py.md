Source: [control-plane-kit-core/tests/test_mcp_streamable_http_contract.py](../../../../control-plane-kit-core/tests/test_mcp_streamable_http_contract.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# MCP contract test evidence

Five pure tests exercise the [MCP declaration](../src/control_plane_kit_core/operations/mcp.py.md) through its operations import surface. Protocol assertions distinguish the TCP/MCP application value from HTTP while retaining http/https endpoint schemes. Default-contract assertions name methods, response types, POST and named-request headers, authentication, Origin validation and local binding policy.

The descriptor test checks the complete expected mapping for a custom /control/mcp path and round-trips it. It rejects one extra key and a POST-only methods list. The path test rejects empty, relative, root, query-bearing and fragment-bearing values. Those examples do not cover whitespace, arbitrary control characters, every collection/type branch, constructor protocol identity or the equality-admitted plain-string tuple case.

The last test searches the canonical descriptor's repr for five forbidden process/stdio/session strings. This is a finite vocabulary check on one value, not an import-isolation proof, a universal disclosure test or evidence that a hosted server has no sessions. Security flags are checked as data; no authentication, Origin, streaming or network behavior runs here.

Full 124-line test and full 269-line contract owner read, with selected actual Protocol and process-consumer branches. No executable validation was performed, and these tests alone cannot establish concrete transport conformance.
