Source: [control-plane-kit-core/tests/test_node_authority_delivery.py](../../../../control-plane-kit-core/tests/test_node_authority_delivery.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Per-instance authority survives graph and request transformations

The fixture instantiates the same product as controller, database and custody
nodes, assigning delivery only to the controller instance. Tests check that
siblings and the reusable product contract receive no inferred declaration.
Graph encoding omits absent/empty delivery lists, preserves a nonempty delivery
through round trip, and rejects malformed or duplicate declarations and unknown
delivery fields.

Adding/removing delivery changes graph identity and produces reconciliation for
the recipient. Runtime product material carries the exact declaration through
descriptor and intent projection; changed declaration changes intent identity.
Two private reference handles remain distinguishable in redacted diff evidence,
while their text is hidden. Ordering and malformed material vectors exercise
the selected value/codec boundaries.

Full 199-line file, graph fixture and consequential product-instance,
materialization, runtime-material and recipient code read, with prior graph/
diff/compiler and intent owners as context. The
[empty-delivery fixture](fixtures/node_authority_empty_graph.json.md) protects a
historical canonical descriptor baseline; its filename does not mean a graph
with zero nodes. These tests do not prove a runtime mount, process permissions,
TLS connectivity, registry admission or automatic authority propagation.
