Source: [control-plane-kit-operations/tests/test_deployment_program_interpreter.py](../../../../control-plane-kit-operations/tests/test_deployment_program_interpreter.py).
Maintain this document alongside its source file. When the test or relevant
interpreter/import contracts change, verify and update this companion in the same
change.

This 615-line suite has eleven tests of DeploymentProgram preparation composition.
It combines actual Core validation, transitions and planning values with four
RecordingService doubles. It proves selected commands, order, hashes, projections,
preflight and error mappings without a database. It does not establish durable
replay, transaction rollback, process restart, saved admission or provider behavior.
The separate PostgreSQL suites own persisted-state evidence.

RecordingService records (stage_name, command) before either raising a configured
BaseException object or returning its configured result. services creates operations,
desired, planning and approval doubles sharing one trace. Default results are
SimpleNamespace values with session-a, graph-desired/projection-desired/revision one,
a Core-derived plan-a and approval-a. Failure injection replaces one result with an
exception. program constructs a new actual interpreter from those doubles; no
real Operations command service is executed by this fixture.

context builds an AuthenticatedPrincipal with synthetic issuer/operator identity
and one workspace grant. command defaults to workspace-a, operator-a, parent-key,
an inline graph named desired, expected current graph/projection, absent expected
desired/revision zero, title and comment. Default scopes are workspace edit and
plan request. This uses the actual
[command contract](../src/control_plane_kit_operations/deployment_program.py.md)
but does not authenticate credentials. The fixture does not construct saved input
or supply saved_preparations.

scenario selects a named value from the
[Core scenario corpus](../../../../control-plane-kit-core/src/control_plane_kit_core/planning/scenarios.py).
plan_result validates both scenario graphs, calls Deploy and compile_activity_plan,
then wraps the transition and plan in a namespace. Selected graph builders compile
topology values with synthetic implementation endpoints. DockerRuntime and planned
activities are data in this suite; no runtime adapter is invoked. The returned
planning fixture is not derived from the command recorded by the planning double.
Several tests deliberately use the default desired graph with a separately chosen
scenario result, so this is composition evidence, not input/result congruence proof.

module imports the interpreter dynamically. If that exact module is absent it
reports a test failure; a nested ModuleNotFoundError is reraised. The dedicated
nested-dependency test temporarily replaces importlib.import_module, injects a
sentinel for the interpreter import, restores the original function in finally
and requires exception identity. This protects the test helper from hiding a
missing nested dependency; it does not test prepare's runtime exception mapping.

The preflight test removes all edges from the insert-rate-limiter desired graph
and tries workspace-edit-only, plan-request-only and no scopes. Each case must
raise DeploymentProgramAuthorizationDenied with the exact fixed message and an
empty service trace. Its error helper requires no cause/context and absence of the
graph name in str(error). The companion intrinsic-invalid test first verifies that
this graph fails actual validation, then with both scopes requires fixed state
conflict and no service calls. Together these distinguish authorization precedence
from intrinsic graph rejection; they do not spy on validator call counts or verify
real grant revocation.

The actual
[interpreter owner](../src/control_plane_kit_operations/deployment_program_interpreter.py.md)
checks the two Core policy scopes before validation and services. It does not load
external grants. Inline graph validation precedes deterministic child-key generation,
then session, desired and planning execute in order, with approval conditional on
the returned transition/readiness. This owner behavior explains the trace assertions;
the test doubles provide no evidence about the transactions behind real execute
entry points.

The exact-child test requires an approval-required result with workspace-a, plan-a
and approval-a and the four-stage trace in order. It inspects actual constructed
StartOperationSession, SetDesiredGraph, RequestActivityPlan and RequestApproval
values. Selected assertions check session workspace/actor/title/metadata; desired
session, candidate object identity and absent prior lineage/revision; planning
current and returned desired lineage/revision; and approval session/plan/scopes/
comment. The expected four child-key strings and initial intent digest are fixed
literals, not computed from production private helpers inside the test.

Those checks protect field propagation and the same desired object being passed
to authoring. They do not compare every field of every child command or require
exact command/result classes: assertIsInstance permits subclasses. The result repr
plus descriptor must omit title, comment, graph name, intent digest and parent key.
It does not test a universal secret inventory, nested result corruption or all
returned coordinate validation.

The digest test starts with the actual insert-rate-limiter graph and present prior
desired lineage/revision one. Ten variants independently change workspace, actor,
graph name, current authored ID, current projection ID, prior desired authored ID,
prior desired projection ID, desired revision, title and comment. Each changes the
metadata digest. Changing the parent key or adding workspace-read scope preserves
it. Reversing insertion order of runtime/node/edge mappings preserves graph equality
and the digest. _intent_digest here runs public prepare with recording services and
reads the first recorded session's metadata; it does not call the private production
hash helper directly.

