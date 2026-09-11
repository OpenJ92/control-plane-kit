Source: [control-plane-kit-operations/tests/test_node_control_attempts.py](../../../../control-plane-kit-operations/tests/test_node_control_attempts.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This file contains 15 tests for the
[intended-attempt value](../src/control_plane_kit_operations/node_control_attempts.py.md)
and its [PostgreSQL store](../src/control_plane_kit_operations/postgres/node_control_attempt_store.py.md):
seven methods in NodeControlAttemptTests and eight in the PostgreSQL class. The
first class includes a real database schema test; it is not wholly a pure contract
suite. Coverage combines value laws, exact retained wire reconstruction, selected
relational witnesses, schema drift/reentry, rollback and advisory locking. It does
not execute a retained command or prove that an intended attempt was delivered.

_NodeControlAttemptFixture builds an apply-command request targeting a router's
control socket and limit variable, with replace-scalar payload, version-4
precondition and fixed request/idempotency IDs. Its read-state variant omits the
apply payload/codec/precondition. Constructed transit/workload grants bind those
request coordinates, use epoch 100..200 and have fixed issuer/key/JTI values.
The attempt has constructed registration/authorization IDs, correlations, lineage,
runtime and intended timestamp. These are unsigned typed values, not signatures,
resolved credentials or a graph/runtime observation. The public contract getter
checks that a name exists in the Operations root; it is not an independent
owner-module export-identity comparison.

The first test independently hashes the RFC8785 object containing profile, actor,
gateway node and request digest, using a 1e20 scalar request. It compares that
fingerprint and all three derived wire properties with the underlying values,
plus derived workspace/request IDs. Its repr assertions only exclude private and
signature substrings from this fixture's rendering. They do not establish general
payload redaction or absence of operational identities in nested grants.

Two further tests separate semantic identity from retained authority evidence.
Changing request alone makes the aggregate incoherent; the constructor has no
intent_fingerprint parameter. Changing intended time and only transit expiry
preserves the fingerprint, while actor changes alter it. A larger replacement
changes attempt/runtime, key and authorization registrations, correlations, grant
keys/JTIs/times and intended time while preserving the fingerprint. Changing gateway
node or changing the command and both matching grants changes it. These prove the
chosen fingerprint law for concrete cases, not full equality of all retained fields
or current authorization merely from equal fingerprints.

The read/apply test admits both operations, then requires rejection of 18 mismatch
constructions: attempt/graph, each family's target, variable, request ID,
idempotency key, digest, operation/codec, and transit workspace/revision. Factories
construct nested values inside the assertion, so a NodeControlAttemptError from
the aggregate is required; unrelated Core exceptions would not satisfy it. This
matrix protects command/grant correspondence but does not test equal family grant
times, an external clock, live audience/target reachability or signature validity.

The scalar/selector test supplies eight invalid actor/projection/runtime/key/
authorization/correlation/issuer strings and checks bounded candidate-free errors.
A connection fake raises if execute is called; five invalid workspace/request/
attempt selector calls must fail before SQL. The common helper bounds message at
128 characters and repr at 160, checks an optional canary where supplied, and
requires absent cause/context. It is not an exhaustive boundary-length, subclass,
arbitrary-object or all-field validation matrix.

The schema test requires CPK_OPERATIONS_TEST_DATABASE_URL, opens a real autocommit
connection and calls install_schema. It queries live information_schema columns
in ordinal order, including normalized timestamp precision, nullability and
defaults, comparing all 27 columns with _ATTEMPT_COLUMNS. Remaining relation,
constraint and index assertions inspect the imported
[current schema contract](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema_contract.py)
value. They should not be described as independently queried live catalog details
within that test, even though actual installation performs its own verification.

Those constants require a permanent heap relation without partitioning or RLS,
four key constraints, seven nondeferrable no-action foreign keys, 26 named validated
checks, and ten indexes: four constraint-owned plus six nonunique supporting
indexes. They reject extra contract constraints/indexes, unexpected workspace-head
foreign keys and time/digest index names. Check-expression coverage searches for
selected size/digest/registration tokens; it does not execute every positive and
negative SQL constraint boundary. The FK shape intentionally preserves historical
projection/source/workspace and authority registrations without coupling retained
intent to mutable workspace current pointers.

The atlas/package test reads the actual
[Operations table atlas](../../../../control-plane-kit-operations/OPERATIONS_TABLE_ATLAS.md).
It requires the attempt heading, INTENDED-only wording, a #1556 reference, unsigned
terminology within the attempt section and no signed-grants phrase there. It
requires the contract digest on atlas line three, every named attempt FK somewhere
in the atlas and the attempt section before realized projections. These are text
and ordering assertions; they do not verify every atlas claim or live schema state.

The same test parses the two owner/store modules with AST, excludes five exact
import-module strings and disallows attribute calls named commit, rollback,
transaction or connect. Exact import-set intersection is narrower than checking
all submodules, dynamic imports or arbitrary network paths. It establishes
selected source boundaries for these modules, not runtime tracing or a general
proof that SQL mutation is impossible. The real store deliberately performs INSERT;
caller-owned transaction and immutable intent are its architectural contract.

The PostgreSQL class installs schema, truncates cpk_workspaces CASCADE and directly
seeds one running workspace, graph/projection rows with empty JSON descriptors,
current pointers, one provider, three references, three keys and three authorizations.
The third authority set supports same-workspace substitution cases. Key IDs and
fingerprints are constructed, including framed synthetic public material; this
fixture does not run key admission/identity derivation, provider admission or
complete secret-use authorization workflows. Authorization operation IDs are not
populated. These rows support the store's selected joins, not the stronger later
signing-authority reload contract.

Setup fails if the database URL is absent and truncates/closes after setup failure
once schema installation succeeded. Teardown truncates then closes without a
finally guard around the close. These are destructive disposable-database fixtures,
not production read-only checks; no such setup was executed for this documentation.
The main fixture connection is autocommit. Individual rollback/lock/drift tests
also open non-autocommit connections for explicit transaction behavior.

The restart/round-trip test inserts an attempt and opens a second database
connection, then compares the entire reconstructed value from both selector forms.
No process is restarted. Corruption cases append whitespace to each wire column;
replace each with noncanonical or different canonical bytes while updating its
digest; change each digest alone; change six duplicated issuer/key/JTI scalars;
and change request ID, attempt ID or retained graph/projection. Each selected read
must raise the bounded corrupt error, with canary checks where supplied. The test
resets the attempt table between cases. Successful reconstruction uses actual
Core canonical decoders and the store's digest, duplicate scalar, aggregate and
fingerprint checks. Recomputing a digest is insufficient to admit those inconsistent
values; this is not a tamper-proof seal against coherent rewriting of all evidence.

_attempt_catalog_snapshot queries real catalog data for this relation: kind and
persistence, column names/types/nullability, constraint names/types/definitions,
and index names/definitions, sorted into a tuple. It does not capture every schema
attribute, privilege, comment, trigger or all tables. The prior-shape test adds a
forbidden text column inside a transaction, takes that snapshot, then requires
install_schema to raise a reset-required SchemaInstallationError. The snapshot
must remain unchanged and the forbidden column must still exist before rollback.
This is one deliberate drift shape, not reproduction of every historical schema
or permission to automatically reset a live database.

The current-reentry test inserts an attempt, saves its full row and catalog
snapshot, then clears both workspace current pointers. Reinstallation must preserve
the selected catalog snapshot and attempt row, and a subsequent read must equal
the original attempt. The actual
[schema installer](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/schema.py)
uses its transaction context, namespace advisory lock and exact-current checks;
it rejects drift instead of repairing it. On the outer transaction in the added-
column test, installation's nested transaction context preserves the caller's
earlier DDL when verification fails. These assertions cover retained historical
intent and the chosen catalog projection, not a full backup/restore workflow.

Four persisted substitutions replace a family's key or authorization registration
with another same-workspace fixture identity and require corruption. A separate
16-case matrix independently changes purpose/issuer/key ID/private reference for
each key, and intent/actor/correlation/secret reference for each authorization,
restoring each field afterward. Fourteen SQL boolean witnesses represent these
relationships: changing either side of a reference-equality witness is tested.
These reads do not check active provider/reference/key status, authorization
operation provenance or private/public key correspondence. The fixture's deliberately
limited evidence should not be credited as successful signing-authority validation.

The rollback/idempotency test proves an uncommitted insert disappears, a caller
can lock then read the existing attempt, and another attempt retaining the request
identity conflicts. That conflicting candidate also retains issuer/JTI pairs, so
the test does not isolate which unique constraint rejects it. It does not assert
that add itself returns an existing value on duplicate insertion. Finally it moves
current workspace lineage forward and requires the stored attempt's original graph
ID to remain readable. The separate missing-key registration case requires a bounded
candidate-free insert error with absent cause/context; it tests one FK failure,
not all relational constraint failures.

The advisory-lock test uses two transactional connections and a thread. The first
takes the request lock; the second signals readiness before attempting the same
lock. The fixture connection polls pg_stat_activity for that second backend to
be active with wait_event_type Lock, requires it not yet acquired, then commits
the first transaction. It joins the thread, requires no errors and successful
acquisition, and rolls back the second transaction. Cleanup releases, joins and
can cancel/close a stuck contender. The wait_event value is selected but not
asserted, and pg_blocking_pids is not inspected. This is real observed lock waiting
for a controlled call, not a fake sleep-only assertion, but it does not prove
global lock ordering, every workspace/request pair's independence, or concurrent
end-to-end preparation and at-most-once command execution.

The suite retains row truth and tests selected error representations; it does not
create delivery attempts, signatures, provider effects or operational completion
history. There is no live runtime restart, commit-crash matrix, network or secret
resolution, complete database mutation audit, or full data-restoration test here.
Read depth: the full 1490-line test and all fixtures/constants/helpers, with retained
full 214-line owner and 204-line store. Newly inspected sections include atlas/AST
checks, schema constants, catalog snapshot, drift and current-reentry tests; actual
schema installer and selected atlas/contract/verification dependencies were read
alongside retained Core codec, timestamp, caller and UoW context. No executable
validation, source/pin change, database setup, credentials/private-key access,
provider/runtime action or publication occurred. Documentation adds no new security
surface and makes no claim that this suite ran or passed during authoring.
