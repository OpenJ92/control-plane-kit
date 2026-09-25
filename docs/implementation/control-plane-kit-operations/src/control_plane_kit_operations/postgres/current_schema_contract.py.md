Source: [current_schema_contract.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema_contract.py).
Maintain this companion alongside its source.

The frozen semantic contract mirrors the complete fresh SQL, including health
preparations and managed command receipt provenance: 41 relations, 526 columns,
411 constraints, 135 indexes and 95 foreign keys. The health table has eighteen columns, sixteen checks, four
primary/unique constraints and eight restrictive owner foreign keys. Existing
health signing vocabulary and rotation restrictions remain unchanged.
The optional receipt `managed_intent` adds one bounded JSON column and check;
legacy null receipts retain their existing fingerprint and decoding contract.

The fixed fingerprint is SHA-256 over ASCII compact sorted-key JSON of the
literal's dataclass fields under the existing domain and format-version envelope.
It is not the JCS health-preparation codec. Array ordering preserves catalog
relation/name order and physical column/constraint/index ordinals. The ordinary
native tests independently compare fixed goldens, actual PostgreSQL semantics,
SQL bytes and the table atlas. No hash generator or schema introspector is added
to the runtime. The authoring transcription reproduced the accepted prior hash
before calculating the new literal's prospective hash.

This is a fresh-store contract, not a migration program. Exact-current existing
stores receive bounded semantic row verification only. Incompatible schema/data
refuses without repair, reset or backfill; callers retain transaction ownership.