The source's inline intent profile is deployment-program-prepare.v1, encoded with
the default graph codec and sorted compact JSON. It includes the tested intent
coordinates/content and omits parent key and scope material. Child keys instead
hash deployment-program-prepare-child.v1, workspace, stage and parent key, with a
stage-specific prefix. The fixed witnesses and mutations protect this profile, not
universal hash collision freedom, every graph representation's canonicalization,
issuer changes, all private fields or a cross-language JSON standard. No saved
intent profile is exercised here.

The terminal trace matrix supplies no-change, unsupported-implementation-transition
and fresh-deployment plans. It requires respectively no-changes, review-blocked and
approval-required projections, three/three/four calls, the same first three stage
names and only the conditional approval suffix. Despite exact in the test name,
projection types are checked with assertIsInstance and no complete descriptor
comparison is made in this matrix. It does not independently validate each plan's
activities, risk or dependency set against the full scenario expectations.

The zero-activity case validates empty graphs named before and after, derives their
real transition and plan, and asserts the activities tuple is empty. Supplying that
planning result still yields approval-required with approval last in the trace.
This protects the source's NoOpDeployment check from replacement with an
activities-is-empty shortcut. It does not persist a plan or grant approval.

Five expected-child failure cases inject OperationIdempotencyConflict at operations,
DesiredGraphCommandError at desired, ActivityPlanningGraphStateConflict at planning,
ApprovalStateConflict at approval and ApprovalAuthorizationDenied at approval.
Each requires the mapped error family, exact state-conflict or authorization message,
the failing stage as the final trace entry, no cause/context and no original error
text in str(error). The last-entry assertion shows no later recorded stage ran; it
does not independently assert the entire prefix for each failure case.

The actual owner catches the respective service error families and raises fixed
errors after leaving the except block. Approval denial remains distinguishable
from state failure. A separate unexpected-failure test injects SentinelFailure and
InvalidOperationCommand at operations and requires both to escape by identity.
The source explicitly reraises InvalidOperationCommand and does not catch arbitrary
programming exceptions. The test covers that identity behavior at operations only,
not all four stages, result-attribute access, child-constructor failures or saved
admission errors.

_assert_clean_error checks cause/context are None and canaries are absent from
str(error). It has no general numeric length assertion and does not inspect repr,
traceback locals or logs. Exact fixed messages bound the supplied mapped cases;
they must not be described as universal exception sanitization. Unexpected errors
are intentionally permitted to propagate with their original contents.

The child-key namespace test uses a 200-character parent in two workspaces. It
requires all eight resulting key strings to fit within 200 characters, four unique
keys per workspace, disjoint key sets and absence of a sixteen-character parent
substring from the first workspace's keys. It does not exercise durable duplicate
suppression, concurrent requests, direct collision attacks or every possible input
length. The initial fixed key literals provide a separate deterministic witness.

The export/source test requires four interpreter names to be a subset of root
__all__ and root attributes to be identical to module objects. It checks the
constructor's parameter-name tuple equals operations, desired_graphs, planning,
approvals, saved_preparations and the evaluated prepare return annotation is the
actual DeploymentProgramProjection union. It does not require an exact root export
set/order, inspect every parameter kind/default or demonstrate implementations of
every union variant. The actual owner defines preparation only.

Its AST helper collects modules from import and from-import statements into a set.
The source set must equal twelve allowed modules, including hashlib/json, selected
Core/Operations owners and saved_deployment_preparation. Three synthetic forbidden
imports cover psycopg, absolute PostgresUnitOfWork and a relative postgres import.
Relative levels are normalized to the Operations package without distinguishing
their depth. Sets discard import order, duplicates and imported symbol identity.

The source ast.Name set must mention the four service names and omit
PostgresUnitOfWork, UnitOfWork, stores, connection and cursor. This finite source
guard does not inspect attribute names, strings, dynamic imports, called dependency
implementations or injected service effects. It is not proof of a transitive
effect-free graph. Actual imports were inspected alongside the test so its structural
claims remain tied to the current owner rather than inferred from the allowlist.

For persistence, the separate
[preparation tests](test_deployment_program_preparation.py.md)
exercise real services and PostgreSQL stage outcomes. They do not become coverage
of this recording suite merely because both call prepare. Likewise, saved grouped
admission and historical replay belong to other tests. This suite adds no production
network/auth surface and performs no credential/provider calls; its synthetic
authority, selective redaction assertions and permissive service results are the
important security and evidence limits.

Read depth: the full 615-line test, all helpers and full 301-line interpreter were
read in this continuous slice and retained for authoring. Full command/identity
reading and selected policy checks, projection definitions and Core scenario/
topology helpers were retained, with insert-rate-limiter refreshed. Root exports
and actual interpreter imports were inspected. This is not a full Core planning,
server or transitive dependency review. Validation was documentation-only: local
links, whitespace and frozen-source comparison. No application imports, executable
tests, database/provider calls, source/inventory edits or publication were performed.
