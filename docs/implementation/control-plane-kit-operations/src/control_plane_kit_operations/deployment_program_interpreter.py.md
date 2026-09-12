Source: [control-plane-kit-operations/src/control_plane_kit_operations/deployment_program_interpreter.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/deployment_program_interpreter.py).
Maintain this document alongside its source file. When the source or relevant
service contracts change, verify and update this companion in the same change.

This 301-line owner composes durable deployment preparation from existing Operations
services. DeploymentProgram exposes prepare; it does not implement progression,
execution, observation, current-graph advancement or cleanup. Its effect-free
module description means no runtime-provider effects in this composition, not
absence of database writes. Session, desired graph, plan and approval-request truth
can be persisted before prepare returns or raises.

The module defines DeploymentProgramError as RuntimeError and two subclasses,
DeploymentProgramAuthorizationDenied and DeploymentProgramStateConflict. The package
root reexports those three errors and DeploymentProgram by identity. This module
has no explicit __all__. Its constructor takes operations, desired_graphs, planning
and approvals, plus optional keyword-only saved_preparations. It stores those
dependencies without runtime type checks or constructing a database connection.
Annotations identify the normal service boundaries; they do not seal injected
objects against alternate execute/start behavior.

The input
[command owner](deployment_program.py.md)
normally validates exact command component types, context, lineage/revision coupling,
key and text bounds. prepare itself does not revalidate the outer command type or
reconstruct its nested authority. It first reads context.granted_scopes and applies
the actual
[Core policies](../../../../../control-plane-kit-core/src/control_plane_kit_core/policies.py):
workspace edit requires INSTANCE_WORKSPACE_EDIT, and plan request requires
PLAN_REQUEST. Missing either raises the fixed authorization error before graph
validation, child-key construction or any service call. These are checks of supplied
trusted-context scopes on every invocation, not credential verification or a fresh
read of grants from an authorization registry.

For inline desired graphs, validate_graph(...).require_valid() runs before any
stage. GraphValidationError becomes the fixed state-conflict message after the
except block. The exact SavedDesiredTopologyRevision branch skips this inline
validator because the saved admission service owns loading and checking graph
truth. Other exceptions from policy or graph validation are not universally caught.

_child_keys always constructs four IdempotencyKey values, for session, desired,
plan and approval. Each is deployment-prepare.v1:<stage>: followed by the SHA256 of
a JSON object containing profile deployment-program-prepare-child.v1, stage,
workspace_id and parent_idempotency_key. The same parent/workspace/stage therefore
selects the same retry key across inline and saved input modes. Changing actor or
desired intent does not select a new key; the downstream intent commitment must
reject conflicting reuse. The saved branch computes but does not use the desired
stage key. Keys remain bounded independent of parent-key length and do not embed
the parent string directly; hashes are not encryption or authorization evidence.

Inline preparation constructs StartOperationSession with workspace, actor, title,
session child key and one metadata entry, deployment_prepare_intent_sha256. Its
_intent_digest hashes profile deployment-program-prepare.v1, workspace, actor,
DEFAULT_GRAPH_CODEC.encode(desired), both expected lineages, expected desired
revision, title and approval comment. Absent expected desired is encoded as null.
The parent key, scopes, issuer and other principal fields are absent. Retry key
identity and preparation intent are intentionally separate commitments.

Both local hash helpers use json.dumps with sort_keys=True and separators=(",", ":"),
UTF-8 bytes and hashlib.sha256(...).hexdigest(). The implementation leaves other
JSON options at their defaults. Canonicalization here is this codec/JSON profile,
not a promise of cross-profile equivalence for arbitrary encoders or all future
graph representations. The hash carries private intent into durable metadata
without putting that content directly in the metadata field; the ordinary session
still stores its title/actor separately, and approval requests may store comments.

After session execution, inline preparation takes session_result.session.session_id
and constructs SetDesiredGraph. It passes the same candidate graph object, expected
desired authored/projection coordinates or None, expected desired revision and
desired child key. It then consumes graph_version_id, desired_realized_projection_id
and desired_graph_revision from that service result. These returned coordinates,
rather than guessed graph IDs, become planning inputs.

