Source: [control-plane-kit-operations/tests/test_planning_commands.py](../../../../control-plane-kit-operations/tests/test_planning_commands.py).
Maintain this document alongside its source file. When the test or relevant
planning/store contracts change, verify and update this companion in the same change.

This 1036-line suite contains 21 PostgreSQL-backed tests for desired-graph authoring,
plan recording/replay, runtime-authority delivery admission and serialization with
session close/cancel. It exercises real Operations services and stores with pure
product/topology values. It does not deploy the product, execute planned activities,
request approval, prove live health or call a Docker/registry provider.

setUp requires CPK_OPERATIONS_TEST_DATABASE_URL, opens an autocommit psycopg inspection
connection, installs the schema and truncates cpk_workspaces CASCADE. It registers
an inline product descriptor, creates workspace-a, saves graph-current at version
one, sets its current pointer and commits that group. A separate operation command
then creates session-a and action-start. tearDown closes the inspection connection;
it does not truncate again. The destructive reset belongs to the isolated owning
Operations Docker apparatus. No setup or test command ran in this documentation pass.

The product is a ContainerServerProduct with an HTTP provider socket and a synthetic
OCI digest of repeated b characters. Its registry/name/tag are descriptor inputs,
not a fetched or verified image. product_graph instantiates it as app and compiles
a DockerRuntime topology. empty_graph also compiles a DockerRuntime, with no children;
the current fixture is not a structurally empty DeploymentGraph. This explains why
the expected fresh plan reconciles an existing runtime rather than starts one.

Sequence pops supplied IDs in order. Services receive independent Sequence instances
and fixed timestamps, with optional clock/unit-of-work injection for lock tests.
Names such as unused-plan are ordinary returnable IDs, not a factory that fails if
called, so they do not independently prove zero allocation on replay. Each unit of
work creates a new psycopg connection. set_desired defaults to absent desired state;
request_plan supplies authored IDs but omits projection IDs and desired revision
unless a test constructs its own command.

The fully inspected
[planning owner](../../../../control-plane-kit-operations/src/control_plane_kit_operations/planning.py)
uses idempotency locks and action fingerprints before fresh command processing.
Desired authoring requires an open matching session, then invokes graph authoring
under its unit of work. The selected
[graph-authoring helper](../../../../control-plane-kit-operations/src/control_plane_kit_operations/graph_authoring.py)
locks workspace truth, checks expected desired authored/projection/revision fields,
validates registered product references, saves graph/projection and updates desired
state. The surrounding desired service records its action in the same transaction.

Planning locks an open matching session and workspace, checks authored pointers,
uses current workspace revision when the command omits it, and resolves omitted
projection IDs to identity projections before comparing selected pointers. It reads
and validates realized graphs, derives Deploy and compiles its diff, checks fresh
delivery admission and records plan plus action. Result construction checks selected
action/lineage evidence and requires the transition to compile to the recorded plan.
These implementation laws explain the test outcomes; this suite does not separately
exercise every constructor guard or corrupted-result case.

The actual
[unit of work](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py)
physically commits on successful context exit after commit() was requested. An
exception or exit without a commit request rolls back. Desired writes and their
action form one transaction; planning and its action form another. There is no
single transaction spanning this fixture's set-desired and request-plan calls.
Replay is interpreted from durable action/graph/plan evidence, not a process cache.

The first desired success test checks nonreplay, graph-desired, action ordinal two,
the action's desired graph ID, the workspace desired pointer and the two recorded
action kinds. Its atomicity name is supported by the separately injected late-action
failure case, not by the success assertions alone. The replay test checks replayed,
same graph ID and same action, then changes only actor under the same key and requires
DesiredGraphIdempotencyConflict. It does not vary every fingerprint field.

Two identical-desired concurrency tests release two workers through a barrier. One
requires one returned graph ID, one action ID and exactly one replay. The other
checks persisted counts: two authored graphs and two projections including setup,
desired revision one, workspace pointers matching the first result and exactly the
start/action pair at ordinals one/two. A distinct-key/actor race requires exactly one
StaleDesiredGraph outcome, a winner among the two candidate IDs and only start plus
one desired action. The barriers synchronize entry, not a demonstrated overlapping
SQL lock wait; a serial database schedule remains a valid outcome.

Historical desired replay is tested after a second desired graph is written with
explicit prior lineage/revision and the session is closed. The original command
must replay its original graph, realized projection, desired revision and action.
The source checks existing idempotent action evidence before fresh open-session/
pointer checks. The test does not assert every current workspace field after replay
or compare a complete all-table snapshot.

The desired late-action failure reuses action-start and requires a real
psycopg UniqueViolation. It then requires graph-rolled-back to be absent and the
workspace desired graph ID to remain None. It does not separately count projections,
assert desired revision rollback or inject a connection-commit failure. The stale/
closed desired test asserts the two error families after a prior write and close;
despite writes-nothing in its name, it takes no post-error row snapshot/count.

The delivery-admission test uses the selected pure
[_admitted_node_delivery helper](../../../../control-plane-kit-operations/tests/test_runtime_effect_translation.py)
to construct a RegisteredRuntimeAuthorityDelivery for local-docker socket mounting.
It embeds that delivery in product configuration and uses the same runtime authority
reference. Before registration, planning must fail and plan-a be absent. It then
registers LocalDockerSocketAuthority plus the delivery through stores, commits and
accepts planning. Revoking the delivery preserves replay of that recorded plan,
while a fresh key must fail and plan-b remain absent.

