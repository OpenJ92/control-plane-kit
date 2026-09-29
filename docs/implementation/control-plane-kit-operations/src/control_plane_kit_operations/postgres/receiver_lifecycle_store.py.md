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
