Source: [control-plane-kit-operations/tests/test_postgres_node_control_signing_authority.py](../../../../control-plane-kit-operations/tests/test_postgres_node_control_signing_authority.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These eight tests exercise the
[signing-authority reload service](../src/control_plane_kit_operations/node_control_signing_authority.py.md)
with actual PostgreSQL stores and UoWs. They combine persisted truth mutations,
clock boundaries, static adapter checks and a concurrent lock probe. They use
constructed unsigned requests/public material and never resolve private keys,
perform cryptographic signing or deliver a node-control command. This companion
records source inspection, not a test run or passing database evidence.

The class inherits only _SigningAuthorityFixture from the
[public contract test](test_node_control_signing_authority.py.md), not its test
class. Setup requires CPK_OPERATIONS_TEST_DATABASE_URL and fails if it is absent;
it opens an autocommit psycopg connection, installs schema and resets the fixture.
Reset and teardown TRUNCATE cpk_workspaces CASCADE; setup failure also truncates if
schema installation completed, then closes. This is destructive test-database
setup, not a read-only production probe. Service calls normally create separate
transactional connections through PostgresUnitOfWork.

The fixture directly inserts a running workspace, a canonical empty authored
DeploymentGraph and its identity projection, and current lineage pointers. It
does not construct the router/socket/gateway runtime described by the inherited
request. This tests retained lineage identity rather than live graph reachability.
It directly inserts two active secret-provider records, two active references
bound to the primary provider, and active transit/workload keys. The second
provider supplies substitution cases. Key registration IDs are derived with the
real identity function; inherited framed public-key strings remain synthetic,
without a private-key correspondence proof.

Two secret-use authorization rows are inserted with constructed IDs/fingerprints,
family intents, actor/correlations and attempt operation_id. The overridden attempt
factory substitutes actual current projection and derived key-registration IDs;
the real NodeControlAttemptStore persists that typed attempt. The fixture does not
run the full provider/key admission and secret-use authorization workflow. Its
fixed epoch clock is independent of the fixture's durable timestamp strings.

The success test asserts one clock observation, retained attempt/actor, both public
keys, both private-key reference identifiers and both operation IDs. Repr checks
exclude the public-key header and provider-token substring from the pair and
family wrappers. It does not independently assert every returned field or spy on
transaction exit. The service/UoW source establishes that commit/close precede
returned pair construction; a separate concurrency test checks release after
return. Repr substring assertions are not generic serialization redaction tests.

The selector/clock test rejects an oversized slash-containing attempt selector
and an object passed as execute's command. A fake UoW factory would raise if that
invalid command entered it. Four invalid clock values, True, a float, -1 and
2**53, are rejected through real database reloads. A deliberately failing fake
UoW raises a preconstructed psycopg.OperationalError from __enter__; the test
requires that same exception object to escape. This is simulated entry failure,
not a disconnected database or commit-failure experiment. No provider adapter is
installed or observed, so the test name's no-provider-effect wording does not add
dynamic network instrumentation.

The lineage test nulls both workspace current pointers, requires unavailable, and
compares the entire retained attempt row before/after. It then resets, inserts
constructed next graph/projection rows with empty JSON descriptors, points the
workspace to them and again requires unavailable. It does not independently vary
only one pointer or compare every table for absence of writes. The time test first
accepts now=100, then rewrites the stored attempt for each family separately:
not_before=101 rejects at 100, and issued/not-before=99 with expires_at=100 rejects
at 100. These are four per-family persisted negative cases for inclusive start
and exclusive expiry, with no wall-clock waiting or token verification.

Key tests perform five mutations: transit revocation, changed transit public PEM
and matching stored fingerprint under the old registration ID, transit wrong
purpose, workload revocation, and analogous workload public-material substitution.
Each must be unavailable, with resets between mutations. Separate extra active
keys from another issuer create transit and workload ambiguity and are rejected.
Changing both PEM and fingerprint makes the public-identity cases stronger than a
simple stale fingerprint-column check: retained registration derivation must still
agree. The suite does not exhaust every lifecycle status or symmetric purpose case.

Seven authorization-chain mutations cover wrong operation, unexpected session
provenance and wrong intent on the transit authorization; revoked transit reference;
revoked shared provider; reference-provider substitution; and authorization-provider
substitution. Each is a real SQL mutation followed by unavailable and reset.
They exercise both SQL join exclusions and public-owner semantics, without asserting
which internal check rejected a case. They are not an exhaustive actor/correlation,
prefix/intent-policy or per-family mutation matrix. The unavailable helper bounds
message/repr lengths and requires absent cause/context; its optional canary check
is not supplied by these calls.

The adapter-shape test reads the actual
[private store source](../src/control_plane_kit_operations/postgres/node_control_signing_authority_store.py.md)
and checks AST attributes for fetchone, no fetchall/commit/rollback, one literal
.execute( occurrence, all three relation names, FOR SHARE and absence of selected
SQL mutation/DDL tokens. These are literal source checks, not a query plan, timing
bound or observation of all SQL executed by reload. It also requires the bundle
field and compares current-schema table count before/after another install_schema
call. Equal table counts do not prove that every constraint/column or schema byte
is unchanged, nor isolate schema creation from the installation path itself.

The lock test runs reload in a thread and pauses its injected clock after local
truth selection. With the service still paused, separate real connections set
lock_timeout=250ms and attempt six writes: workspace metadata, each authorization's
session, each reference's metadata, and the shared provider's metadata. All must
raise LockNotAvailable and are rolled back/closed. Two further contenders call the
real DelegationSigningKeyStore.revoke, one per purpose, and must also time out.
Actual key-store source takes an exclusive purpose advisory lock before its issuer
lock and key row update; reload's selection takes the shared purpose lock. These
checks exercise participating lifecycle calls, not arbitrary direct key SQL.

Finally the test releases the clock, joins the thread, requires no failures and
one returned result, then repeats all six writes and both revocations successfully
on one connection and commits. It asserts each update's rowcount, both returned
revoked statuses, both committed session values and the expected metadata markers:
one workspace, two references and one provider. Both grant families used the same
physical provider, so this does not test locking two distinct providers. Events
and joins use five-second bounds; this is a controlled concurrency case, not a
stress test or a proof of global deadlock freedom. It observes locks at the paused
clock and availability after return, not every instant through commit. Commit
ordering is supported by the inspected UoW/service, not a commit callback here.

The suite neither dispatches effects nor adds operational attempt/history events.
Only the lineage test explicitly compares the retained attempt row; there is no
complete before/after durable-state or event-history audit. There is no process
restart, commit interruption, live credential resolution, malformed private-store
row injection or deployed server acceptance test. Fixed errors and repr checks
protect selected representations; the injected operational exception is explicitly
allowed to propagate unchanged.

Read depth: full 887-line test and 246-line private store, with the previously
reviewed full 793-line inherited contract-test module and 622-line public owner.
Actual UoW/timestamp code and selected bundle, key-lock/revocation and schema
declarations were inspected. These documentation-only notes ran no tests or database
setup, accessed no credentials/private keys, changed no source/pins and performed
no provider/runtime action or publication. No new security surface was introduced.
