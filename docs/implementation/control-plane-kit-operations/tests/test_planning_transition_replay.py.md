Source: [control-plane-kit-operations/tests/test_planning_transition_replay.py](../../../../control-plane-kit-operations/tests/test_planning_transition_replay.py).
Maintain this document alongside its source file. When the test or relevant
planning/transition contracts change, verify and update this companion in the same
change.

This 806-line suite has 17 tests protecting retained deployment transitions and
replay from pinned graph truth. It combines actual PostgreSQL services with selected
private-helper/store doubles and a finite AST architecture check. Its purpose is
to keep first planning and historical replay on the same Deploy/compile path,
preserve meaningful transition forms and reject missing, malformed or incongruent
retained evidence. It does not execute plans, authenticate a caller or prove live
deployment/restart behavior.

Every test inherits setUp requiring CPK_OPERATIONS_TEST_DATABASE_URL, even tests
whose body only exercises a helper or AST. Setup opens an autocommit psycopg
connection, installs the schema and truncates cpk_workspaces CASCADE. tearDown
truncates again and closes in finally; several test bodies also reset before a
second scenario. These destructive fixture operations belong to the isolated
Operations Docker apparatus. No imports, setup, SQL or tests were executed during
this documentation work.

The service factories use actual OperationCommandService, DesiredGraphCommandService
and ActivityPlanningCommandService with fresh PostgresUnitOfWork connections, fixed
timestamps and supplied Sequence IDs. Sequence raises AssertionError when empty,
making an empty planning factory an explicit no-new-ID witness. The clock does not
similarly fail on access. Planning accepts an optional injected graph codec or
unit-of-work factory for targeted failures.

prepare commits workspace/current authored graph/current pointer, starts an operation
session through its own service, writes the desired graph through its own service,
then returns RequestActivityPlan with explicit current/desired authored and realized
IDs plus desired revision. plan additionally executes planning. These are multiple
command transactions, not one transaction grouping fixture setup and planning.
The default principal material is actor text operator-a, not a verified credential
or authorization-scope test.

The actual
[planning owner](../src/control_plane_kit_operations/planning.py.md)
loads realized projections, verifies workspace/authored membership, decodes and
validates both graphs, invokes the
[Deploy transition owner](../src/control_plane_kit_operations/deployment_transitions.py.md)
and compiles transition.diff. ActivityPlanningResult retains that transition while
omitting it from its descriptor/repr field, and requires its compiled plan to match
the recorded plan. Replay uses the plan's pinned projections after checking retained
action/session evidence, rather than replanning against current workspace selection.

The first test plans the fresh-deployment scenario, requires transition's dataclass
field repr=False, checks an InitialDeployment and equality with a freshly compiled
transition diff, and excludes both scenario graph names from result repr. It compares
the complete result descriptor: plan/session/authored/projected coordinates,
desired revision, readiness/count, action ID/ordinal and replayed=False. The expected
values largely come from the result itself, so this protects descriptor shape and
omission rather than independently verifying every stored coordinate.

Three focused forms distinguish graph meaning from ID or activity count. Equal graph
values saved under distinct IDs produce NoOpDeployment and no activities. Distinct
empty graph names produce UpdateDeployment, no activities and a nonempty diff.
Adding an ExternalRuntime to an empty graph produces InitialDeployment with no
activities and a nonempty diff. An external-runtime topology is a pure fixture value,
not an external provider operation.

The scenario matrix selects fresh-deployment, backend-switch, full-teardown and
no-change from the
[Core corpus](../../../../control-plane-kit-core/src/control_plane_kit_core/planning/scenarios.py),
checks the corresponding transition form and equality between plan and compilation
of its diff, resetting between cases. A separate unsupported-implementation scenario
must retain UpdateDeployment with ready_for_execution=False. Type checks use
assertIsInstance, not exact type identity. These cases do not assert every activity,
risk or dependency expectation in the larger corpus.

The historical replay test actually moves the desired authored pointer through a
new desired command, then closes the session. It takes four durable table counts,
calls planning with a fresh service and empty Sequence, and requires replayed=True,
the same plan record, equal transition and unchanged counts. The workspace must
still name graph-moved, distinct from the replayed plan's desired graph. This proves
the selected historical result survives both pointer movement and session closure
without allocating a new ID; it does not restart a process/database or assert no
clock/read activity.

_durable_counts counts authored graphs, realized projections, plans and operation
actions only. Count equality cannot detect arbitrary updates to existing rows and
does not cover sessions, approvals, executions, observations or provider resources.
Plan-record/transition equality and the explicit workspace pointer assertion add
specific evidence beyond those counts, without constituting a whole-database snapshot.

The workspace-evidence test alters the retained planning action's workspace payload
to workspace-b and supplies ExplodingCodec, which counts decode calls and raises
its configured exception. Replay must raise the exact incongruent-evidence message,
have no cause/context and make zero decode calls. This establishes that this
workspace disagreement is rejected before graph decoding. It does not exercise
every possible action-field corruption or independently snapshot post-error state.

