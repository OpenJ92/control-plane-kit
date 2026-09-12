Source: [control-plane-kit-operations/tests/gateway_rotation_retirement_fixture.py](../../../../control-plane-kit-operations/tests/gateway_rotation_retirement_fixture.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

GatewayRotationRetirementFixture extends the
[shared overlap fixture](gateway_rotation_overlap_fixture.py.md) to seed a
draining A+B rotation and supply retirement preparation/execution factories.
Unlike its base, this mixin owns setUp/tearDown: it requires the operations test
database, connects with autocommit, installs/verifies schema, truncates workspaces
CASCADE and seeds fresh truth. It closes the connection on teardown. It is
database-mutating test scaffolding, not a collection of standalone test methods.
No fixture method or executable test was run for this documentation change.

reset_truth inherits product-backed graph and synthetic public-key seeding,
creates actual rotation approval records, and uses the real overlap preparation
and execution programs with the base fixture's SuccessfulAdapter. It then calls
the signing-key store's activate directly in a UoW to make B active, before
recording new-key-active and draining transitions through GatewayKeyRotationService.
The IDs are the fixture literals activate-b and drain-a, with trusted epoch 1000.
The setup asserts drain_deadline == 1065 and retains overlap projection ID,
rotation version and deadline for later commands.

This setup does not call
[GatewayKeyRotationActivationProgram](../src/control_plane_kit_operations/gateway_key_rotation_activation.py.md).
It therefore does not exercise that program's active-reference admission checks,
deterministic activation-stage IDs or progress-result lineage. No secret-provider
or private-reference registration is added here before the direct store mutation.
The arbitrary fixture transition IDs are sufficient for the retirement builder's
stored draining-state/deadline gate; they are not evidence that the activation
program itself ran. The direct key-store operation is local signing selection,
not private-key activation at a provider.

The [retirement publication owner](../src/control_plane_kit_operations/gateway_key_rotation_retirement.py.md)
requires exact stored B-active/A-verify-only overlap before producing desired
B-only material. This fixture's old key-a and new key-b sort in role order.
It does not exercise the confirmed source limitation where Core sorts keys
lexically but retirement compares the current tuple with unsorted (old, new):
old key-z/new key-a can fail despite valid overlap material. It also does not
seed equivalent phase material under another physical projection ID, the
conditional prerequisite for the publication fingerprint/replay mismatch.
Those are source-confirmed caveats, not assertions executed by this fixture.

command creates PrepareGatewayKeyRotationRetirement from the saved draining
version and overlap projection, expected revision 2, operator-a, worker-a and
a 1,800-second lease. Defaults include rotate, plan-execute and execution-operate
actor scopes, with execution-operate worker scope. scopes or defaults and
projection_id or saved_projection mean empty values silently select defaults;
consuming negative tests must not treat those helper arguments as empty authority
or lineage. program supplies an injected epoch defaulting to the exact 1065
deadline and deterministic IDs/textual time. Selected actual preparation imports
perform their own draining/deadline gate and compose the shared child helper;
this note does not certify the complete preparation owner.

prepare_retirement_execution calls that preparation program and retains its
prepared version/checkpoint. It does not itself call execution progress or assert
the returned phase/outcome. execution_command wraps the saved prepared version,
rotate actor scope, worker scopes, a fixed worker-a generation-1 fence and a
configurable idempotency key. Unlike the preparation helper, its actor/worker
scope arguments are passed directly, so empty tuples remain empty. The fixed
fence is a fixture assumption, not a fresh lease lookup or a takeover operation.

execution_program wires the shared real effect-attempt coordinator to a supplied
RecordingAdapter and constructs the retirement execution wrapper with constant
05:00 textual time, epoch 5000 and local IDs. The actual retirement command fixes
the shared execution kernel's phase to RETIREMENT. Calling that program can
mutate the test database and use the simulated adapter; the factory alone does
not prove accepted retirement, key-row retirement or provider revocation.

RecordingAdapter appends the activity ID and consumes the supplied outcome at
each entry, mapping runtime results through the base helper. Exhaustion raises
AssertionError; an injected BaseException is re-raised after recording the call.
This enables consuming tests to inspect attempts and simulated interruption.
It does not dispatch Docker/cloud operations or observe external resource state.
The base coordinator includes an indeterminate observer; its presence alone is
not evidence that a particular consuming test invokes reconciliation.

CountingIds increments an in-memory prefix counter. _timestamp_clock ignores
its prefix argument and returns successive 04:01, 04:02, ... minute strings;
it is deterministic fixture time, not a real clock and has no rollover after
minute 59. Textual overlap setup time, inherited accepted/activation times,
trusted epochs and database lease time are separate inputs, not a demonstrated
monotonic chronology. No real sleeping or grant lifetime measurement occurs.

workspace, old_key and rotation read stored facts through fresh UoWs. Other
helpers report retirement plan activity count, run event kinds, advancement-event
count, authored graph row count, selected child row counts and desired pointer/
revision. count interpolates only a fixed allowlist of table names. These query
results become evidence only when consuming tests assert them; child_counts
does not cover every action/event/key/provider table or detect every mutation.
retirement_activity_count itself does not assert that the plan is nonempty.

Read depth: full 366-line fixture and 183-line publication owner, with retained
full overlap fixture620 and reviewed rotation/activation/overlap preparation/
execution/shared kernel context. Actual retirement preparation constructor and
new-work gate, and execution command/program forwarding, were selectively read.
The separate retirement program/execution test files remain outside this group;
there is no standalone retirement projection test file here. Synthetic public
keys, opaque reference handles, setup assertions and returned helper values are
not live signing, restart, grant drain, cleanup or provider proof. No credentials,
provider/database/runtime actions were performed; these notes add no security
or durable-mutation surface.