The selected
[authority contract](../../../../control-plane-kit-operations/src/control_plane_kit_operations/runtime_authorities.py)
checks the requested material against an active, exact, same-workspace/reference
admission snapshot. The source invokes it only before recording a fresh plan.
This particular test covers missing, active and revoked delivery; it does not
independently test every wrong-kind/reference/material combination or real authority
revocation at an external daemon. LocalDockerSocketAuthority is a value here; no
socket is opened or mounted. Replay of historical planning evidence grants no new
execution authority.

The pinned-truth test checks authored IDs, current/desired realized projection IDs
and desired revision against the workspace, then requires exactly ReconcileRuntime,
StartNode and WaitForHealthy operation types. It checks the health activity depends
on the start activity, action ordinal three, plan ID in action evidence and the
stored plan's equality to the returned plan. It does not assert every activity
target, risk, dependency or provider effect.

The selected-projection test directly saves a DELEGATION_VERIFIER projection named
rotation-b from the same product graph and changes only the workspace's desired
projection through compare-and-set. An explicit planning command must retain the
same authored desired graph ID, the selected projection ID and the resulting desired
revision. The selected
[workspace/projection store](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/graph_store.py)
checks projection ownership and increments desired revision when switching it.
This is lineage-selection evidence; no key rotation runs and no altered verifier
material or resulting behavior is compared.

Planning replay checks equal persisted plan and replayed=True; actor change under
the same key must conflict. Fresh commands reject a different desired authored ID
and each of two swapped current/desired projection coordinates. These cases do not
snapshot every rejected write or independently vary desired revision. The test
named replay_survives_later_pointer_and_session_state_changes only closes the
session after planning: its body does not change a pointer. It proves closed-session
replay for that recorded plan, not a second pointer-drift scenario.

Identical-plan workers synchronize at a barrier and require one plan ID, one action
ID and exactly one replay. A separate close-versus-plan barrier race accepts either
session order: start/desired/close if planning conflicts, or
start/desired/plan/close if planning succeeds. It checks persisted action kinds for
the winning order, not a forced overlap or database blocker identity.

SessionLockObservedConnection delegates SQL/commit/rollback/close to a real connection.
It normalizes str(query) and looks for FROM CPK_OPERATION_SESSIONS plus FOR UPDATE.
It signals an optional before event immediately before delegating that SQL and an
after event only after execute returns. This is narrow SQL instrumentation; it
does not inspect lock tables, PostgreSQL blocking PIDs or unrelated lock statements.

The planning-owner test observes the session-lock SQL return, pauses at the planning
clock, starts a close worker, releases planning and requires plan before close in
action history. close_started signals entry to the close function before its service
call; it does not prove that close reached blocked SQL. The selected
[workflow service](../../../../control-plane-kit-operations/src/control_plane_kit_operations/workflows.py)
does acquire the session row before its terminal transition, making that ordering
consistent with the implementation's shared lock boundary.

The close-owner and cancel-owner tests share _assert_terminal_owner_rejects_plan.
They pause the terminal command at its clock, start planning and wait until the
wrapper signals an attempted session-lock query, then release the terminal owner.
Planning must raise ActivityPlanningSessionConflict; action kinds must contain only
start/desired/the terminal action and no plan may exist. The source places the
terminal clock after acquiring the session row. The before-execute signal is an
attempt witness, not direct evidence that PostgreSQL reported the follower blocked.

Barrier/event/clock waits use five seconds and selected future.result calls use ten.
Other futures/map collection and executor shutdown can wait without those bounds;
these tests set no database statement/lock timeout. Release is not uniformly in a
finally block, although clock waits eventually raise on their own timeout. The
tests should not be described as a universally bounded deadlock/failure harness.

The terminal write-once test closes a session, replays exactly the same session and
action, rejects a new cancel with OperationSessionStateConflict and checks only one
terminal action. This covers close then conflicting cancel, not every terminal
command permutation. The planning-service replay and terminal-service replay are
separate durable command contracts even though they share this test file.

The malformed-graph test inserts a GraphVersionRecord with invalid descriptor shape
inside a unit of work, then requires set_desired_graph to reject it with
RealizedGraphProjectionConflict containing valid realized graph material. It exits
without requesting commit. A later read checks no desired graph, no plans and just
the start action. This probes the store's pointer/identity-projection validation
and an uncommitted transaction; it does not first commit corrupt graph material
and then exercise ActivityPlanningCommandService against it.

The late-plan-action test attempts to insert plan-rolled-back followed by a duplicate
action-start ID. UniqueViolation must propagate; afterward the plan must be absent
and only start/desired actions remain. It proves rollback across the selected plan
and action writes, not every store failure, serialization failure or ambiguous
commit outcome. Neither late-failure test is a process restart or provider rollback.

Security and evidence limits: the command services here receive actor strings and
fixture-trusted access directly, not verified user credentials or denied-scope
requests. Product/authority registration is fixture setup, not proof of external
authorization. Most errors are asserted by family only; this suite has no general
cause/context, canary, repr or error-length redaction matrix. Action/plan checks are
selected structured-history evidence and do not establish that every unqueried
table or external resource stayed unchanged. No runtime observation or effect is
inferred from a successful plan.

Read depth: all 1036 test lines/local helpers were read, with the truncated middle
read back in full. The complete 915-line planning owner was covered across this
and immediately preceding slices, including command/result guards, execution,
replay, admission, graph interpretation and fingerprint helpers. Full unit-of-work
and selected graph-authoring/workflow/store paths were retained or refreshed;
the imported delivery helper and selected authority validator/value were inspected.
No full runtime-effect test module, graph-store, authority, Core product/planning
or transitive dependency review is claimed. Validation was documentation-only:
links, whitespace and unchanged inspected source versus the frozen baseline. No
application imports, executable tests, DB/provider/credential operations, source/
inventory changes or publication were performed.