Missing-plan evidence is tested by changing the action's plan_id to a nonexistent
canary ID in PostgreSQL. The underlying plan is not deleted. Replay must report
planning replay truth is missing without retaining the cause/context or canary.
After resetting and rebuilding valid state, missing-session behavior uses
ReplayHistory with a real action/plan and an injected KeyError from get_session.
It must produce the distinct session-conflict category/message, clean error and
zero fake commit calls.

ReplayHistory's lock method is a no-op; lookups return configured objects without
checking query arguments. ReplayUnitOfWork counts commit() calls but has no database,
rollback or closing behavior, and __exit__ returns False. Its zero-commit assertion
therefore proves a service control-flow boundary, not actual transactional rollback
or broken foreign-key handling in PostgreSQL. Although the common test setup uses
Postgres, this missing-session branch intentionally uses a lightweight double.

Foreign-projection membership is a direct call to the private _projection_record
helper with a valid workspace-b record and expected workspace-a. StaticProjectionStore
returns that record regardless of requested ID; ProjectionUnitOfWork simply exposes
it. The error must be realized graph truth is unavailable with no cause/context,
workspace-b or graph-name canary. This probes membership rejection, not a real
cross-workspace database query or proof of all record identity fields.

The first-planning malformed case commits a direct SQL replacement of the desired
projection descriptor with invalid nodes shape, then invokes real planning. It
requires ActivityPlanningGraphInvalid with persisted graph pair is invalid, no
cause/context and no canary. Unlike the malformed authoring test in the separate
command suite, this is corrupted persisted projection input to the planner. It does
not assert table counts or an empty ID factory after the failure.

Replay corruption has two subcases separated by reset. One changes the desired
projection descriptor to invalid shape and requires graph-invalid with a clean
fixed message. The other replaces the recorded plan payload with a valid empty
ActivityPlan whose contents disagree with the graphs; replay must report persisted
plan does not match graph transition. Another test writes a decodable graph whose
required socket wiring is invalid, verifies validate_graph rejects that graph and
requires the same graph-invalid error during replay.

These corruption cases distinguish malformed descriptor, invalid decoded graph and
valid-but-incongruent plan material. The normal store record validation can reject
some corrupt descriptors before codec decoding; the assertions do not attribute
every rejection to one particular decoder instruction. They do not cover every
record field, concurrent corruption or all possible codecs, and do not compare a
complete post-failure database snapshot.

The unexpected-codec test supplies a SentinelFailure through ExplodingCodec on real
replay. The exact exception object must escape and decode_calls must be one. The
shared projection-lookup test directly supplies MissingProjectionStore: KeyError
is translated to the fixed lookup category without its canaries, while an unrelated
SentinelFailure escapes by identity. These distinguish selected unavailable-data
translation from programming/unexpected failure propagation. They do not imply
that all database, codec or constructor failures are sanitized.

The result-congruence test obtains a real fresh-deployment result and constructs an
unrelated no-op transition. Omitting transition must raise TypeError; supplying an
object or that plan-incongruent transition must raise InvalidOperationCommand.
It does not independently corrupt every plan/action evidence field checked by
ActivityPlanningResult, probe transition subclasses or prove recursive
immutability of its retained graphs.

The final test parses the actual planning source. It gathers from-import names,
top-level functions, ActivityPlanningCommandService.execute and _activity_plan_replay.
reachable_surface follows direct ast.Name calls into same-module top-level functions;
the fresh path blocks traversal into the replay helper so it must find its own
Deploy/compile route. Both surfaces must include Deploy and compile_activity_plan,
exclude direct diff_graphs calls, and import Deploy from the transition owner
without importing diff_graphs from Core topology.

compiles_deploy_diff looks within a visited function for assignment of a Deploy(...)
call to a simple name and a one-positional-argument compile_activity_plan(name.diff)
call using such a name. It does not prove dominance, execution order or reachability
of those statements; ast.walk can include nested/dead code. The traversal does not
resolve attribute calls, aliases, arbitrary dispatch or external implementations.
This is a finite source-structure guard against the named duplicate classifier,
not a complete semantic equivalence or transitive effect-free proof.

_assert_clean_error checks cause/context are None and excludes supplied canaries
from str(error). Exact-message assertions bound the covered error cases, but this
helper has no general numeric bound, repr inspection or trace/log check. Result repr
checks use selected graph-name canaries, not every sensitive value. The actual
planning owner intentionally propagates some unexpected errors; this suite should
not be treated as a universal redaction guarantee.

The selected
[unit-of-work contract](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py)
and source paths explain real transaction/replay behavior, while the
[planning-command suite](test_planning_commands.py.md)
separately covers late-action rollback, delivery revocation and session lock races.
Neither test suite performs runtime execution or validates external credentials.
Saved catalogue admission, approval, provider outcomes, cleanup and operational
health remain outside this slice.

Read depth: full test806/every fixture and AST helper, complete planning owner915
and transition owner131 were read in this continuous slice and retained; the
count/error and replay doubles were refreshed. Full planning-command test1036 and
unit-of-work context, selected graph/history store, graph-authoring, workflow and
authority paths were retained. Core codec/compiler/validator and scenario contexts
were selected reads, not a full dependency review. Validation was documentation-only:
links, whitespace and frozen-source comparison. No application imports, executable
tests, database/provider/credential calls, source/inventory edits or publication
were performed.
