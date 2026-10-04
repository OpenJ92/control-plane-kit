Source: [current_schema_contract.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema_contract.py).
Maintain this companion alongside its source.

The frozen semantic contract mirrors the complete fresh SQL, including health
preparations, managed command receipt provenance and graph receiver storage:
43 relations, 549 columns, 440 constraints, 142 indexes and 110 foreign keys.
Receiver storage adds two relations, twenty-three columns, seven keys/indexes,
seven checks and fifteen restrictive foreign keys. Only the original-binding
receiver reference is deferred, allowing atomic introduction/binding creation.
The health table has eighteen columns, sixteen checks, four
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

B1 / #1923's exact catalog adds two relations, 23 columns, nine constraints and
six indexes for immutable configuration protection. The current totals are
46 relations, 580 columns, 458 constraints and 153 indexes, including 115 foreign
keys. The SQL, contract hashes, table atlas and schema tests move together;
these counts do not authorize migration or reset of a retained namespace.

B2 E7 adds the original workspace-initialization leaf: one relation, nine
columns, nine constraints (including four restrictive foreign keys) and one
primary index. Prospective totals are 47 relations, 589 columns, 467 constraints,
154 indexes and 119 foreign keys. The exact literal/SQL and owning assertions
move together. Literal metadata hashing is source authoring only; independent
owning Docker catalog/hash verification remains pending on draft PR #1926.

The E1/E2 typed-original and acceptance schema adds two relations, 35 columns,
29 constraints and eight indexes. Prospective totals are 49 relations, 624
columns, 496 constraints, 162 indexes and 136 foreign keys. Both fixed hashes and
metadata assertions move with the literal. This checkpoint remains unvalidated
until the owning PostgreSQL catalog and static-law tests run.

The first owning run exposed a literal-ordering defect: the verifier compares
ordered arrays, while the new entries were initially prepended. The correction
orders relations by name, columns by relation and physical SQL position, and
constraints/indexes by relation and name. Counts and SQL are unchanged; the
literal fingerprint and expected table atlas move with that source correction.
The failed run provides no database-backed behavior credit. A focused rerun is
required to establish actual catalog equality and detect any further mismatch.

#1931 source checkpoint (unvalidated): D1 adds the exact seven-column invocation completion relation, primary key, source and outcome foreign keys, scalar checks and six-column outcome commitment index. Original event identities stay in the existing outcome owner. Exact empty-install/current-verification/drift-refusal policy is unchanged; this is not a migration.

#1931 source3 corrects the added literal entries to the established catalog
ordering. Source2 (`fc0198c6`) prepended them and therefore necessarily failed
the order-sensitive exact-contract comparison during fresh installation; all
18 selected methods stopped in setup and earned no behavioral credit. The
strict verifier and SQL are unchanged. Current totals are 50 relations, 631
columns, 503 constraints, 164 indexes and 138 foreign keys. The fixed digest,
schema-test metadata and atlas move together. Source-literal transcription
reproduced the accepted prior digest before authoring the new digest; this is
not executable package validation. The ordinary focused gate remains required
to establish catalog equality and expose any additional mismatch.
