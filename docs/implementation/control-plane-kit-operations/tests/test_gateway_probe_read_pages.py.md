Source: [control-plane-kit-operations/tests/test_gateway_probe_read_pages.py](../../../../control-plane-kit-operations/tests/test_gateway_probe_read_pages.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This owner separates probe pagination laws into SQL-shape recording tests,
real-Postgres traversal tests, fake-store service/adapter tests and retired-API
assertions. The [probe store](../src/control_plane_kit_operations/postgres/gateway_probe_store.py.md)
owns native ordering; the read projection maps receipts and the server adapter
admits route arguments and caller authority. The tests describe receipt history,
not live health, probe execution or signature verification.

Recording connections assert a single workspace-scoped query with strict
(issued_at, probe_id) < seek, both fields descending and limit+1. The first page
omits the seek. Assertions exclude offset/count and epoch-to-timestamp conversion;
the seek example preserves integer 9,007,199,254,740,993 in parameters. These empty
recorded results establish generated SQL shape and parameter precision, not
database bigint boundary behavior or a query planner's performance.

The PostgreSQL class requires CPK_OPERATIONS_TEST_DATABASE_URL, installs schema,
truncates workspaces with CASCADE, and directly inserts a workspace and graph row
whose descriptor is empty JSON. It adds synthetic intended attempts through the
store, without graph admission, key registration or dispatch. Empty and final
pages, equal-second ID tie-breaking and the last exposed cursor are checked with
four rows. A separate writer then commits a new head and a new tail between page
reads: continuation excludes the new head, includes the new tail, and does not
repeat already seen identities; a fresh traversal sees the head. This deliberately
proves continuation over changing committed data, not a stable snapshot or a
simultaneous writer/reader race. These setup mutations require a disposable test
database and are not operational inspection commands.

Fake workspace/probe stores and a no-op UoW isolate projection and adapter laws.
InstanceReadService.gateway_probe_timeline must take only self and request,
invoke the page selector once and expose workspace_id, kind, limit, items and
next_cursor, without offset, total or has_more. HTTP/MCP-shaped first and
continuation requests yield identical envelopes; detail still returns the exact
attempt descriptor. Legacy list/count methods retained on the fake are not proof
that production continues to expose them: the final class separately asserts
their absence from GatewayProbeStore and retirement of FocusedCollectionReadModel
from the read-services module and package exports.

Input negatives cover offset, unknown fields and limit 101, including an MCP
offset case. They expect 400 and omission of a chosen rejected-value marker.
An authorized malformed epoch cursor containing decimal text 01 must fail 400
with neither cause nor context and before creating a UoW. A principal with no
workspace grant receives 403 before cursor interpretation or UoW acquisition,
even with hostile-shaped cursor contents. These are precise local admission
precedence and selected redaction laws, not exhaustive malformed-input coverage,
foreign-workspace row isolation or live authentication/HTTP/MCP transport tests.

Read depth: full 520-line owner, full 252-line store and full
[command suite](test_gateway_probes.py.md), with the full command owner and
previously read gateway-security projection companion; selected EpochReadCursor,
ReadPageRequest/ReadPage and schema contracts. The fake-store cases exercise
the adapter by calling handle directly; adapter implementation was not fully
re-read for this companion. No suite or database operation was executed, and
this documentation adds no security, persistence or runtime behavior.
