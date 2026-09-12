Source: [control-plane-kit-operations/tests/test_postgres_guarded_observed_effect_fold_authority.py](../../../../control-plane-kit-operations/tests/test_postgres_guarded_observed_effect_fold_authority.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These four tests combine real PostgreSQL registration/fold setup with patched
intent/authority returns and a fake connection for active-selector edge cases.
They protect selected stored-intent correspondence, authority lookup shape and
error categories. The [guarded fixture](postgres_guarded_observed_effect_fold_fixture.py.md)
owns setup and synthetic local/remote authority values. No provider is contacted
or credentials resolved by these tests, and no tests or database operations were
executed during documentation authoring.

The registration control seeds one guarded start and registers local authority,
then reads it back through the ordinary runtime-authority get method and requires
exact equality. Before the remote case, raw SQL revokes registrations for that
workspace/reference; the fixture then registers remote TLS authority and reads it
back equally. This is real test-table mutation with synthetic authority data,
not the public revocation workflow or proof of access to a Docker daemon. The
attempt assertion compares only identity, not full state or history. This control
does not invoke get_active_for_update or prove lock behavior.

The fresh-intent matrix patches EffectAttemptIntentStore.get for five cases.
Missing and malformed inject KeyError and OperationsRecordError. Foreign forges
the exact record type with a different activity identity. Drifted constructs a
lawful intent record after clearing its authority reference and intent/product
deliveries. Original-event constructs a record whose original event ID differs.
The guard still carries the original accepted intent record. Each service call
must raise the exact invalid-truth conflict. No case asserts ID counts, lease or
authority-lookup avoidance, safe-error bounds or a before/after snapshot.

Those cases do not isolate all underlying equality checks. The foreign record
disagrees internally and with both locked attempt and supplied guard. The drifted
record changes fingerprint as well as guard equality. The changed original event
disagrees with both the attempt's original event and the supplied record; only
event ID is varied, not every event field. The actual
[interpreter](../src/control_plane_kit_operations/effect_attempt_fold_interpreter.py.md)
compares exact record type, identity, complete original event, request ID, request
fingerprint and guarded-record equality before lease observation. That broader
source check explains the tests without making five patched returns an exhaustive
deep revalidation matrix. The actual [intent store](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/effect_attempt_intent_store.py)
loads canonical intent evidence with its joined original event; its decoder is
bypassed by these mocks.

The large selector test captures get_active_for_update if published, otherwise
uses the local _predecessor_active_selector while reaching its assertions. When
the target exists, inspect.getsource checks normalized source for active filtering,
FOR UPDATE and fetchall, absence of LIMIT/OFFSET and absence of selected mutation
keywords after removing FOR UPDATE. The final assertion requires the real target
to be present. The fallback is a diagnostic predecessor, not permission for a
missing production selector to pass. It calls the actual row decoder but lacks
the production selector's explicit exact-tuple/nine-column row guard.

_RowsConnection records execute and fetchall calls, returns predeclared rows or
raises a supplied exception during execute. Its ledger helper requires exactly
execute/fetchall, or execute alone for injected raw errors. It checks that the
query starts with SELECT, includes workspace/reference/active filters and
FOR UPDATE, excludes LIMIT/OFFSET/INSERT/UPDATE/DELETE after removing the lock
clause, and passes the exact requested workspace/reference parameters. These are
lexical query and fake-driver interaction checks, not a SQL parser, proof of
real row filtering or a concurrency witness. They do not ban every possible SQL
construct or establish that no indirect function could have effects.

A genuine registered row is fetched from PostgreSQL and supplied to the fake
connection; the selector must reconstruct the expected registration and satisfy
the ledger. A second fake row changes workspace to exactly 512 characters and
requires acceptance with matching lookup parameters. That boundary value is not
inserted into PostgreSQL by this case. The first round also compares only the
current attempt's identity with its seeded identity.

Seven malformed lookup inputs cover a non-string, empty, 513-character or
control-containing workspace; a non-reference object; an exact reference forged
with empty text; and one forged with _HostileText. Each requires the fixed lookup
error, empty cause/context and zero fake-connection calls. The hostile string
instruments all attribute access, equality, hashing, iteration and length,
recording then raising on dispatch; its ledger must remain empty. This is a
strong selected non-dispatch witness for reference text, not coverage of every
possible hostile workspace/reference subtype or special method.

Five fake row cases cover no row, duplicate rows, an EXTERNAL runtime-kind value,
a missing column and an extra column. Absence must give RuntimeAuthorityNotFound;
the other four give the fixed registration-row error. All require no cause/context
and the execute/fetchall ledger. The two arity cases additionally require the
exact exception type after capturing any Exception. Duplicate rows are fabricated
in memory, not created by competing registrations. The EXTERNAL value is a real
RuntimeKind enum member encoded as text, but the registration model only supports
Docker; it is a semantically inadmissible row rather than unknown enum syntax.
No non-tuple row case, actual corrupt database row or complete column-fault matrix
is asserted here.

The inspected [authority selector](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/runtime_authority_store.py)
admits exact workspace/reference input shapes, reconstructs the reference,
selects all active rows for that key FOR UPDATE and requires exactly one tuple
with nine fields. Its row decoder constructs the reference, enum values, Docker
authority and timestamp before the registration value; selected ValueErrors
become the fixed row error after the handler exits. The selector's SQL scopes
the returned rows; these fake-driver tests do not independently prove every
returned key/status agrees with the requested active key under a dishonest
driver. The service's additional comparison is a distinct boundary.

Two selector raw-error cases inject TypeError and RuntimeError at execute. The
exact supplied exception must escape, and the ledger must show execute without
fetchall. Those are injected fake-driver failures, not actual psycopg failures;
their canary-bearing text is deliberately not checked for redaction. The fixed
lookup/row errors have exact-message and chain assertions here, but no explicit
rendering-length bound or general canary exclusion helper.

The selector test finally runs three actual fresh guarded folds: no authority
reference, local authority and remote authority. It seeds corresponding intent
evidence, registers authority when needed, passes register=False to prevent
additional guard-setup registration and wraps get_active_for_update. Each result
must be NewlyFolded. The wrapper records no lookup for no-reference intent and
exactly one persisted workspace/reference lookup for local and remote intent.
These are concrete service integration witnesses for the selected lookup key and
optional authority path. They do not perform endpoint probes, read back the full
fold aggregate or prove concurrent lock exclusion.

The last service matrix separates active-authority absence, mismatch and malformed
returns. Missing and revoked labels both inject RuntimeAuthorityNotFound from
the selector and must become the fixed authority denial. They do not actually
delete/revoke rows in this matrix. Replaced changes only registration ID; foreign
changes workspace. Both are individually constructed registration values whose
inequality with the accepted guard must also deny.

Malformed-kind instead forges an exact registration with runtime kind EXTERNAL;
malformed-status returns a legitimately constructed REVOKED registration from an
API promising active authority. Both must become invalid-truth conflicts. The
status enum is valid; the violation is returning a non-active value from that
boundary. This differs from a revoked row correctly filtered out and reported
as not found, which belongs to the denied category. The accepted guard remains
active and unchanged, so these cases exercise the selector-return boundary rather
than constructor rejection of the command itself.

The actual service maps RuntimeAuthorityNotFound to denial and registration
decoding errors to invalid truth. For a returned value, wrong exact record type,
runtime kind or status is invalid truth; other inequality with the accepted guard
denies. The test chooses particular kind/status/value mismatches, not every enum,
type or metadata defect. It asserts expected exception category and exact message
without snapshot, safe-error or ID-allocation assertions. Two final patched
selector TypeError/RuntimeError cases must escape as the identical supplied object,
again without a redaction assertion or actual driver-failure reproduction.

Lease expiry and current worker-claim authorization precede active registration
lookup in the inspected fresh guarded path, but this file does not vary expiry,
rotate claims or count lease observations. Its denied/invalid-truth partitions
must not be described as fresh-expiry tests. It also takes no complete_snapshot
or equivalent before/after image anywhere; only the limited identity read-backs
and local call ledgers described above are asserted. Must-not-allocate ID labels
are not inspected sequences and supply no independent allocation evidence.

The fixture registers authority in separate caller-owned transactions and owns
test-table reset and connection cleanup. The local selector doubles/forgeries
are test-only constructions, and patches are scoped by context managers. This
file proves selected admission and lookup boundaries without establishing actor
authentication, provider reachability, rollback/retry behavior or durable event
completeness. No new runtime cleanup mechanism is defined here.

Read depth: all 586 source lines, four tests and all local selector, connection,
ledger and hostile-value helpers; retained full guarded PostgreSQL/parent fixture
and fold-owner context; actual fresh intent/lease/authority service branches,
registration value admission, store registration/get/active-selector/row-decoder
paths and intent-store lookup context. No tests, application imports, database
connections, source/dependency changes, credentials, Docker or provider actions
were executed while authoring this companion.
