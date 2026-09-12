Source: [control-plane-kit-operations/tests/test_gateway_key_rotation_program_acceptance.py](../../../../control-plane-kit-operations/tests/test_gateway_key_rotation_program_acceptance.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This single acceptance test composes the rotation's approved generation,
overlap preparation/execution, activation/drain, retirement preparation/execution
and completion through actual Operations services. PostgreSQL holds the records
and transitions; generation, runtime and revocation effects are simulated. Fresh
program instances exercise selected reconstruction/replay boundaries. The test
does not kill/restart a process, contact a provider or prove live gateway behavior.
No executable validation was run for this documentation.

setUp requires CPK_OPERATIONS_TEST_DATABASE_URL, installs/verifies schema and
truncates workspaces CASCADE. The
[overlap fixture](gateway_rotation_overlap_fixture.py.md) seeds an authored graph,
realized A projection, registered product and active old key, with an unrelated
gateway verifier; include_replacement_key=False leaves B for this test's generation
path to admit. Old key activation in setup is a direct store operation. Unlike
the simpler retirement fixture, subsequent B activation goes through the actual
activation program. tearDown closes the main connection; the next setUp resets
data. This is isolated-database mutation, not a pure in-memory fixture.

Provider and old-reference registration use synthetic endpoint/credential
references, an allowed keys prefix and gateway-signing intent. The old reference
records version-a / 1. A real operation session, rotation approval request and
manager approval decision establish APPROVED through the rotation service.
These are durable policy records; scope/actor fields are supplied directly and
do not exercise HTTP/MCP authentication. Generation, deployment, activation and
completion use their respective focused scopes. Completion does not create a
separate exact-version approval decision in this composition.

TrackingUnitOfWorkFactory wraps actual PostgresUnitOfWork objects, incrementing
active after entry and decrementing in finally after exit. Each simulated effect
asserts that active is zero when called, and the test checks zero at the end.
This protects effect placement relative to UoWs created by this factory. It does
not inspect all database sessions, the main autocommit connection, other factories
or a real provider's transaction state. The counter is used sequentially here;
this is not a concurrency or connection-leak stress test.

RecordingGenerationProvider accepts the returned generation action, checks that
no tracked UoW is active and constructs DelegationKeyGenerationEvidence for
key-b, version-b / 1 and the action's reference/correlation. It uses the fixture's
synthetic public PEM and increments a call count. It has no key-generation engine,
provider receipt authentication, correlation ledger or replay cache. The test
calls it explicitly between prepare and submit because the
[generation program](../src/control_plane_kit_operations/gateway_key_rotation_program.py.md)
does not call a provider itself.

Generation prepare through a new instance must reproduce the action. After the
single simulated generation call, submission through another instance admits
reference/key and advances rotation. Repeating submission must report replayed.
This establishes selected stored-intent/result reconstruction with fixed inputs;
it does not inject a loss after provider success or validate durable provider
idempotency. Its only generation-call assertion is the final count of one.

Overlap preparation is run twice through fresh instances and must return equal
checkpoints. Execution uses the actual shared
[coordinator composition](gateway_rotation_overlap_fixture.py.md), including
lifecycle, effect start/fold and reconciliation services, but RecordingRuntimeAdapter
always returns success after recording an activity ID. Its runtime entry point
converts that result with the requested effect ID. The fixture observer returns
indeterminate evidence; this successful run does not assert that observation or
reconciliation was exercised.

Execution helpers try at most 32 progress invocations per phase, suffixing the
caller key with the step number and returning when the rotation reaches phase
READY. They do not assert every intermediate outcome or compare adapter calls
with the complete activity plan. After overlap acceptance, another fresh program
receives the original unsuffixed key and must leave runtime-call count unchanged.
That call uses already-accepted classification, not the terminal step's exact
coordinator receipt key; its return outcome is not directly asserted here.

The actual [activation program](../src/control_plane_kit_operations/gateway_key_rotation_activation.py.md)
first returns WAITING. The test then assigns its mutable epoch directly to the
returned drain deadline and requires READY_FOR_RETIREMENT on another invocation.
There is no sleep, grant traffic or observed draining. The expected deadline is
not independently calculated/asserted here. Phase textual timestamps are fixed,
while the mutable epoch controls policy progression and PostgreSQL supplies lease
time; those clocks are not interchangeable evidence of elapsed wall time.

Retirement preparation uses current workspace lineage after activation/drain,
then repeats through a new instance and compares checkpoints. Its execution uses
the same successful runtime recorder with the retirement coordinator/clock and
the same bounded numbered-key loop. An unsuffixed-key call after acceptance must
again add no runtime calls. Both phases use worker-a generation-1 claims; the
test never expires, transfers or changes either claim.

RecordingRevocationProvider checks that no tracked UoW is active, increments calls,
changes the requested in-memory version from active to revoked if needed, and
constructs a matching SecretVersionRevocationReceipt. It is not a durable provider
or a correlation/fingerprint replay ledger. The
[completion program](../src/control_plane_kit_operations/gateway_key_rotation_completion_program.py.md)
performs local retirement, prepares revocation, invokes this adapter and folds
success. A fresh completion instance must then report COMPLETED_REPLAY without
another provider call; source reconstructs that receipt from the checkpoint.
The test does not exercise a second revocation dispatch or provider cache recovery.

Final assertions require COMPLETED then COMPLETED_REPLAY, one generation call,
one revocation call, simulated version-a revoked and version-b still active,
at least one runtime call and zero active tracked UoWs. Durable key checks require
old key REVOKED, active key key-b and rotation COMPLETED. They do not decode the
final realized graph, compare authored descriptors or unrelated verifier material,
count exact activity/advancement events, or inspect all reference/provider rows.
The actual composed services have additional gates documented with their owners;
that does not turn this file's final assertions into an exhaustive snapshot.

The phase ledger creates lightweight RotationPhaseEvidence values from stored
transition destination status/version/ID and prepends a synthetic REQUESTED,
version-1, rotation-requested entry. It requires exactly this status sequence:
REQUESTED, AWAITING_APPROVAL, APPROVED, GENERATION_PREPARED, KEY_GENERATED,
OVERLAP_DEPLOYING, OVERLAP_READY, NEW_KEY_ACTIVE, DRAINING_OLD_GRANTS,
RETIREMENT_DEPLOYING, RETIREMENT_READY, OLD_KEY_RETIRED, REVOCATION_PREPARED,
COMPLETED. This rejects missing, extra or reordered phase destinations in the
returned transition sequence. It does not independently assert contiguous version
numbers, exact transition IDs, fingerprints, timestamps or the synthetic initial
entry's presence in a durable transition table.

The ledger repr must omit secret://, version-a, version-b, public key, private
and compact. Those canaries apply only to the deliberately narrow phase projection;
the test does not serialize full rotation/action/grant/provider results or inspect
all errors/logs. Public PEM and secret-reference metadata elsewhere in the fixture
are not audited by that one assertion. No actual private key or credential bytes
are generated/resolved by the simulated adapters.

This is one coherent happy path through real local services with selected fresh-
instance replay checks, not evidence for every interruption/retry boundary. It
does not cover rejection, provider failure/uncertainty, concurrent operators,
changed accepted history, malformed receipts, late fold failure, reverse lexical
key order or conditional different-ID projection reuse. Separate reviewed
[generation tests](test_gateway_key_rotation_generation_program.py.md),
[overlap execution tests](test_gateway_key_rotation_overlap_execution.py.md),
[retirement execution tests](test_gateway_key_rotation_retirement_execution.py.md)
and [completion tests](test_gateway_key_rotation_completion_program.py.md)
retain their own bounded evidence; none establishes a live provider or deployment
merely by composing successful fakes here. No resource teardown or secret-erasure
procedure is tested.

Read depth: full 731-line test, retaining complete generation/overlap/activation/
retirement/completion source and reviewed phase-test context. The actual fixture's
replacement-key switch, graph/key seeding, coordinator wiring, result conversion
and observer were rechecked, with retained UoW and service transaction contracts.
No source change, executable test, key/credential, database/provider/runtime
action or publication was performed for this note. Documentation adds no security
or mutation surface; the local/external and tested/untested distinctions above
remain part of its acceptance meaning.
