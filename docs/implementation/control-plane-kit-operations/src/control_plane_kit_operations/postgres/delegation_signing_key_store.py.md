Source: [control-plane-kit-operations/src/control_plane_kit_operations/postgres/delegation_signing_key_store.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/delegation_signing_key_store.py).
Maintain this document alongside its source file. Recheck the imported value,
temporal, page and schema contracts when the store changes.

This 469-line adapter stores delegation public-key identity, private-key references
and lifecycle evidence on one caller-owned Postgres connection. Its sole public
export is DelegationSigningKeyStore. It neither obtains private key bytes nor
generates keys, signs requests, validates signatures, contacts a secret provider or
publishes verifier configuration. A registered or active row is durable selection
material, not proof that a remote workload has accepted that key.

The store bundle constructs this adapter with its shared connection. The
[unit of work](unit_of_work.py.md) owns commit, rollback and connection lifetime.
The adapter never commits or rolls back independently. Its multi-statement mutations
and transaction-scoped locks require the caller's transaction to remain open across
the whole operation; autocommit does not provide that grouping. Returned values are
readback evidence inside that transaction, not independent proof of commit.

_SELECT fixes an 18-column row layout: registration/workspace/purpose/issuer/key ID,
algorithm/public PEM/fingerprint/private reference, admission actor/time/status and
activation/retirement/revocation actor-time pairs. _row reconstructs the imported
values for every selector rather than returning unchecked database tuples.

The actual [Core public-key owner](../../../../../../control-plane-kit-core/src/control_plane_kit_core/delegation_keys.py)
provides four purposes and ED25519 as the only algorithm. DelegationPublicKey
normalizes bounded ASCII public-PEM-shaped text and fingerprints the normalized text.
It does not parse DER, validate the base64 body or prove mathematical Ed25519 key
validity. Its descriptor and repr omit PEM, but the public_key_pem attribute remains
available to internal consumers. _row compares its recomputed fingerprint with the
stored fingerprint and raises DelegationSigningKeyConflict on disagreement.

_row then constructs SecretReference, purpose/status enums and
[RegisteredDelegationSigningKey](../delegation_signing_keys.py.md), decoding all four
timestamps and preserving None for absent lifecycle times. That value checks
identifiers, nominal values, actor/time pairing and required evidence for active,
retired or revoked status. It does not derive registration_id again or enforce a
complete temporal ordering of lifecycle events. SecretReference validates an opaque
provider-qualified identity; constructing it performs no secret resolution.

These checks are not a universal sanitization layer. Malformed enums, public-key
material, references, timestamps or lifecycle evidence can raise their imported
errors; unexpected database failures also propagate. The store does not catch every
exception into a bounded public response. Internal records retain reference strings
and public material. The registered value's descriptor omits PEM through the Core
descriptor but includes the private reference identity; it never substitutes for
the caller's authorization or public projection boundary.

register first requires isinstance(candidate, RegisteredDelegationSigningKey), then
encodes admitted_at before touching the connection. It acquires the exclusive
workspace-purpose lock followed by the issuer-scope lock, reads the complete
workspace/purpose/issuer/key-ID identity FOR UPDATE and either returns an existing
matching identity or rejects key-ID reuse with changed identity.

same_identity_as compares workspace, purpose, issuer, key ID, algorithm, normalized
public PEM and private reference. It excludes registration_id, admission actor/time
and all lifecycle state/evidence. Consequently duplicate registration returns the
stored row, including its current lifecycle state, without refreshing admission
metadata or resurrecting a retired/revoked key. Even that path validates the new
admission timestamp before lookup. This is identity-based convergence, not an
operation-session idempotency key or an exact replay of every submitted field.

A fresh registration inserts twelve identity/admission/status fields and reads the
row back through get. It does not insert the six lifecycle-evidence columns. The
normal registration service constructs a VERIFY_ONLY candidate. Direct register
calls are not a general lifecycle import: the store does not explicitly restrict
the candidate to VERIFY_ONLY, and a prepopulated lifecycle value is not faithfully
inserted. Readback validation can fail after the insert, making caller rollback
essential. The store itself does not verify active secret-reference admission or
derive a canonical registration ID.

