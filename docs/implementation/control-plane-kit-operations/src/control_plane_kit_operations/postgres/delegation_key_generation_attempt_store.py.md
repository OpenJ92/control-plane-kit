Source: [delegation_key_generation_attempt_store.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/delegation_key_generation_attempt_store.py).

The store shares its caller's connection and never commits. Exact workspace,
command kind and request identify one immutable intent. Reserve-or-read conflicts
on changed fingerprint, including full actor identity. Row locks and expected
fences serialize initial and explicit reconciliation claims; one invocation makes
one claim attempt. There is no lease, timer or automatic redispatch.

Native state/fence/time/admission columns constrain closed typed intent and
receipt descriptors. Reads reconstruct exact values and fingerprint, bound JSON
before transfer, and verify admitted references against stored key/public identity
and provider-version metadata. Historical admission remains readable through
later key/reference lifecycle changes. Schema reentry scans these records in
bounded pages; corrupt semantics refuse rather than repair.

`POSTGRES_READ_CARDINALITY.toml` classifies `validate_current_rows` as an exact
verifier: strict `(workspace_id, request_id)` keyset pages with `LIMIT 100`,
bounded descriptors, and complete exhaustion before schema admission succeeds.
`OPERATIONS_TABLE_ATLAS.md` records the new relation and its three restrictive
foreign keys, admission transaction and restore dependencies. These inventories
describe the owner; they add no query or mutation behavior.

The new relation installs only through the existing object-free current-schema
program. A nonempty older namespace without it is incompatible and returns the
existing bounded reset-required refusal without performing reset or migration.
Foreign keys retain the existing default NO ACTION behavior; no independent
cleanup or retention process is introduced. Core #2024 tests own the durable concurrency,
receipt retention and atomic admission laws; #2025 owns provider execution.