The actual
[workflow service](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/workflows.py)
commits session and initial action together. The selected
[desired/planning services](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/planning.py)
each own another unit of work. Desired authoring persists graph/projection, desired
workspace state and action within its stage; planning persists plan plus action.
The actual
[Postgres unit of work](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py)
requests commit through commit() and physically commits only on successful exit.
prepare has no outer transaction encompassing these service execute calls and no
rollback of an earlier completed stage when a later one fails.

Saved preparation requires an injected service; otherwise it raises state conflict
with the distinct message saved preparation service is unavailable. It calls
saved_preparations.start(command, session_key=keys["session"]). SavedPreparationError
is translated to the ordinary fixed state conflict after leaving the except block.
On success, prepare uses the returned session ID and the command's expected desired
lineage/revision. It does not run SetDesiredGraph, select a draft revision, advance
the catalogue head or replace selected desired truth.

The fully inspected
[saved admission owner](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/saved_deployment_preparation.py)
groups fresh admission, session/initial action and relational saved-source insertion
in one unit of work. It checks exact saved input and scopes, locks the session key,
then on the fresh path locks workspace and draft, checks expected lineage/generation,
rejects tombstoned drafts, validates immutable revision/graph/projection evidence and
active product references, and calls operations.start_in_unit_of_work. That inner
session entry does not commit; the saved service commits the group. Planning and
approval still happen later through their ordinary separate transactions.

The saved owner computes its own deployment-program-prepare-saved.v1 intent digest
and closed saved-source metadata. This interpreter does not supply a caller-computed
saved digest. Saved replay checks the retained commitment, session/start action,
immutable graph evidence and relational source/revision, while its fresh path owns
current-selection/product admission. Replay does not repeat all fresh mutable
admission checks. Historical completed replay can therefore differ from starting a
new plan after selection drift; the planning service owns that later distinction.
The saved owner validates selected session/action acknowledgment fields, but that
does not establish universal acknowledgment validation in this interpreter.

Both branches converge on RequestActivityPlan with returned session ID, supplied
workspace/actor/current lineage, the branch's desired coordinates/revision and plan
child key. The result supplies plan_record.plan_id, transition and plan readiness.
prepare creates a DeploymentProgramReference using the command workspace and returned
plan ID. An isinstance(NoOpDeployment) transition yields DeploymentNoChanges first.
Otherwise an unready plan yields DeploymentReviewBlocked. A ready graph update
continues even if its activities tuple is empty; zero activities alone does not
mean no changes.

The continuing branch calls the
[approval service](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/approvals.py)
with session/plan IDs, actor, current supplied scopes, approval child key and comment.
It returns DeploymentApprovalRequired containing the reference and returned request
ID. This requests review, not a decision, execution lease or permission to perform
destructive effects. The selected
[projection definitions](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/deployment_program_projections.py)
provide pure frozen values containing those public coordinates. The broad annotated
DeploymentProgramProjection union does not mean this method returns every variant.

prepare does not inspect replayed flags, load a terminal projection from a private
cache or implement its own retry loop. Reinvocation repeats preflight and child
calls with deterministic keys; actual services own durable replay/fingerprints.
Session replay checks its intent; desired replay consumes recorded action/graph
evidence; planning replay checks plan linkage and rederives its transition/plan;
approval replay checks request/action evidence. Existing completed stages remain
available to a later invocation, while fresh later stages can still reject changed
workspace/session truth. No compensation, automatic resume or cleanup is scheduled.

Result handling is deliberately thin. The interpreter accesses expected attributes
and constructs the next command or projection, but does not require exact service
result classes, compare all returned lineage/actor/workspace fields with the input,
verify a commit acknowledgment, or reread every written record. Reference and child
constructors impose some local shape bounds; they are not a substitute for durable
result congruence. Recording-service tests use SimpleNamespace results, making this
dependency contract visible. A malformed or incongruent injected result can cause
an uncaught attribute/constructor error or supply plausible coordinates this layer
does not independently verify.