The [current schema](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema.sql)
has a registration primary key, unique workspace/purpose/issuer/key-ID identity,
workspace foreign key, closed purpose/algorithm/status checks, selected textual
checks and paired lifecycle columns. Its partial unique active-scope index permits
at most one ACTIVE row per workspace/purpose/issuer. It does not impose one active
issuer across the whole workspace/purpose. The value's required status evidence and
the store's recomputed fingerprint add checks beyond the selected SQL constraints;
the schema is not a cryptographic verifier or secret-custody service.

get queries the complete identity and raises DelegationSigningKeyNotFound for an
absent row. require_active filters workspace/purpose/issuer/status and fetches one
row without an ordering or lock; its uniqueness expectation relies on the current
partial index. It does not recheck secret admission, expiry or remote readiness.
list_for_verification returns ACTIVE and VERIFY_ONLY rows for the same scope,
ordered by key_id, excluding RETIRED and REVOKED. That list describes the retained
verification set and does not itself authorize verification or publish it.

require_unambiguous_active selects without caller-supplied issuer. It takes a shared
workspace-purpose advisory lock, queries active rows ordered by issuer/key_id with
LIMIT 2 and accepts exactly one. Zero or multiple candidates produce the same fixed
not-found category. It does not elect the first row from an ambiguous set or decode
every rejected candidate. The shared lock lasts for the caller transaction, not
for the lifetime of the returned Python value.

_lock_scope takes the exclusive workspace-purpose lock before an exclusive issuer
lock. Both use hashtextextended text keys and PostgreSQL transaction advisory locks.
Thus lifecycle writes to different issuers in one purpose still serialize and
exclude the shared selector. lock_purpose_for_lifecycle exposes the exclusive
purpose lock so a larger caller can acquire it before a multi-store mutation.
These are cooperative protocol locks, not a prohibition on arbitrary SQL updates.
Ordinary get, require_active, listing and pagination do not acquire the shared lock.
There is no local lock timeout, automatic retry or deadlock recovery loop.

activate validates its timestamp before locking, reads the target FOR UPDATE and
returns it unchanged if already ACTIVE. Otherwise only VERIFY_ONLY may activate.
It first demotes the old active row in the same issuer scope to VERIFY_ONLY, retaining
that row's activation evidence, then updates the target's status and activation
actor/time. Both writes and final readback belong to the caller transaction. A
previously active key demoted to VERIFY_ONLY can activate again, replacing its
activation fields; these columns are not an append-only activation history.

retire similarly validates time, locks and reads the row. RETIRED returns unchanged;
only VERIFY_ONLY can newly retire. An active key must first leave active status.
revoke returns an already REVOKED row unchanged and otherwise permits revocation
from any decoded status, retaining prior activation/retirement evidence. Missing
targets fail rather than being created. Repeated lifecycle calls preserve existing
actor/time evidence after validating the supplied timestamp; changed actor/time
does not become a changed-intent conflict. Retired/revoked keys cannot reactivate
through activate. No method deletes rows, erases private material or compensates an
external effect.

Mutation statements do not inspect rowcount or use RETURNING. Normal cooperative
locks and schema constraints support their assumptions, and the following get
decodes the result. The store is not an independent acknowledgment checker for
every adversarial connection, trigger or concurrent writer outside that protocol.
It records lifecycle columns, not operation sessions, actions or activity events.
Higher-level rotation workflows own any broader approval/history story.

list_workspace returns every status in purpose/issuer/key-ID order without a bound.
workspace_page is the bounded alternative. It requires DELEGATION_SIGNING_KEYS and
uses workspace scope, ascending (purpose, issuer, key_id), strict tuple seek and
LIMIT request.limit + 1. The [page contract](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/read_pages.py)
normally validates the exact workspace scope, matching DelegationKeyReadCursor and
limit 1..100. Every fetched row, including the extra sentinel, is decoded before
ReadPage trims to limit and returns the last exposed tuple as next cursor when more
exists. Page construction checks cursor congruence/count, not SQL ordering itself.

