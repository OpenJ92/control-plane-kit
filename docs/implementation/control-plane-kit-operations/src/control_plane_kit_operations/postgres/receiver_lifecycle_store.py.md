Source: [receiver_lifecycle_store.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/receiver_lifecycle_store.py).
Maintain this companion alongside its source.

This private graph-store helper owns receiver structural persistence on the
caller's existing connection. It never commits, calls a provider, allocates
identity, resets a schema, or supplies admission authority.

Writes require a same-owner/workspace lifecycle guard with the current
transaction identity. Exact stored graph/projection records supply material.
Reservation uses globally sorted receiver IDs and unique-key arbitration;
replay checks immutable origin without reading a foreign owner's row.
Binding writes verify introduction scope and return a complete rederived set.
Acceptance and retirement are paired write-once witnesses with exact replay.

Reads bound copied text and JSON before transport. Binding reads limit returned
rows to derived membership plus one, refusing missing, extra or substituted
material instead of exposing partial truth. Current verification uses bounded
keyset pages and performs no repair. Structural action/session/workspace joins
are checked here; C owns action semantics and the full admission transaction.

Current verification enumerates binding projections and all introduction origins,
not arbitrary generic graph history. It cannot discover an entirely absent later
non-origin binding set; C must establish complete membership atomically at every
supported publication entry. Direct explicit member reads remain complete.
Bounded member results are normalized in Python before comparison so database
text collation cannot change material equality.

The authoring snapshot adds fixed scoped selectors with one shared ledger:
1,024 SELECTs, 4,096 returned rows and 8 MiB scalar transport. Every query
reserves its upper bound before execution. Int4/null length probes reserve
512 bytes per possible row; guarded text fetches reserve observed lengths and
boolean flags, then charge actual values before JSON decode. SQL repeats cell,
row-count and aggregate-column guards. Descriptor cells are at most 1 MiB,
introducing payloads 64 KiB and ancillary text 2 KiB. Arbitrary record metadata
is excluded. Exact complete graph/material cache hits reuse the same budget.

Original action attribution is one shared pure validator used by this bounded
reader and the existing graph owner. Original draft-revision and recorded
acceptance witnesses are bounded boolean existence reads in the same snapshot.
No execution/run/outcome traversal recomputes acceptance. This helper does not
authorize a read: the dedicated application service does so before snapshot
entry. A legacy receiver-free draft without a persisted identity projection
returns an explicit absent projection and creates no record.

During a B1 configuration command, exact receiver introductions, graph material,
and binding reads join the active configuration ledger. Their existing typed
constructors and complete-membership checks remain unchanged. Legacy authoring
reads keep their existing ledger; this bridge neither creates origins nor
substitutes accepted-current evidence.
