Source: [control-plane-kit-operations/tests/gateway_rotation_overlap_fixture.py](../../../../control-plane-kit-operations/tests/gateway_rotation_overlap_fixture.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This is shared test scaffolding for a stored gateway verifier transition from A
to A+B, with real Operations service/store composition and simulated runtime
outcomes. GatewayRotationOverlapFixture expects the consuming test to provide
unit_of_work and database isolation. It is not a TestCase and owns no database
URL, schema installation, truncation or teardown. Its seeding methods do perform
durable writes when called; they must receive an isolated test UoW. None were
executed for this documentation change.

The fixture's ContainerServerProduct has a synthetic pinned image digest and
an empty socket contract. It encodes that value through the actual Core product
codec and derives its ProductReference and metadata. Module assertions require
no secret deliveries and no secret:// or pull_authority text in the descriptor.
The graph's two proxy-family container-server nodes share this metadata and a
Docker runtime value, with gateway-probe delegation bindings to cpk-server.
These are graph/product values; no image is pulled and no Docker process is
started by constructing them. The fixture does not prove that the image exists.

PUBLIC_KEY_A/B/OTHER are synthetic public PEM text, not generated Ed25519 key
pairs. The actual Core DelegationPublicKey contract checks bounded ASCII public
PEM framing and fingerprints normalized text; it does not parse or verify the
cryptographic key. Signing-key helpers derive registration identity from the
public record and secret reference, producing verify-only records with opaque
private-key handles. No private key or credential is read or embedded.

seed_graph_and_keys builds the authored graph and a realized A projection, with
the other gateway retaining key-other verifier material. In one UoW it creates
workspace-a, registers the product and verifies metadata/reference agreement,
saves both graph records, sets current and desired pointers, registers/activates
key-a and optionally registers replacement key-b as verify-only. The other
gateway's public verifier is in graph material; this method does not register a
key-other signing-key row. These setup writes bypass a full user-facing
deployment workflow and establish test starting truth, not deployment evidence.

seed_rotation_approval creates an operation session through OperationCommandService,
requests the rotation, obtains an actual rotation approval request/decision via
ApprovalCommandService, and advances the rotation through awaiting, approved,
generation-prepared and key-generated. Rotate and rotate-approve scopes are
separate supplied test authority. Generation uses fixed provider/action digest
and version metadata; no generation/custody provider or secret-reference
admission is invoked. It records rotation/version and approval IDs/review digest
on the fixture for dependent tests. Actual approval records are materially
different from the standalone projection test's directly seeded rotation row.

Sequence returns a finite supplied list and raises StopIteration if exhausted.
CountingIds increments a local prefix counter; it is deterministic test identity,
not a globally unique or restart-persistent allocator. SuccessfulAdapter returns
ActivityExecutionOutcome.succeeded and RuntimeEffectResult.succeeded with the
requested effect ID. It performs no external effect. runtime_result_for_outcome
maps succeeded evidence or failed/unsupported/uncertain failure details to the
runtime result algebra, asserting that nonsuccess outcomes contain a failure.

effect_attempt_execution_coordinator wires actual run lifecycle, effect-attempt
start, fold and reconciliation services around the supplied adapter/UoW/clock.
Its IndeterminateRuntimeObserver records request/authority pairs in a list and
returns indeterminate evidence with fixed provider-result-unknown failure text.
It does not observe a provider or redispatch its effect. This allows consuming
tests to exercise durable uncertainty handling without asserting external truth;
the call list is in-memory test instrumentation, not retained activity history.

CrashAfterCommitUnitOfWork delegates entry/stores/commit/rollback to a real UoW.
commit calls the inner commit method, then marks a local request. On exit it
first lets the inner UoW complete its physical transaction, then counts a
successful requested commit and raises SimulatedProcessLoss at the configured
count. That exception derives from BaseException, bypassing ordinary Exception
handlers. This models an interruption after a committed transaction; it is not
a process kill, database restart, provider crash or rollback of the committed
facts. Its local counters and commit-request flag belong to the fixture wrapper.

accept_prepared_overlap builds the actual overlap execution program with a
SuccessfulAdapter coordinator, fixed accepted-at clock, trusted epoch 3000 and
deterministic IDs. It supplies rotate scope, worker-a with EXECUTION_OPERATE,
the prepared rotation version and prepared fence. It loads the plan and calls
progress once per activity with distinct idempotency keys, returning the final
program result. An empty plan raises AssertionError. The helper itself does not
assert the final status; consuming tests must assert acceptance, history and
other intended results. Its program calls can mutate the supplied database even
though the runtime adapter is simulated.

The [rotation service tests](test_gateway_key_rotations.py.md) were read in full
as an actual consumer: they use this fixture for prepared overlap, simulated
acceptance, durable drain state and blocked identity checks. The separate
[projection publication tests](test_gateway_key_rotation_overlap_projection.py.md)
do not import this fixture and instead seed their own simpler graph/rotation.
Admission, preparation-program and execution sibling tests are not claimed as
reviewed by this companion. The fixture's crash/observer capabilities alone do
not constitute assertions or establish live recovery, key use or cleanup.

Read depth: full 620-line fixture retained from the prior rotation group, with
full rotation owner/store/test context and the current full overlap projection
owner/shared builder/publication owner/test reads. Selected actual Core public
key/product/materialization and PostgreSQL publication contracts were inspected.
Sibling runtime program imports are described through their fixture construction
and call sites, not as a full review of those owners. This is documentation only;
no secrets, credentials, provider or runtime actions and no new security surface.
