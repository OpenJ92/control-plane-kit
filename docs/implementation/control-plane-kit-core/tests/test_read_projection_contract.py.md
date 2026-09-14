Source: [control-plane-kit-core/tests/test_read_projection_contract.py](../../../../control-plane-kit-core/tests/test_read_projection_contract.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Read projection contract tests

Six pure tests cover the [projection vocabulary](../src/control_plane_kit_core/operations/projections.py.md) and its declared HTTP/MCP correspondence. The large canonical assertion fixes the ordered operation/kind/schema/policy/workspace/paged tuples, including the revision-history operations. It separately checks READS service, READ scope and READ_ONLY safety for every canonical entry. It does not assert every numeric page limit; overview and gateway timeline have explicit 100-limit checks elsewhere in this file.

The overview test compares its single projection to a GET /workspaces/{workspace_id}/overview HTTP declaration with a 65536-byte schema reference and get_operator_overview MCP binding. The final parity test compares every operation ID and response-schema name against the parity factory. These are value-level composition checks, not HTTP requests, tool calls, authorization tests or measured response sizes.

Descriptor round-trip checks the canonical set, rejects an extra top-level key and searches its repr for selected server/store/token terms. The shared [security assertion](../../../../control-plane-kit-core/tests/contract_security_assertions.py) recursively rejects a finite set of normalized key names and five value markers. Neither check establishes arbitrary-input redaction or secrecy of actual read results.

Two negative constructor examples reject a PLANNING service role and a paged contract with no maximum. They do not cover every malformed descriptor or bound, boolean page sizes, noncanonical ID-to-kind/policy assignments, missing/duplicate set membership or workspace=False. The production constructors and canonical factory have distinct admission obligations; the test title alone is not evidence for unasserted laws.

Full 515-line test, full 611-line owner and full shared assertion read, with selected actual HTTP and parity dependencies. No executable validation or concrete adapter/store audit was performed for this note.
