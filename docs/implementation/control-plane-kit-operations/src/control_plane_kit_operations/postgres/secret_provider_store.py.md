Source: [control-plane-kit-operations/src/control_plane_kit_operations/postgres/secret_provider_store.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/secret_provider_store.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

SecretProviderStore, SecretReferenceStore and SecretUseAuthorizationStore retain
admission/lifecycle and correlated authorization truth on one supplied PostgreSQL
connection. The [service owner](../secret_providers.py.md) supplies scopes,
cross-record policy and commit requests. These stores do not contact providers,
resolve credentials, attest existing handles or record custody/resolution success.
Endpoint and credential fields contain opaque references, not endpoint URLs or
resolved values.

Both registration stores require an active typed candidate and encode admitted_at
before acquiring their transaction advisory lock. The lock key scopes provider
registration by workspace/provider ID and reference registration by workspace/
secret handle. They then lock an existing candidate ID, if present, and return
it unchanged regardless of its current lifecycle state. That shortcut trusts
factory-derived identity; the store does not compare all candidate semantics
after finding the same ID. Otherwise they lock the active row and return matching
admission semantics, preserving original actor/time.

New differing admission requires supersession evidence when any history exists.
The named target must exist in the workspace and share provider ID or secret
handle. If an active row exists, that exact active ID must be the target. With
no active row, the code accepts a matching historical target without requiring
it to be the latest history row. Supersession marks the old active row superseded
and inserts the new row in the caller's transaction. A failed or uncommitted UoW
rolls both changes back. No provider credential is rotated by this mutation.

The [schema](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema.sql)
adds primary registration IDs, partial uniqueness for active workspace/provider
or workspace/handle, and workspace-scoped foreign keys for supersession and
reference-to-provider admission. It enforces selected reference shapes,
active/revoked/superseded status and revocation-field consistency. These foreign
keys establish retained identities, not active provider state or allowed
prefix/intent relationships. Direct reference-store registration does not perform
the service's provider policy check. Advisory locking supports convergent
same-identity registration among callers using this store, not an assurance that
every other mutation participates in the same lock protocol.

Provider revoke_active validates time and locks the active row. Without an active
row it returns the last history row only if that row is revoked; otherwise it
raises not-found. Reference revoke locks the exact registration, replays revoked,
accepts active, and rejects superseded. Neither revocation takes the registration
advisory lock. Both update status with actor/time and reread; no dependency rows
or secrets are deleted. Provider revocation/supersession does not cascade into
reference status. Historical registration getters remain available; active
reference selectors alone do not prove their provider is still active.

The ordinary provider/reference selectors use plain reads. The explicit
require_active_registration_for_update and get_active_for_update variants hold
row locks within the caller transaction. Secret-use authorization combines these
with lock_correlation before inserting or returning a receipt. The use store's
add method validates time and inserts; it does not implicitly acquire the
correlation lock or validate current admission. Its get and for_correlation
selectors are workspace scoped. The application checks fingerprint equality;
the schema enforces unique workspace/correlation and authorization ID.

Authorization foreign keys independently bind provider and reference registration
IDs to the same workspace. They do not prove that the selected reference row
pins that particular provider ID, or that all contextual operation/run/activity
identities correspond to actual records. The schema checks their selected
grammars, closed use intents and digest/authorization-ID shapes. Row decoding
validates AuthorizedSecretUse shape but does not recompute its fingerprint or
ID. This owner offers no authorization update/delete API, though the table is
not made immutable against arbitrary direct SQL by that API choice.

Provider active_page seeks ascending provider_id; reference active_page seeks
ascending registration_id. Both use limit+1 and IdentityReadCursor, with no count,
offset or snapshot. Full reference list_active instead orders secret_reference;
do not assume it shares page ordering. Full active/history lists are unbounded,
and history orders admitted_at then registration_id, not causal commit sequence.
Revocations and supersessions can change later pages.

All row constructors decode UTC timestamps, typed references/intents/status and
the value owner's metadata rules. They do not recompute deterministic admission
IDs, revalidate provider policy or inspect external state. Normalization can sort/
deduplicate persisted prefixes/intents rather than reject their noncanonical
ordering. Driver/decoder failures are not universally caught or redacted here.
Stores commit nothing independently; lifecycle rows and use receipts retain
local history without appending provider results or general activity events.

Read depth: full 939-line owner, full 1,307-line language/service and
[1,096-line tests](../../../tests/test_secret_providers.py.md), selected actual
Core grant/reference and current-schema constraints, plus retained complete
UoW/temporal and read-page contracts. No executable tests, database inspection/
mutation, credential resolution or provider effects accompanied these notes.
