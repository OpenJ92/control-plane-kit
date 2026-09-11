Source: [control-plane-kit-operations/src/control_plane_kit_operations/postgres/node_control_attempt_store.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/node_control_attempt_store.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This adapter appends and reconstructs
[NodeControlIntendedAttempt](../node_control_attempts.py.md) evidence. Its four
methods lock workspace/request identity, insert a value, read by attempt ID and
read optionally by workspace/request ID. It exposes no update/delete/repair API,
does not install schema and does not commit or roll back. Insert-only describes
this API, not a database prohibition on direct SQL changes; the selected corruption
tests deliberately modify retained rows and joined witnesses.

lock_request_id validates both selectors before issuing a transaction advisory
lock over hashtextextended of the node-control-attempt:workspace:request string.
This coordinates callers following that protocol. add and the read methods do not
implicitly take it. A caller must keep the same transaction open across lock,
lookup and insertion to serialize preparation. The lock is not caller authentication,
a worker lease, an effect receipt or a guarantee that unrelated writers participate.
Normal get/get_by_request_id queries have no explicit row-lock clause.

add builds 27 insert values: retained identifiers, canonical request and two grant
byte strings, their three digests, duplicated issuer/key/JTI scalars, encoded
intended timestamp and semantic fingerprint. It sends one parameterized INSERT
and returns the supplied value after statement success. The type annotation is
not an exact runtime type check; _record_values reads its attributes/properties.
No duplicate lookup, semantic comparison, signing or authority refresh happens
inside add. Repeated insertion, including the same value, is not a successful
idempotent operation at this boundary.

The selected actual
[schema](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema.sql)
has attempt_id as primary key, unique workspace/request identity, and separate
unique transit issuer/JTI and workload issuer/JTI pairs. Any UniqueViolation maps
to the same fixed NodeControlAttemptConflict; the adapter does not identify which
unique constraint collided. Other IntegrityError failures map to a fixed references
unavailable NodeControlAttemptError. Those mappings occur outside the handlers and
discard exception chains. They do not recover an aborted PostgreSQL transaction:
rollback remains the caller's responsibility. Attribute/encoding failures and
other database exceptions outside these catch sets can propagate.

Foreign keys bind the workspace, projection to authored source and workspace, and
each retained key/authorization to that workspace. They do not bind the attempt to
the workspace's mutable current pointer. gateway_runtime_id has bounded text shape
without a runtime foreign key here. Byte bounds and digest/identifier grammars are
checked in schema, while deeper canonical/content relationships are checked on
decode. Database references alone do not prove active authority or valid policy.

Both reads validate selectors and use the same SELECT: 27 attempt columns plus
14 boolean relational witnesses. Four inner joins connect both key registrations
and both authorizations by ID and workspace. For each family, witnesses compare
key purpose/issuer/key ID, authorization intent/actor/correlation, and authorization
secret reference against the key's private reference. Missing joined truth removes
the row from the query. get then raises KeyError containing the validated attempt
ID; get_by_request_id returns None. A present row with a false witness is corrupt.
This distinction is not a universal redaction contract for lookup errors.

_decode_row requires exactly 41 columns and every witness to be the exact True
value. It converts the three retained wire columns to bytes and calls actual Core
[request/workload codecs](../../../../../../control-plane-kit-core/src/control_plane_kit_core/node_control.py)
and the [transit codec](../../../../../../control-plane-kit-core/src/control_plane_kit_core/node_control_transit.py).
Their selected canonical decode methods require bounded bytes and equality with
re-encoded canonical bytes, so adding JSON whitespace is not silently normalized.
The store independently hashes each original byte string and compares its stored
digest, then checks duplicate request/workspace/graph and grant attempt/issuer/key/
JTI scalars. It reconstructs the public attempt, including timestamp decoding and
all aggregate cross-value laws, and recomputes the semantic fingerprint.

Any ordinary Exception in that reconstruction becomes fixed
NodeControlAttemptCorrupt without cause/context. No partly reconstructed value is
returned and no repair is written. This detects selected inconsistency; it is not
a cryptographic integrity seal against an actor who can coherently rewrite all
related database evidence. Canonical request bytes and full dataclass round-trip
equality serve different checks from the smaller semantic replay fingerprint.

These joins do not require active key status, recompute key registration identity
or public fingerprint, or inspect reference/provider registration rows. They also
do not compare authorization operation/session/run/activity/effect/probe fields,
grant time against a clock or current workspace pointers. The later
[signing-authority reload](../node_control_signing_authority.py.md) owns stronger
current-authority checks. Retained reads can continue after graph advancement,
yet can become corrupt if a joined key/authorization witness changes. The row is
historical evidence whose reconstruction still consults those related records.

The selected actual
[intent-service replay branch](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/node_control_intents.py)
uses lock, lookup and fingerprint comparison in one UoW, returning the existing
attempt on semantic replay. Fresh preparation inserts before requesting commit.
The actual
[PostgresUnitOfWork](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py)
shares a connection through its bundle and commits on successful requested exit,
otherwise rolling back and closing. Direct autocommit use commits the INSERT as
one statement and would release an advisory transaction lock before a later
lookup. No attempt status, delivery count, execution event or compensation record
is maintained by this store.

Selected actual
[tests](../../../../../../control-plane-kit-operations/tests/test_node_control_attempts.py)
read through a newly opened PostgreSQL connection and compare the whole attempt
value. They reject appended whitespace, noncanonical bytes with recomputed digest,
different canonical command/grant bytes with recomputed digest, wrong digests,
duplicate scalar drift and graph/attempt/request substitutions. Four same-workspace
key/authorization substitutions and 16 separate joined-row mutations exercise
relational witnesses. These use direct SQL fixtures with empty graph descriptors,
constructed registrations/public fingerprints and no complete admission workflow;
they do not establish active signing capability or a deployed graph.

The selected rollback test proves an uncommitted insert disappears, locked lookup
returns the existing value, another attempt sharing request identity conflicts,
and graph advancement leaves the historical graph ID readable. Its conflicting
candidate also retains issuer/JTI pairs; it does not isolate the workspace/request
unique constraint as the sole reason for rejection. The two-connection lock test
observes the contender active with wait_event_type Lock in pg_stat_activity before
the first connection commits, then requires successful acquisition. It is a real
coordinated database lock check, not an end-to-end simultaneous preparation test,
process restart or proof of at-most-once delivery. Selected error helpers assert
bounded messages/repr, canary omission where supplied and absent exception chains.

Read depth: full 204-line store and 214-line owner; selected test ranges 159..754,
814..990, 1036..1174 and 1229..1490. The complete 1490-line test module and its
companion remain a separate review batch. Selected schema table/check/key/FK
declarations, actual Core canonical decoders and intent replay caller were read,
with retained full UoW/timestamp codec and signing-authority context. No executable
validation, database setup, credential/private-key access, source/pin change,
provider/runtime action or publication occurred. Documentation adds no security
surface; operational callers still own transactions, access and delivery history.
