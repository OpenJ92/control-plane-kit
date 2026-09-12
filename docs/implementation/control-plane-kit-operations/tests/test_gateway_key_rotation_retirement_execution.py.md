Source: [control-plane-kit-operations/tests/test_gateway_key_rotation_retirement_execution.py](../../../../control-plane-kit-operations/tests/test_gateway_key_rotation_retirement_execution.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These seven tests cover the
[retirement execution wrapper](../src/control_plane_kit_operations/gateway_key_rotation_retirement_execution.py.md)
using real PostgreSQL operations services and simulated runtime results. The
[retirement fixture](gateway_rotation_retirement_fixture.py.md) requires the test
database, installs/verifies schema and truncates workspaces CASCADE. It seeds real
graph/approval records, prepares and simulates accepted overlap, directly activates
B in the key store and records activation/drain transitions before preparing
retirement. That setup bypasses the activation program and reference-provider
admission; it does not prove that separate vertical. Synthetic public PEM and
opaque private references do not contact a real gateway or secret provider.
No tests were executed for this documentation change.

Commands use the stored prepared version, rotate actor scope, execution-operate
worker scope and worker-a generation-1 fence. Execution uses a fixed textual clock
at 05:00 and trusted epoch 5000; lease time still comes from PostgreSQL. The
RecordingAdapter records activity IDs and consumes supplied outcomes, raising if
exhausted or if a supplied BaseException requests process-loss simulation. It
returns typed runtime results with the requested effect ID. An empty recovery
adapter with zero calls proves no duplicate simulated dispatch, not absence of
mutation by a real provider.

The happy path requires multiple planned activities and progresses with distinct
retirement-step keys. Intermediate outcomes are dispatched; the final result is
accepted/RETIREMENT_READY with an accepted checkpoint and one reported attempt
for that invocation. Adapter calls equal activity count, current and desired
authored graph remain graph-a, current projection becomes retirement's desired
projection and only one authored graph row exists. Old key status remains
VERIFY_ONLY, old_key_retired_at and old_secret_revoked_at remain None. Accepted
B-only deployment is therefore explicitly separate from key retirement/revocation.

A fresh program replays using the fixture's default retirement-execute-a key,
different from the original terminal step key. It returns accepted-replay without
more adapter calls or a second retirement advancement event. This exercises the
kernel's already-accepted classification before coordinator receipt handling,
not an exact-key receipt replay. It does not test altered accepted history or all
later statuses, and does not assert the advancement result's claim generation.

One test supplies generation 2 against the stored generation-1 claim and expects
the exact checkpoint-truth conflict, no cause/context and no adapter calls.
Another rejects a stale prepared version, missing actor scope and missing worker
scope, then changes desired revision to 99 and requires conflict. Despite its
before_io name, these checks establish absence of adapter dispatch; database
setup and service reads occur. They are not a universal no-write assertion.

Failure cases each reset and prepare their own child. Failed and unsupported
adapter results both block with retirement-effect-failed, while uncertain blocks
with retirement-effect-uncertain. Exactly one adapter call and the corresponding
STEP_FAILED, STEP_UNSUPPORTED or STEP_UNCERTAIN event are asserted; current remains
the overlap projection. Unsupported retains its distinct event even though the
coordinator classifies the failed schedule as FAILED. These cases do not exercise
every coordinator status or constructor-negative input.

Intent-loss simulation raises after adapter entry, leaving the committed start.
Same-key recovery returns blocked/retirement-effect-uncertain with an empty adapter,
one STEP_STARTED and current still overlap. The incomplete coordinator receipt
short-circuits to uncertain without observer execution on this path. A direct SQL
change of claim generation to 2 then rejects the old command's blocked replay
with the exact stale-authority conflict, no cause/context and no dispatch. That
SQL setup is not a legitimate lease-transfer or automatic takeover procedure.

The post-effect interruption matrix completes all preceding activities, then
raises after physical commit at boundaries 5 through 9 of the concrete composed
execution: effect fold, run complete, coordinator receipt complete, current graph
advance and rotation fold. At 5 and 6, recovery blocks uncertain with zero new
attempts, no retirement advancement and current still overlap. At 7 and 9,
recovery accepts or replays acceptance with one advancement. Prior activities
dispatch once, the final adapter dispatches once, and recovery dispatches none.
These tests do not assert one STEP_STARTED/STEP_SUCCEEDED per activity across the
matrix; that assertion belongs to the separate overlap suite. Nor do they kill
a process or interrupt the database commit itself.

Boundary 8 checks current already retirement-desired while rotation still has
the prepared checkpoint. After changing claim generation, the stale invocation
must fail without changing rotation, transitions, event kinds or advancement
counts. The test obtains a replacement handoff for the same worker and directly
calls the rotation service with an accepted checkpoint and existing advancement
evidence, reaching RETIREMENT_READY. This recovery is a manual new-fence writer
fold, not automatic adoption or retry by the original execution command.

The acceptance-evidence test prepares but does not execute/advance retirement,
then fabricates an accepted checkpoint and directly requests the ready transition
through the fenced rotation writer. Missing advancement evidence must produce
the exact incongruent-evidence conflict without cause/context. Rotation and
transitions remain equal, and event/action counts remain unchanged. This covers
the missing-evidence case, not the overlap suite's eight evidence-mutation cases
or a complete database snapshot. It protects fresh acceptance by the writer;
it does not demonstrate revalidation of history on already-accepted wrapper replay.

Fixture event helpers select the retirement run. The local advancement-action
count counts every ADVANCE_CURRENT_GRAPH action, including the seeded overlap,
so unchanged total action count is distinct from one retirement advancement
event. Count equality cannot rule out arbitrary payload changes in uninspected
rows or changes in other tables.

Read depth: full 555-line test, 208-line wrapper, 366-line retirement fixture and
651-line shared execution kernel, retaining full preparation, publication,
rotation and overlap contexts. Selected actual coordinator receipts/dispatch/
classification, advancement gates/replay, effect-attempt authority/expiry and
database lease observation paths were inspected. Expired leases, concurrent real
workers/providers, actual process restart, automatic takeover, key retirement,
secret revocation and resource cleanup remain outside this test evidence. This
documentation adds no runtime or security surface; no executable validation,
credentials, database or provider actions were performed.
