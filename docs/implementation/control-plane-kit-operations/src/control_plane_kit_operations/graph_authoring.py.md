Source: [graph_authoring.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/graph_authoring.py).
Maintain this companion alongside its source.

Desired-graph authoring retains its existing caller-owned transaction, revision
checks, registered product requirements and authored/realized graph records.

`validate_proposed_graph_id` validates explicitly supplied names through Core's
public `NodeControlGraphReference(GRAPH_REVISION, value)` law. This admits exact
strings of 1–128 ASCII identifier characters and rejects public credential or
endpoint material. Refusal is fixed and detached; the name grants no authority.
Omitted historical IDs do not pass through this new admission step.
`GraphIdentityConflict` is the inward domain error used by the PostgreSQL graph
store for immutable-ID collision. This module imports no backend and performs
no IO at import time. Existing authoring accepts the admitted name through its
unchanged explicit `graph_id` seam and never rewrites supplied graph bytes.

`product_reference_in_node(node)` is the authoritative pure extraction of an
optional pinned product reference. It retains the existing metadata pair,
namespace/name/integer-revision normalization, digest validation and error family.
`product_references_in_graph(graph)` delegates per node and returns the same sorted
unique aggregate. Alias revisions such as `001` still normalize to revision 1.

Fresh management planning needs the per-node result so two nodes referencing one
registered product cannot hide an omitted management declaration behind the
aggregate's deduplication. The parser neither checks catalog status nor invents
authorization. Its candidate-bearing errors stay internal to the planning and
execution predicates, which return fixed refusal decisions. Other authoring
callers retain the original parser behavior.

Tests in `test_management_planning_admission.py` protect normalized aliases,
ordering, deduplication, absent/malformed metadata, and per-node management
projection checks. Existing authoring and execution tests protect aggregate
consumers. No schema, credential, provider access or history mutation is added.
