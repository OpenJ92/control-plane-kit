Source: [control-plane-kit-operations/src/control_plane_kit_operations/postgres/gateway_probe_store.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/gateway_probe_store.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

GatewayProbeStore persists and selects GatewayProbeAttempt receipts on a supplied
PostgreSQL connection. The [command service](../gateway_probes.py.md) owns the
intent/effect/completion sequence and requests commits through its unit of work;
this store neither commits independently nor performs a network probe. Rows
retain grant metadata and JTI, not private key bytes, signed compact grants or
secret-resolution grants. Their status and evidence describe recorded attempts,
not current target health.

lock_request_id takes a transaction advisory lock over the workspace/request
coordinate. The command service calls it before get_by_request_id and add; add
does not acquire it implicitly. The
[current schema](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema.sql)
also makes probe ID primary, JTI unique and workspace/request unique. It checks
closed access path/kind/status, lowercase request digest, nonnegative issued
epoch and increasing expiry, and requires both completion fields absent for
intended or present for terminal rows. Separate foreign keys require workspace
and graph existence; they do not themselves prove the graph belongs to the
workspace or validate gateway/target/key coordinates. Those stronger admission
checks are service responsibilities. The table's time check does not impose the
Core grant's 300-second lifetime limit.

add encodes requested_at and optional completed_at before its own SQL access,
inserts the record and returns the supplied value. get reads by probe ID without
a workspace argument; get_by_request_id scopes the request identity by workspace.
The read projection must enforce workspace ownership for detail access. All
selected rows reconstruct closed enums, canonical bounded evidence and the
attempt value, decoding aware database timestamps to canonical UTC text. Driver
or malformed-row errors are not universally normalized by this store.

complete rejects intended as the requested result, then validates completed_at
before selecting the row FOR UPDATE. Missing identity raises KeyError. A row
already terminal returns its stored receipt without comparing the newly supplied
status, time, result code or evidence; the new timestamp must nevertheless pass
admission first. An intended row is updated with a status predicate and RETURNING;
failure to return the row raises a conflict. Locking and the transaction protect
this local fold, not the earlier external effect. There is no repair, redispatch,
retention policy or deletion command in this owner.

page accepts the GATEWAY_PROBES ReadPageRequest, filters workspace, and seeks
strictly below (issued_at, probe_id), ordering both descending. These are native
bigint epoch seconds and text identities, not requested_at/completed_at or
timestamp conversions. The matching workspace/epoch/identity index supports this
shape. One LIMIT limit+1 query provides the lookahead row; ReadPage exposes at
most limit items and the last exposed cursor only when lookahead exists. There
is no count, offset or snapshot token. A later committed head needs a fresh
traversal, while a new row below the cursor can appear during continuation.

The selected
[read-page values](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/read_pages.py)
validate collection/scope/cursor congruence and limits; EpochReadCursor renders
its epoch as decimal text to preserve integer precision. ReadPage validates the
candidate envelope but relies on the selector for ordering. BoundedEvidence's
canonical-size and secret-shaped-key checks do not certify arbitrary evidence
values as non-sensitive. The store applies those shared record rules, without
independently inspecting network responses or logging provider exceptions.

Read depth: full 252-line owner and full
[command tests](../../../tests/test_gateway_probes.py.md) and
[paging tests](../../../tests/test_gateway_probe_read_pages.py.md), full command
service, selected current-schema table/constraints/index, evidence and read-page
values; previously read complete UoW and temporal codecs. This is documentation
only; no database, test, probe or cleanup operation was run.