_execute_state catches its stage-specific OperationCommandError,
DesiredGraphCommandError or ActivityPlanningError and raises the fixed state conflict
outside the except block, avoiding retention of those caught exceptions as context.
It explicitly reraises InvalidOperationCommand unchanged. _execute_approval maps
ApprovalAuthorizationDenied to the fixed authorization error and other
ApprovalWorkflowError to state conflict. These mappings preserve the distinction
between denial and unavailable state for their covered families.

This is not a blanket exception/redaction boundary. Child command construction is
evaluated before entering the helper; result access/projection construction occurs
after it. Hash encoding errors, arbitrary runtime exceptions, uncaught database
errors and programming failures can propagate. The inspected saved test deliberately
expects a real UniqueViolation to escape after rollback. Fixed messages omit rejected
values for covered errors; neither all exception chains nor all logs from arbitrary
injected services are sanitized by this owner.

The inspected production caller is the
[cpk-server application adapter](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/cpk_server.py).
Its composition factory supplies all four ordinary services and
SavedDeploymentPreparationService when it builds the default program. Its prepare
route requires workspace-edit/plan-request scopes, chooses exactly one inline or
saved input mode, builds PrepareDeploymentProgram from trusted context/payload and
calls prepare. It maps authorization/state errors to 403/409 and selected malformed
input errors to 400. Its response helper accepts the exact three preparation result
types and emits status, workspace, plan and optional approval-request IDs. These
selected adapter functions were inspected; this is not a full server/auth review.

The fully read
[interpreter suite](../../../../../control-plane-kit-operations/tests/test_deployment_program_interpreter.py)
uses recording services, actual Core transition/planning values and synthetic
principals. Its eleven tests cover missing-scope preflight, intrinsic-invalid graph
rejection, exact child command trace/key/digest fixtures, three result branches,
zero-activity update, five mapped failures, two unchanged unexpected failures,
workspace/key bounds, exports/signature/finite AST imports and a nested-import helper
failure. That last test checks the test helper's import behavior, not prepare.

The digest test varies ten included coordinates/content fields, excludes parent key
and added read scope, and preserves the digest under selected graph-map reordering.
These fixtures protect the current profile; they do not prove universal collision
freedom, all descriptor ordering laws or every principal-field exclusion. Recording
results are not persistence evidence. The tests do not exercise saved input in this
file, arbitrary result corruption, every uncaught-error family or transitive absence
of effects through all imports/injected services.

The separately read
[PostgreSQL preparation suite](../../tests/test_deployment_program_preparation.py.md)
checks inline projection replay, stage counts, six changed-intent conflicts,
workspace namespaces, partial persisted state and metadata-only canaries. Its
post-commit sentinels and fresh service objects do not restart a process/database.
The separately fully read
[saved preparation suite](../../../../../control-plane-kit-operations/tests/test_saved_preparation.py)
and direct saved fixture exercise saved selection/revision evidence, stage resume,
historical replay, corrupted evidence, grouped session rollback and concurrency
cases. The suite's late duplicate action ID rolls back its new session; a separate
uncommitted inner-session call remains invisible to a second connection. Its
inherited fixture/SQL instrumentation and other saved-source/adapter suites were
not fully reviewed in this slice, so no exhaustive fixture, lock or source-insert
fault-matrix coverage is claimed here.

Read depth: all 301 source lines, full interpreter test615, full saved owner210,
saved test459 and direct saved fixture120 were read, with full command226,
inline preparation test519 and unit-of-work reading retained. Selected actual
policy, service execution/replay, graph authoring, projection, root export and
server composition/route contracts were inspected. No full larger service/store,
server, policy or inherited saved-fixture review is claimed. Validation was
documentation-only: local links, whitespace and frozen-source comparison. No
application imports, tests, database/provider/credential calls, source/inventory
changes or publication were performed.
