Source: [control-plane-kit-operations/src/control_plane_kit_operations/planning.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/planning.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This 915-line owner provides desired-graph and activity-planning command/result
values plus two transactional services. DesiredGraphCommandService records selected
desired graph truth and its action. ActivityPlanningCommandService interprets a
pinned realized graph pair into a deployment transition and plan, then records plan
plus action. It does not approve a plan, claim execution, call a runtime provider,
advance current truth or implement cleanup/compensation. Actor IDs are supplied by
trusted callers; these service entry points do not authenticate credentials or
perform a general workspace policy-scope check.

The module defines DesiredGraphCommandError with idempotency, session, missing-
workspace and stale-desired subclasses, and ActivityPlanningError with invalid-
graph, graph-state, idempotency, session and missing-workspace subclasses. Both
families derive from RuntimeError. InvalidOperationCommand is imported from
workflows for malformed command/result contracts. The module has no explicit
__all__; the Operations root reexports its named public commands, results, services
and errors.

SetDesiredGraph, DesiredGraphEditResult, RequestActivityPlan and ActivityPlanningResult
are frozen dataclasses without slots. Freezing does not recursively freeze retained
graphs/records or validate arbitrary forged nested objects. Their constructors do
not enforce exact outer types everywhere: several checks use isinstance, and
service dependencies/results are structurally consumed rather than sealed by a
runtime interface checker.

SetDesiredGraph holds session/workspace/actor, a DeploymentGraph, optional expected
desired authored ID, idempotency key, optional expected desired projection ID and
nonnegative expected desired revision defaulting to zero. Required text uses
isinstance(str) and strip nonblank, without a local length/control-character bound.
The graph and key also use isinstance. Optional IDs are checked only when present;
the constructor does not couple absent desired IDs to revision zero. Revision is
exact int, rejecting bool. Durable expected-state agreement is checked later.

Its descriptor exposes command kind, actor/workspace/session, expected coordinates,
key and a graph summary of name plus sorted runtime/node/edge IDs. The default
dataclass repr still includes the graph field; neither representation is a universal
secret scrubber. _desired_graph_fingerprint hashes the full default-codec graph
descriptor, command kind, actor/session/workspace and all expected desired
coordinates/revision. It excludes the key from its preimage because the key chooses
the durable action slot independently.

RequestActivityPlan contains session/workspace/actor, required current and desired
authored IDs, key, optional current/desired projection IDs and optional desired
revision. Text and key checks follow the same permissive nominal pattern. A supplied
revision must be exact nonnegative int; None requests use of the workspace revision
at fresh execution time. Its descriptor includes all these command fields and the
key. _activity_plan_fingerprint hashes that descriptor, so this fingerprint includes
the key, unlike the desired-command preimage. Both use sorted compact JSON and
SHA256; JSON TypeError/ValueError is chained into InvalidOperationCommand.

DesiredGraphEditResult retains workspace, previous desired ID, authored graph ID/
version, action, realized projection ID/revision and replayed=False by default. It
requires positive exact integer graph version/revision, nonblank main IDs,
SET_DESIRED_GRAPH action kind and agreement between action evidence and the result's
workspace, current desired coordinates/revision and previous pointer. It does not
load the graph/projection, validate replayed as bool or prove every action field.
Its descriptor presents coordinates, versions, action ID/ordinal and replay flag.

ActivityPlanningResult retains plan_record, action, mandatory transition and replay
flag. transition is repr=False and must be exactly one of InitialDeployment,
UpdateDeployment, TeardownDeployment or NoOpDeployment. The result requires the
planning action kind, common session, matching plan/authored/projection/revision
evidence, bounded nonempty workspace evidence, and equality between the stored plan
and compile_activity_plan(transition.diff). The workspace evidence helper accepts
str subclasses and whitespace but limits length to 512 and excludes code points
below 32. This does not compare the workspace with an independently loaded session.

The result's descriptor omits transition/graph material and returns plan/session/
lineage/revision, plan readiness/count, action ID/ordinal and replay flag. Hiding
transition from repr does not erase it or remove all plan/action content from the
rest of the value. Validation does not compare every auxiliary action field, such
as its recorded readiness/count, actor, timestamp or ordinal, and does not validate
every nested result's exact type. It establishes selected value congruence, not
independent confirmation that a database commit occurred.

Each service accepts a unit-of-work factory, clock and ID factory; planning also
accepts a graph codec, defaulting to DEFAULT_GRAPH_CODEC. No service constructs
Postgres itself. The actual
[Postgres unit of work](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py)
requests commit through commit() and performs it on successful context exit. Result
construction occurs before that exit, so a result-validation exception still rolls
back the transaction. A later caller combines desired authoring and planning as
separate commands; this module does not group them into one transaction.

Desired execution computes its fingerprint, opens a unit of work and acquires the
session/key action-idempotency lock. The selected
[history store](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/activity_history.py)
implements that lock with a transaction-scoped PostgreSQL advisory lock. An existing
action takes the replay path before fresh open-session/pointer checks. Otherwise
_desired_session locks the session row and requires matching workspace; execute
requires OPEN before proceeding.

Fresh authoring calls
[set_desired_graph_in_unit_of_work](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/graph_authoring.py)
with the command's expected state, newly allocated graph ID and timestamp. That
owner locks workspace truth, compares all expected desired coordinates/revision,
checks referenced products are registered and active, saves authored/identity
projection truth and updates the desired pointer/revision. It shares the caller's
transaction and does not commit independently.

The service then records an operation action with next session ordinal, actor,
workspace, previous desired ID, resulting authored/projection/revision and product
references, requests commit and returns a validated result. Graph/projection/
workspace/action changes commit together. The selected store paths retain their
own validation: plan insertion, for example, joins session and both projection
memberships and rejects a missing insertion result. The service is not a second
complete checker of every store acknowledgment or returned row.

Desired replay checks action kind and fingerprint, reads selected payload fields,
loads the referenced authored graph for its ID/version and builds replayed=True.
It does not reread current workspace selection, require an open session, re-admit
products or reread the recorded realized projection. It also does not independently
compare the loaded graph's workspace with action evidence. The action/result
contract and store invariants carry those responsibilities beyond the checks
explicitly made here. Historical replay can return an earlier desired result after
the workspace moves and the session closes.

Fresh planning similarly locks the session/key action slot, checks replay first,
then locks session and workspace rows. It requires an open same-workspace session
and exact current/desired authored pointers plus desired revision. If the revision
argument is None, the comparison uses the just-read workspace revision. Missing
projection arguments resolve to identity_for_authored; they do not automatically
select an arbitrary current nonidentity projection. Both resulting projection IDs
must equal the workspace's selected pointers. Callers planning a nonidentity
projection therefore supply explicit lineage.

_planning_transition is shared by first execution and replay. _projection_record
loads each realized record and checks workspace plus source-authored membership.
A missing record becomes fixed graph-state conflict; a wrong membership gets the
same category. This helper does not independently compare every returned record
field, including a second equality check of its projection_id against the lookup
argument. The normal store lookup owns that identity selection.

Record-validation errors encountered during the two lookups become graph-invalid.
The selected graph codec decodes each descriptor and is also passed to validate_graph;
both wrappers must require_valid. GraphDescriptorError or GraphValidationError is
translated to fixed graph-invalid. The module then calls the actual
[Deploy transition owner](deployment_transitions.py.md)
and compile_activity_plan(transition.diff). It does not duplicate graph-diff
classification. The transition owner distinguishes an empty diff from structural
empty-boundary changes; consequently a zero-activity plan can still represent an
update or initial deployment. Planning records review-blocked plans too; readiness
is not required before recording a plan.

Before fresh plan persistence, _require_fresh_plan_delivery_admission examines only
StartNode/ReconcileNode activities. For a target node declaring runtime-authority
deliveries it lazily reads the active delivery snapshot once, then invokes the
selected
[authority validator](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/runtime_authorities.py)
with the node's deliveries, workspace and runtime authority reference. That validator
requires exact declared material and one matching active same-workspace/reference
registration. Missing graph material or registration errors become fixed graph-state
conflict. This is a durable admission check, not a credential read, daemon connection
or blanket authorization of all activity types. Replay does not run this fresh check.

Fresh execution creates a PLANNED ActivityPlanRecord with both authored IDs, both
resolved projection IDs and desired revision, then writes REQUEST_ACTIVITY_PLAN
action evidence with workspace, plan/lineage, readiness and activity count. It
requests commit and returns the plan, action and derived transition. The current
graph is not advanced, no approval is granted and no activity is executed. Plan
values describe intended work, including potentially destructive work, without
crossing the external-effect boundary.

Plan replay checks action kind/fingerprint and a bounded plan ID before loading the
plan. _planning_evidence_matches checks session, plan/authored/projection/revision
linkage and bounded workspace evidence. It loads the owning session, requires its
workspace to match that evidence and requires both pinned projection IDs. It then
reconstructs the transition/plan from those persisted projections and rejects any
plan inequality before returning replayed=True.

This replay path does not require current workspace pointers, an open session or
fresh delivery admission. It also does not independently require plan status
PLANNED. It preserves historical command results while still validating their
retained graph/plan evidence; it does not grant fresh execution authority. Replay
continues to use the current injected codec/compiler implementation, so immutable
records alone do not eliminate interpretation drift if those dependencies change.
No in-memory retry state or automatic retry loop is present.

Error handling is deliberately uneven across these owners. Desired missing-session/
workspace and GraphAuthoringError paths retain chained causes; stale classification
matches a substring in the authoring error and retains its text. Fresh planning
missing-session/workspace errors also chain their KeyError. In contrast, selected
replay missing-plan/session, projection lookup, malformed graph and delivery paths
raise fixed errors after leaving except blocks, avoiding those caught contexts.
Unexpected codec/store/database exceptions and other validation failures are not
universally caught. This module is not the final public error-redaction boundary.

The inspected
[deployment interpreter](deployment_program_interpreter.py.md)
supplies trusted-context authorization and composes these commands with session/
approval services. Selected cpk-server routes also build them from trusted actor
context and payload. The inspected
[key-rotation preparation helper](gateway_key_rotation_deployment_preparation.py.md)
uses RequestActivityPlan with the same authored ID and distinct explicit projection
coordinates before its own admission/lifecycle stages. Those caller operations do
not become effects owned by this module. No full server or rotation integration
review is claimed by reading these composition boundaries.

The fully read
[planning-command suite](../../tests/test_planning_commands.py.md)
has 21 PostgreSQL tests of desired/planning success, changed-actor idempotency,
historical replay, selected lineage, missing/active/revoked delivery admission,
late-action rollback and session-order races. Its entry barriers and SQL wrapper
observations have different strengths; they do not all prove an overlapping database
lock wait or universally bounded deadlock handling. The same-graph verifier projection
case is not live key rotation, and the malformed authoring test uses an uncommitted
store transaction. That companion records the precise per-test limits.

The separately fully read
[transition/replay suite](../../../../../control-plane-kit-operations/tests/test_planning_transition_replay.py)
has 17 tests. It covers transition retention/descriptor omission, no-op versus
zero-activity update/initial forms, four scenario classifications, review blockers,
real desired-pointer movement plus session close followed by fresh-service replay,
and stored graph/plan corruption. It also rejects missing/wrong transition values
in ActivityPlanningResult. Selected missing-session/store paths use doubles rather
than impossible relational deletions, and unexpected codec/store sentinels escape
by identity.

That suite's real historical replay uses an empty Sequence that fails on any new
ID allocation and compares four table counts. Its error helper checks cause/context
and canaries in str(error), without a general repr/length/log guarantee. The AST
check follows direct local function-name calls from first/replay paths and requires
compilation of a Deploy result's diff, excluding a second diff_graphs call on that
finite surface. It is not a transitive semantic proof across arbitrary aliases or
injected dependencies. Neither suite is external runtime, approval or production
credential evidence.

Read depth: complete owner915 across consecutive reads, full planning-command
test1036, full transition/replay test806/all helpers, full transition owner131 and
full key-rotation preparation helper were read. Full unit-of-work and selected
workflow, graph-authoring, authority, graph/history store and server/root contracts
were inspected or retained. Core codec/compiler/validator entry points and earlier
scenario context were selected reads, not a full Core interpreter review. No full
store, schema, record, server, rotation or transitive dependency review is claimed.
Validation was documentation-only: links, whitespace and frozen-source comparison.
No imports, executable tests, database/provider/credential operations, source/
inventory changes or publication were performed.