Status changes do not move a row's cursor identity. A fresh page may show changed
lifecycle evidence while a continuation remains after its earlier tuple. Pagination
does not establish one snapshot across separate caller transactions. Missing
workspaces yield empty lists/pages rather than a distinct workspace error. These
selectors return internal registered values, not already-authorized UI dictionaries.

The actual [timestamp codec](temporal.py.md) accepts exact canonical UTC text, validates
real calendar instants and encodes aware datetimes. It decodes database datetimes
to UTC seconds or six-digit nonzero microsecond form. All four mutations validate
their incoming time before the first store SQL, including no-change/replay paths.
This law does not mean a composing service performs no earlier admission reads.
Chronological monotonicity is not checked by this store.

The fully read [signing-key suite](../../../tests/test_delegation_signing_keys.py.md)
has eleven tests. Its PostgreSQL setup installs the schema, truncates workspaces,
creates two workspaces and admits synthetic provider/reference metadata through
services; it retrieves no private key. Teardown closes its inspection connection.
Tests cover duplicate identity, changed PEM/reference conflict, workspace-scoped
reads, surface-read purpose, activation overlap and retirement/revocation filtering.
The restart-safe test uses service calls and fresh connection reads, not an actual
process/database restart. The permissions test rejects read-only scope for
registration; it is not an exhaustive authorization matrix.

The same suite supplies a fail-on-access connection for all four malformed-time
mutations and checks a fixed cause/context-free ValueError before execute. Real
service tests reject malformed timestamps on duplicate/lifecycle repeats, preserve
the old active signer after invalid activation and compare complete selected
records after invalid retirement/revocation. The native-time test uses Asia/Tokyo
session timezone and seconds/microsecond/null evidence across get, active,
unambiguous, workspace and verification selectors; it does not call workspace_page.
There is no broad cryptographic or arbitrary-storage-corruption proof in this suite.

Selected additional tests strengthen specific boundaries. In
[node-control intents](../../../../../../control-plane-kit-operations/tests/test_node_control_intents.py),
test_key_lifecycle_and_authority_selection_use_opposite_purpose_locks holds the
shared selector lock and requires PostgreSQL LockNotAvailable under a 250ms local
timeout for register, activate, retire and revoke contenders across issuers. After
release, registration succeeds. An exclusive purpose holder similarly blocks the
selector, which succeeds after release. These are database contention witnesses,
not just thread-entry barriers; the complete surrounding intent suite was not read.

The selected reload lock test in
[signing-authority tests](../../../../../../control-plane-kit-operations/tests/test_postgres_node_control_signing_authority.py)
pauses its service at a clock and probes workspace/secret rows plus key revocation
using actual lock timeouts, then retries after release. This is composition context,
not a claim that this store owns the other locked rows. The selected lifecycle-page
test in [large collection pages](../../../../../../control-plane-kit-operations/tests/test_large_read_collection_pages.py)
changes a previously paged key to RETIRED: continuation omits that earlier tuple,
while a fresh page includes its new status in the original tuple order. Its full
seeding apparatus and the remaining suites were not independently reviewed here.

Read depth: full store469, value/service owner391, signing-key test683/all eleven,
Core public-key owner and both temporal helpers were read. Full unit-of-work context
was retained. SecretReference, page values/specification, schema table/index/key
constraints, bundle/export wiring and the named additional test bodies were selected
reads. No full secrets, page, schema, intent/reload, rotation, provider or transitive
dependency review is claimed. Security: this documentation adds no runtime, network,
credential or mutation surface. Validation is static links/whitespace/frozen-source
comparison only; no imports, executable tests, database/provider calls or key access
were performed. Inventory and publication remain with the assigned publisher.
