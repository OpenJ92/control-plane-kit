Source: [control-plane-kit-core/tests/test_revision_history_contract.py](../../../../control-plane-kit-core/tests/test_revision_history_contract.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

One test checks the preparation and attempt history collections for an exact saved draft revision. It compares their GET path templates, READ scope, READ_ONLY safety and MCP tool names, then requires workspace-scoped paged projection declarations with ten-item maxima. These are Core contract values assembled through HTTP, MCP and projection factories; no route, store or tool is invoked.

The [projection owner](../src/control_plane_kit_core/operations/projections.py.md) supplies the two smaller page limits. Selected actual [HTTP route declarations](../../../../control-plane-kit-core/src/control_plane_kit_core/operations/http.py) and [parity bindings](../../../../control-plane-kit-core/src/control_plane_kit_core/operations/parity.py) supply the names and paths. Their constructors may reject incoherent composition, but this test has no explicit negative cases, actual cursor traversal, response-byte assertion, authorization exercise or history-association proof. Its local route dictionary is navigation, not a stand-alone duplicate-detection test.

Full 33-line owner read with selected exact HTTP/parity entries and previously fully reviewed projection/MCP contracts. No executable validation or concrete adapter conformance is claimed.
