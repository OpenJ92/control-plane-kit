# EXTRACT.OPERATIONS.ACTIVITY Run 0001

Status: #871 activity-realization boundary in progress.

Parent: #869

Topology:

```text
#870
  -> #871 -> #872 -> #873 -> #874 -> #875
    -> #880 -> #881
      -> #876 -> #877 -> #878 -> #879
```

## #870 Runtime Law Inventory

#870 is intentionally documentation and artifact work. It does not implement the
Docker runtime. Its job is to make the frozen behavior visible before the
adapter boundary changes in #871.

Machine-readable evidence:

```text
artifacts/extraction/activity-870-runtime-law-inventory.json
```

The artifact records 11 law cards and 7 seeded integration scenarios. The
important split is:

```text
core
  ActivityPlan, graph/product/socket language, pure scheduling contracts

operations
  durable workflow services, Postgres stores, UnitOfWork, coordinator, read models

cpk-server
  HTTP/MCP process wrapper over operations services

control-plane-kit-servers
  OCI product descriptors, Dockerfiles, published images, product process code
```

## Frozen Reference Lookover

The frozen implementation had the full interpreter stack in one package. The
files most relevant to ACTIVITY were:

```text
tests/test_docker_effects.py
tests/test_execution_coordinator.py
docs/DEPLOY_PROGRAM.md
control_plane_kit/workflows/execution_coordinator.py
control_plane_kit/workflows/planning.py
control_plane_kit/workflows/execution_admission.py
control_plane_kit/workflows/run_lifecycle.py
```

The extracted tree currently has the pure plan compiler and durable coordinator
shape, but the coordinator adapter receives only a `PlannedActivity`:

```python
class ActivityExecutionAdapter(Protocol):
    """Effect-proof adapter called only after durable intent commits."""

    def execute(self, activity: PlannedActivity) -> ActivityExecutionOutcome: ...
```

That is enough for fake execution, but not enough for real product realization.
The next issue must add a boundary that carries pinned realization material
without creating a second coordinator, saga, scheduler, or effect language.

## Law Card Summary

The #870 artifact classifies these laws:

| Law | Classification | Next Issue |
| --- | --- | --- |
| ActivityPlan remains pure graph-diff output | operations unit law | #871 |
| Coordinator records intent, commits, then calls adapter | operations unit law | #874 |
| Execution uses admitted plan truth, not mutable current graph | Docker/Postgres integration | #871/#872/#873 |
| Docker mutation requires proven ownership | Docker/Postgres integration | #872 |
| Secret values stay out of descriptors and argv | Docker/Postgres integration | #872 |
| Socket edges drive runtime dependency bindings | Docker/Postgres integration | #873 |
| Process start is not health/readiness | Docker/Postgres integration | #872/#874/#875 |
| Observations do not rewrite desired topology | Docker/Postgres integration | #874/#875 |
| Approval gates admission | operations unit law | #880/#881 |
| Published OCI digest is acceptance truth | server-product law | #878 |
| Public control portals are future work | future non-goal | #882 |

## Seeded Scenario Matrix

The seeded local-Docker ACTIVITY scenarios use only:

```text
cpk-server
hello-server
http-active-router
http-multiplexer
postgres-server descriptor
```

The matrix includes:

1. Initial cpk-server deployment backed by Postgres descriptors.
2. Standalone hello-server deployment.
3. Hello-to-hello HTTP dependency once per-instance dependency binding exists.
4. HTTP active router forwarding to a hello-server target.
5. HTTP multiplexer with a primary hello and optional observer hello.
6. Teardown that removes owned compute while preserving retained Postgres data.
7. Public cpk-server workflow acceptance over HTTP/MCP.

## Product Parameterization Gaps

#870 found five concrete gaps to carry forward:

- Hello currently ships `HELLO_DEPENDENCIES_JSON=[]` and has no base requirement
  socket, so dependency calls need per-instance parameterization or descriptor
  evolution in #873.
- Router target binding must be derived from graph edges, not local smoke-script
  variables, in #873.
- Multiplexer binding must distinguish required `primary` from optional
  `observer-a` and `observer-b` in #873.
- Postgres needs secret delivery and retained data handling in the local Docker
  interpreter in #872.
- cpk-server acceptance must use published image digest truth after backend
  runtime behavior changes in #878.

## Topology Decision

No new child issue is required before #871. The current order remains coherent:

```text
#870 inventory
  -> #871 adapter seam
    -> #872 minimal Docker interpreter
      -> #873 dependency binding
        -> #874 coordinator observations
          -> #875 current graph advancement
            -> #880/#881 approval queue public workflow
              -> #876/#877/#878/#879 acceptance and closeout
```

The decisive #871 handoff is:

```text
PlannedActivity alone is too small for real realization.

Keep the existing coordinator, but define a richer pure operations boundary:

  admitted run + pinned plan + graph material + registered products
    -> realization context
      -> ActivityExecutionAdapter
        -> ActivityExecutionOutcome
```

The adapter must still be called only after durable intent commits, and it must
not load mutable current graph truth itself.

## #871 Activity Realization Boundary

#871 turns the fake-execution adapter seam into the boundary needed by the local
Docker interpreter without implementing Docker behavior yet.

The old extracted seam was intentionally small:

```python
class ActivityExecutionAdapter(Protocol):
    def execute(self, activity: PlannedActivity) -> ActivityExecutionOutcome: ...
```

That shape worked for fake effects, but a real runtime interpreter needs the
durable material pinned by admission. The new boundary is:

```python
@dataclass(frozen=True)
class ActivityRealizationContext:
    activity: PlannedActivity
    request: ExecutionRequestRecord
    run: ActivityRunRecord
    plan_record: ActivityPlanRecord
    base_graph: GraphVersionRecord
    desired_graph: GraphVersionRecord
    registered_products: tuple[RegisteredProduct, ...]
    authority: ExecutionWorkerAuthority
    intent_event: ActivityEventRecord


class ActivityExecutionAdapter(Protocol):
    def execute(
        self,
        context: ActivityRealizationContext,
    ) -> ActivityExecutionOutcome: ...
```

This preserves the external-effect law:

```text
short transaction: record durable STEP_STARTED intent
  -> commit
    -> adapter receives pinned ActivityRealizationContext
      -> short transaction: record result event and projection
```

The coordinator now loads the admitted request, run, exact plan record, pinned
base/desired graph records, and active registered products before scheduling.
It validates that this material belongs to the execution workspace and admitted
plan before any step-start intent is written. The public realization context
then carries the already-written `STEP_STARTED` event so the adapter can prove
which durable intent it is satisfying.

The focused regression introduced in #871 corrupts the pinned desired graph
workspace and proves:

```text
adapter calls = []
persisted events = [run_opened, run_started]
```

So incoherent pinned material cannot create a false step intent.

Validation evidence so far:

```text
./control-plane-kit-operations/test.sh
  103 tests passed
  compileall passed
  control-plane-kit-operations import ok
```

#872 handoff:

The minimal Docker interpreter should consume `ActivityRealizationContext`; it
must not import stores, query the current graph pointer, or reconstruct product
truth itself. Product realization should be derived from:

```text
context.activity
context.plan
context.base_graph
context.desired_graph
context.registered_products
```

The next implementation should preserve product-generic runtime dispatch and
keep Hello/router/multiplexer specifics in descriptor data, seeded products, or
future product-specific renderers rather than in the operations coordinator.

## #907 / #916 Runtime Effect Contract Pivot

#907 corrected the transitional #872 shape. Docker realization must not remain
inside operations and must not become an `operations[docker]` optional extra.
The boundary is now expressed as a pure core language:

```text
core:
  RuntimeEffectRequest

interpreter:
  RuntimeEffectRequest -> IO RuntimeEffectResult

operations:
  ActivityJournal x RuntimeEffectResult -> ActivityJournal'
```

#916 introduces `control_plane_kit_core.runtime_effects` as the value language
between durable operations and concrete runtime interpreters. The request carries
only secret-free, pinned material:

```python
RuntimeEffectRequest(
    effect_id=context.intent_event.event_id,
    kind=RuntimeEffectKind.REALIZE_ACTIVITY,
    runtime_kind=RuntimeKind.DOCKER,
    source=RuntimeEffectSource(...),
    activity_id=context.activity.activity_id,
    operation=context.activity.operation,
    products=(RuntimeProductMaterial(...),),
)
```

Operations now has a translator:

```python
runtime_effect_request_for_context(context)
```

That function interprets already-loaded `ActivityRealizationContext` material
into a pure request. It does not query stores, import Docker SDK, import
`control-plane-kit-interpreters`, or select mutable current graph truth. For
node activities, it uses the pinned graph node metadata to find the matching
`RegisteredProduct` already present in the context, preserving the exact
`ProductReference` and canonical descriptor document.

The remaining old operations-owned `DockerProductRealizationAdapter` is now
explicitly transitional. #917 must move Docker execution into
`control-plane-kit-interpreters`; #919 must reduce operations to translation,
dispatch, and persistence.

## #872 Minimal Docker Product Realization

#872 adds the first real local Docker activity interpreter in extracted
operations. It is intentionally small and product-generic: it consumes the
`ActivityRealizationContext` introduced in #871 and never imports stores,
selects current graph truth, or reconstructs registration state during the
external effect.

The new adapter boundary is:

```python
class DockerRealizationClient(Protocol):
    def inspect_network(self, name: str) -> DockerResourceInspection | None: ...
    def create_network(self, name: str, *, labels: dict[str, str]) -> None: ...
    def inspect_container(self, name: str) -> DockerResourceInspection | None: ...
    def pull_image(self, image: str) -> None: ...
    def run_container(...): ...
    def start_container(self, name: str) -> None: ...


@dataclass(frozen=True)
class DockerProductRealizationAdapter:
    client: DockerRealizationClient

    def execute(
        self,
        context: ActivityRealizationContext,
    ) -> ActivityExecutionOutcome: ...
```

The concrete implementation included in operations is CLI-backed for now. The
important design decision is that operations depends only on the protocol seam,
not on the Python Docker SDK. A later Docker runtime/interpreter package can
provide a `DockerSdkClient` behind the same protocol without changing the
coordinator or durable service boundary.

Supported in #872:

```text
StartRuntime(Docker)
  -> inspect/create one owned private Docker network
  -> label by workspace, plan, desired graph, runtime, and owner

StartNode(OCI container)
  -> require owned digest-pinned registered product material
  -> reject foreign name collisions before pull/run
  -> pull immutable OCI reference
  -> run private container on the planned network
  -> publish provider-socket network aliases
  -> pass only explicit non-secret public environment values
```

Unsupported, deliberately before mutation:

```text
secret deliveries
configuration artifacts
retained data resources / volumes
non-Docker runtimes
non-OCI product nodes
local tags without sha256 digest pins
```

This is a structural limitation, not a shortcut. The Postgres seeded product has
both secret material and retained data, and generic operations does not yet have
a typed data mount target or secret resolver. Until those exist, the correct
runtime result is explicit `OPERATOR_REVIEW` unsupported evidence with no Docker
mutation.

Focused tests prove:

```text
owned network creation preserves workspace/plan/graph labels
digest-pinned node start pulls and runs with private aliases
foreign container collision fails before pull or run
secret + retained-data products are unsupported before mutation
```

Validation evidence:

```text
git diff --check
./control-plane-kit-operations/test.sh
  107 tests passed
  compileall passed
  control-plane-kit-operations import ok
./test.sh
  1219 tests passed
```

#873 handoff:

Dependency binding should build on the #872 private-network node start. The next
step is to derive runtime parameters from graph edges and product contracts, not
from product-specific branches in the Docker adapter. In particular:

```text
Hello dependency/env binding
router target binding
multiplexer primary/observer binding
Postgres secret/data/retention handling
published-image digest truth versus local tags
```

Postgres realization remains blocked until operations has typed secret
resolution and retained data mount material. If that scope becomes necessary for
the ACTIVITY live matrix before #877, create a focused child issue instead of
adding implicit secret or volume behavior to `DockerProductRealizationAdapter`.

## #873 Product Dependency Binding

#873 confirms that dependency binding is already represented by the extracted
core graph language and hardens the Docker realization boundary so the fact does
not become implicit.

The important existing pure transformation is:

```text
SocketConnection(provider, provider_socket, consumer, requirement_socket)
  -> compile_topology
    -> Edge(env_assignments)
      -> consumer Node.socket_environment
        -> Node.non_secret_environment()
```

The Docker adapter then consumes the compiled node material:

```python
self.client.run_container(
    ...,
    environment=node.non_secret_environment(),
    ...,
)
```

No second dependency-binding engine was introduced in operations. This is the
right boundary because protocol compatibility, required/optional sockets, and
environment binding completeness are pure graph concerns. Runtime realization
should receive an already-validated node and pass its non-secret runtime material
to Docker without scanning containers or recognizing product names.

Focused #873 coverage proves:

```text
router active requirement
  app.internal -> router.active
  ACTIVE_TARGET_URL == app.internal endpoint URL

multiplexer requirements
  primary.internal -> multiplexer.primary
  observer.internal -> multiplexer.observer-a
  MULTIPLEXER_PRIMARY_URL == primary endpoint URL
  MULTIPLEXER_OBSERVER_A_URL == observer endpoint URL
  absent optional observer-b does not fabricate an env value
```

The descriptor language is sufficient for the current HTTP seed products:

```text
http-active-router
  active: HTTP requirement -> ACTIVE_TARGET_URL

http-multiplexer
  primary: HTTP requirement -> MULTIPLEXER_PRIMARY_URL
  observer-a: optional HTTP requirement -> MULTIPLEXER_OBSERVER_A_URL
  observer-b: optional HTTP requirement -> MULTIPLEXER_OBSERVER_B_URL
```

The Postgres seeded descriptor remains different. Its graph-visible provider
socket is ready, but realization still needs typed secret resolution and retained
data mount material before operations can start it safely. That remains a later
focused child if the #877 live matrix requires database containers during this
ACTIVITY leg.

Validation evidence so far:

```text
git diff --check
./control-plane-kit-operations/test.sh
  109 tests passed
  compileall passed
  control-plane-kit-operations import ok
./test.sh
  1219 tests passed
```

#874 handoff:

#874 can rely on the Docker adapter receiving fully compiled runtime
environment for edge-connected HTTP products. It should focus on coordinator
dispatch and observation persistence:

```text
durable STEP_STARTED intent
  -> DockerProductRealizationAdapter executes outside transaction
    -> STEP_SUCCEEDED / STEP_FAILED / STEP_UNSUPPORTED / STEP_UNCERTAIN
      -> ObservationRecord / read projection evidence
```

Do not add graph-edge binding logic to the coordinator. If a runtime value is
missing, first inspect graph compilation/validation and product descriptors;
only then consider adapter-level failure evidence.

## #874 Coordinator Result and Observation Persistence

#874 connects real adapter outcomes to the existing observed-state store without
creating another journal or projection.

The new outcome shape is:

```python
@dataclass(frozen=True)
class ActivityExecutionOutcome:
    kind: EffectResultKind
    evidence: BoundedEvidence = field(default_factory=BoundedEvidence)
    failure: FailureEvidence | None = None
    observations: tuple[ObservationRecord, ...] = ()
```

This keeps observations as typed durable values. Adapters do not return raw
dictionaries, and the coordinator does not infer health from process effects.

The coordinator flow now has the intended post-effect transaction boundary:

```text
short transaction:
  STEP_STARTED durable intent
commit

adapter.execute(ActivityRealizationContext)

short transaction:
  STEP_SUCCEEDED / STEP_FAILED / STEP_UNSUPPORTED / STEP_UNCERTAIN
  plus any ObservationRecord values from the adapter
commit
```

`_record_step_event()` writes the event and observations through the same
UnitOfWork connection before committing. If adapter observation evidence names a
foreign workspace after an effect has returned, the coordinator records
`STEP_UNCERTAIN` with `adapter-observation-workspace-mismatch` rather than
leaving an effect-without-result gap or persisting the foreign row.

The local Docker adapter now emits a narrow process observation for `StartNode`:

```python
ObservationRecord(
    observation_id=f"{context.intent_event.event_id}:process-started",
    workspace_id=context.request.identity.workspace_id,
    subject_id=node_id,
    status=ObservationStatus.PROCESS_STARTED,
    observed_at=context.intent_event.occurred_at,
    graph_id=context.plan_record.desired_graph_id,
    probe_kind=ProbeKind.PROCESS,
    probe_outcome=ProbeOutcome.PROCESS_RUNNING,
)
```

That deliberately says only "the process was started/running." It does not claim
transport reachability, application health, or readiness. Runtime network
creation remains event evidence only.

Validation evidence:

```text
git diff --check
./control-plane-kit-operations/test.sh
  110 tests passed
  compileall passed
  control-plane-kit-operations import ok
./test.sh
  1219 tests passed
```

#875 handoff:

#875 can now rely on durable realization evidence existing in two independent
but correlated streams:

```text
ActivityEventRecord
  lifecycle / saga truth

ObservationRecord
  runtime observation truth, projected separately from graph truth
```

The next issue should advance the current graph pointer only after accepted
successful realization. It should not treat observations as desired graph
mutation, and it should not infer readiness from the process-start observations
added here.

## #875 Guarded Current Graph Advancement

#875 restores the extracted operations application service that turns complete
execution evidence into one guarded current-graph projection update.

The key boundary is:

```text
approved/admitted plan
  -> claimed run
    -> complete successful activity event journal
      -> CurrentGraphAdvancementCommandService
        -> CURRENT_GRAPH_ADVANCED event
        -> ADVANCE_CURRENT_GRAPH operation action
        -> workspace current_graph_id compare-and-set
```

This preserves the distinction between truth and projection:

```text
ActivityEventRecord
  append-only lifecycle / saga truth

Workspace.current_graph_id
  cached current-topology pointer advanced only from accepted evidence

ObservationRecord
  runtime observation truth, never graph mutation
```

The command shape is:

```python
@dataclass(frozen=True)
class AdvanceCurrentGraph:
    workspace_id: str
    run_id: str
    plan_id: str
    expected_current_graph_id: str
    desired_graph_id: str
    authority: ExecutionWorkerAuthority
    idempotency_key: IdempotencyKey
```

The service validates all pinned identities before mutation:

```text
request.workspace == command.workspace
request.plan == command.plan
run.plan == command.plan
plan.session == request.session
plan.base_graph == command.expected_current_graph_id
plan.desired_graph == command.desired_graph_id
workspace.current_graph == command.expected_current_graph_id
workspace.desired_graph == command.desired_graph_id
base and desired graph records belong to the workspace
request is still claimed by the advancing worker
worker has execution:operate
```

Advancement uses the existing workspace CAS primitive:

```python
stores.workspaces.compare_and_set_current_graph(
    command.workspace_id,
    expected_graph_id=command.expected_current_graph_id,
    replacement_graph_id=command.desired_graph_id,
)
```

The durable event stream is still the saga journal, but extracted core now wants
pure `ActivityJournalEvent` values. #875 therefore moved the coordinator's
private event projection into one shared operations interpreter:

```python
def activity_journal_events(
    events: tuple[ActivityEventRecord, ...],
) -> tuple[ActivityJournalEvent, ...]:
    ...
```

Both `ExecutionCoordinator` and `CurrentGraphAdvancementCommandService` now use
that same adapter before calling:

```python
project_activity_journal(plan, activity_journal_events(events))
derive_schedule(plan, projection.state)
```

This matters because advancement is not allowed to trust a naked
`ActivityRunStatus.SUCCEEDED` projection. It also requires reconstructible saga
success:

```text
latest event is RUN_SUCCEEDED
exactly one terminal RUN_SUCCEEDED exists
no failed / unsupported / compensating / cancelled evidence appears
no in-flight or uncertain journal state remains
successful step evidence exactly covers the ActivityPlan
```

Focused #875 coverage proves:

```text
complete durable success advances once and exact replay returns original evidence
uncertain, unsupported, or failed step evidence cannot advance
missing scope, foreign worker, and stale graph pointers fail closed
changed idempotent intent conflicts without a second event
late operation-action write failure rolls back pointer and event
concurrent advancement has one winner
```

Validation evidence:

```text
git diff --check
./control-plane-kit-operations/test.sh
  116 tests passed
  compileall passed
  control-plane-kit-operations import ok
./test.sh
  1219 tests passed
```

#880 handoff:

The durable execution spine now has planning, admission, claim/start, execution
result/observation persistence, and guarded advancement. Before full public
workflow acceptance, #880 should expose the approval queue/read model needed for
manager review:

```text
operator requests approval
  -> manager lists pending approvals
    -> manager inspects plan/risk/detail
      -> manager approves or rejects
```

#880 should not bypass approval by inserting rows directly in public acceptance
paths. It should build on the existing approval records and read-service
projection boundaries, then hand off to #881 for cpk-server HTTP/MCP exposure.

## #880 Approval Queue Read Model And Review Contract

#880 completed the manager-facing read contract needed between plan preparation
and approval decision. The important distinction is:

```text
pending approvals
  = bounded queue rows for triage

approval detail
  = one approval request
    + the exact pinned plan/risk/recovery context being reviewed
```

The core contract language now names this projection explicitly:

```python
ReadProjectionKind.APPROVAL_DETAIL = "approval-detail"

_ProjectionDefinition(
    "read.approval-detail",
    ReadProjectionKind.APPROVAL_DETAIL,
    "ApprovalDetailReadResponse",
    ReadProjectionPolicy.PINNED_PLAN_AND_RECOVERY,
)
```

Adapter parity also knows the same operation, route, tool, and response shape:

```text
read.approval-detail
  -> HTTP route read.approval-detail
  -> MCP tool get_approval_detail
  -> ApprovalDetailReadResponse
```

Operations implements the projection without creating new approval truth:

```python
approval = _approval_in_workspace(store, workspace_id, approval_request_id)
plan = _plan_in_workspace(store, workspace_id, approval.plan_id)
if plan.session_id != approval.session_id:
    raise ReadModelError(...)

payload = _plan_descriptor(...)
payload["risk_summary"] = _risk_summary(plan)
payload["recovery"] = self._recovery_for_plan(workspace_id, plan)
```

The projection therefore reconstructs manager review context from existing
durable records:

```text
ApprovalRequestRecord
  -> ActivityPlanRecord
    -> pinned base/desired graph truth
      -> risk summary
      -> recovery transition
```

Focused #880 coverage proves:

```text
canonical read projection set includes read.approval-detail
HTTP route inventory includes /workspaces/{workspace_id}/approvals/{approval_id}
adapter parity binds get_approval_detail to the same projection schema
security parity keeps approval detail read-only and read-scoped
Postgres-backed InstanceReadService.approval_detail joins approval to plan review context
```

Validation evidence:

```text
git diff --check
./control-plane-kit-core/test.sh
  379 tests passed
  compileall passed
  control-plane-kit-core import ok
./control-plane-kit-operations/test.sh
  117 tests passed
  compileall passed
  control-plane-kit-operations import ok
./test.sh
  1219 tests passed
```

#881 handoff:

#881 should expose the complete approval workflow through cpk-server public
adapters. The read side now has the queue and detail projection. The remaining
public workflow is:

```text
operator requests approval
  -> manager lists pending approvals
    -> manager reads approval detail
      -> manager approves or rejects
        -> operator admits only with current approval
```

Do not bypass approval in #881 acceptance paths. Public HTTP/MCP calls should
use the same operations services and UnitOfWork boundaries as direct operations
tests.

Future runtime handoff:

The ACTIVITY leg currently keeps Docker realization behind the operations
adapter seam. A future real `DockerRuntime` implementation should consider a
Python Docker SDK-backed adapter as one implementation of that seam, while
preserving the existing split-transaction external-effect law.

## #881 Cpk-Server Approval Workflow Adapters

#881 exposed the approval workflow through the cpk-server adapter surface without
creating another approval queue, approval decision service, or public command
vocabulary. The new public command contract is:

```text
command.approval.request
  -> /workspaces/{workspace_id}/plans/{plan_id}/approval
  -> ApprovalRequestRequest
  -> ApprovalRequestResponse
  -> PLAN_WRITE
```

This completes the public approval path started in #880:

```text
operator requests approval
  -> manager lists pending approvals
    -> manager reads approval detail
      -> manager approves or rejects
        -> operator admits only with current approval
```

Core now records request-approval parity beside the existing approval decision
contract:

```python
OperationParity(
    command_name="approval.request",
    route_id="command.approval.request",
    mcp_tool_name="request_approval",
    service_role=ControlPlaneServiceRole.APPROVAL,
    request_schema="ApprovalRequestRequest",
    response_schema="ApprovalRequestResponse",
    approval_policy=ApprovalPolicy.SUBMITS_FOR_APPROVAL,
)
```

The cpk-server operations adapter translates that public route into the existing
approval service command:

```python
RequestApproval(
    session_id=...,
    plan_id=...,
    actor_id=...,
    actor_scopes=...,
    idempotency_key=...,
    comment=...,
)
```

The decision route continues to use `DecideApproval`, so request and decision
share the same `ApprovalCommandService` and UnitOfWork boundary.

Focused #881 coverage proves:

```text
core command parity includes approval.request
HTTP route inventory exposes command.approval.request with PLAN_WRITE scope
activity-history parity records accepted and rejected approval commands
cpk-server translates request payloads to RequestApproval
public approval loop persists request -> reads queue -> reads detail -> decides
```

The public approval-loop proof intentionally seeds only workspace/session/plan
truth. It does not insert approval rows directly. The approval request is created
through `command.approval.request`, then observed through the #880 queue/detail
read projections, then decided through `command.approval.decide`.

Validation evidence:

```text
git diff --check
./control-plane-kit-core/test.sh
  379 tests passed
  compileall passed
  control-plane-kit-core import ok
./control-plane-kit-operations/test.sh
  119 tests passed
  compileall passed
  control-plane-kit-operations import ok
./test.sh
  1219 tests passed in 217.657s
```

#876 handoff:

#876 can now use the public approval route sequence instead of inserting
approval records directly:

```text
command.approval.request
read.pending-approvals
read.approval-detail
command.approval.decide
```

#878 must still republish cpk-server before final ACTIVITY acceptance because
the cpk-server backend adapter surface now includes approval request behavior.

## #876 Cpk-Server Public Workflow Routes

#876 completed the first public route proof for the full operator workflow
without collapsing the durable execution lifecycle. The public command language
now includes two additional route contracts:

```text
command.run.start
  -> /workspaces/{workspace_id}/runs/{run_id}/start
  -> StartRunRequest
  -> ActivityRunTransitionResult
  -> EXECUTION_RUN

command.graph.advance-current
  -> /workspaces/{workspace_id}/runs/{run_id}/advance-current-graph
  -> AdvanceCurrentGraphRequest
  -> CurrentGraphAdvancementResult
  -> EXECUTION_RUN
```

The important lifecycle decision is that request, claim, start, execution, and
advancement remain separate durable steps:

```text
admit
  -> execution request id
claim
  -> opens activity run and returns run id
start
  -> records RUN_STARTED
execute
  -> dispatches activities
advance
  -> advances current graph from accepted run evidence
```

The public cpk-server operations adapter test now proves this route sequence:

```text
workspace.create
  -> product.import
    -> operation-session.start
      -> desired-graph.set
        -> deployment.plan
          -> approval.request
            -> read.pending-approvals
              -> read.approval-detail
                -> approval.decide
                  -> deployment.admit
                    -> run.claim
                      -> run.start
                        -> deployment.execute
                          -> graph.advance-current
                            -> read.current-graph
```

The proof intentionally uses both HTTP-shaped and MCP-shaped route requests
against the same operations application boundary. The activity adapter is a
test-local successful adapter, so #876 proves public workflow composition,
approval preservation, run-id handling, and explicit current-graph advancement;
it does not claim real Docker acceptance. The real seeded-product Docker proof
belongs to #877/#878.

Focused evidence also proves the generated plan still dispatches the expected
semantic activity spine:

```text
start-runtime -> start-node -> wait-healthy
```

Validation evidence:

```text
git diff --check
./control-plane-kit-core/test.sh
  379 tests passed
  compileall passed
  control-plane-kit-core import ok
./control-plane-kit-operations/test.sh
  120 tests passed
  compileall passed
  control-plane-kit-operations import ok
./test.sh
  1219 tests passed in 233.590s
```

Review findings:

- approval is not bypassed; admission still depends on current approved plan
  evidence;
- claim/start/execute remain distinct and execute/advance use the run id opened
  by claim, not the admission request id;
- current graph advancement is explicit, guarded, and does not let observations
  rewrite desired graph truth;
- the new routes reuse existing lifecycle, execution, and advancement services
  rather than creating another workflow model;
- no transaction or UnitOfWork ownership changes were introduced;
- core received only closed command/read contract language, not runtime or
  product-specific behavior.

#877 handoff:

#877 should replace the #876 fake-success adapter with seeded local-Docker
product realization. Use the existing public workflow shape, then prove real
setup, dependency binding, observation, cleanup, and current-graph advancement
for digest-pinned seeded products. #878 must republish cpk-server after the
backend route changes from #876/#877 are complete.

## #892 Product Family And Retained Data Mount Material

#877 exposed a real product-language ambiguity before live seeded acceptance:
`postgres-server` is OCI-backed and graph-visible, but it is not a CPK-managed
HTTP server process. It is a data-service product with retained data, a private
Postgres provider socket, public non-secret environment, and a runtime secret
delivery for `POSTGRES_PASSWORD`.

The structural correction keeps `products/postgres_server` in place and updates
the product language instead of moving files for naming comfort:

```text
ProductFamily
  = server
  | data-service

RetainedDataMount
  = resource_id
  x safe absolute container target_path
```

The core product descriptor now carries both fields:

```json
{
  "product_family": "data-service",
  "runtime_contract": {
    "retained_data_mounts": [
      {
        "resource_id": "postgres-data",
        "target_path": "/var/lib/postgresql/data"
      }
    ]
  }
}
```

The retained mount target is graph data, but only as a container path. Host
paths remain outside descriptors and graph truth. The language rejects relative
paths, path traversal, runtime namespaces such as `/proc` and `/sys`, Docker
socket paths, duplicate targets, and mount references that do not correspond to
declared retained data resources.

Operations now interprets the generic OCI product contract without branching on
`postgres-server` by name:

```text
OCI image
  x sockets
  x public environment
  x SecretEnvironmentDelivery resolved at runtime
  x retained data mounts
    -> Docker network/container/volume materialization
```

Secret resolution is an explicit operations-side runtime seam. Missing resolver
authority fails before Docker mutation; resolved secret values are released only
at the Docker process environment boundary and are not included in events,
observations, failure evidence, descriptors, or graph data.

Retained data volumes are created with the same workspace/plan/graph/runtime
ownership labels as containers, plus `control-plane-kit.data-resource-id`.
Foreign volume collisions fail before image pull or container start. Ordinary
compute realization mounts retained data; explicit data destruction remains a
separate future/legacy interpreter concern and must never be inferred from
compute teardown.

Validation evidence:

```text
./control-plane-kit-core/test.sh
  382 tests passed
  compileall passed
  control-plane-kit-core import ok

./control-plane-kit-operations/test.sh
  122 tests passed
  compileall passed
  control-plane-kit-operations import ok

git diff --check

./test.sh
  1219 tests passed in 208.236s
```

#877 handoff:

Use the new `ProductFamily.DATA_SERVICE` and `RetainedDataMount` language when
running the seeded Postgres product. The local Docker adapter now supports the
runtime material needed for the Postgres descriptor, provided the acceptance
harness supplies an explicit secret resolver for
`secret://control-plane-kit/postgres/password`. Continue to treat remote managed
databases such as RDS as future runtime/interpreter work rather than as this
local OCI data-service proof.

## #877 Seeded Product Live Scenarios

#877 replaced the #876 fake-success route workflow with real local-Docker
activity realization over seeded OCI product descriptors. The live harness
drives the same public route-shaped application boundary used by cpk-server:

```text
workspace create
  -> product import
    -> session start
      -> desired graph set
        -> plan
          -> approval request
            -> pending approval queue / approval detail
              -> approval decision
                -> admit
                  -> claim
                    -> start
                      -> execute bounded Docker activities
                        -> advance current graph
                          -> read current graph
```

The scenario matrix now proves:

```text
Postgres data service + Hello server
Router deployment: Hello blue -> active router
Multiplexer deployment: primary Hello + observer Hello -> multiplexer
Router transition: active router retargets from blue to green
Router teardown: router, Hello nodes, and Docker network are removed
```

The live proof consumes digest-pinned descriptors from
`control-plane-kit-servers` and uses Docker-local networking only. That is
intentional for ACTIVITY. Remote control portals, Cloudflare ingress,
CPK-enabled backdoor mutation, recursive child cpk-server acceptance, and cloud
runtimes remain deferred.

Important structural findings:

1. Seeded HTTP descriptors needed bounded readiness retries. One-shot probes
   made successful Docker startup depend on lucky timing. The server catalogue
   now records `maximum_attempts: 5` for seeded HTTP live/readiness checks
   through `control-plane-kit-servers` PR #19. This is descriptor truth, not a
   local harness sleep.
2. `VerificationPolicy.maximum_attempts` needed operational cadence. The
   stdlib Docker health interpreter now sleeps briefly between attempts, so
   retry policy means bounded startup tolerance rather than immediate repeated
   connection attempts.
3. Docker health failure evidence now records failed check ids, outcomes, and
   bounded per-check evidence. This made live failures inspectable through the
   activity journal without exposing command strings or secrets.
4. `ReconcileRuntime` is now interpreted as idempotent owned-network
   reconciliation. It reuses the same local Docker primitive as
   `StartRuntime`; no second runtime model was introduced.
5. Docker plan and graph labels are provenance, not compatibility identity.
   Multi-plan updates must be allowed to reconcile a runtime network created by
   a prior approved plan. Ownership compatibility still requires stable owner,
   workspace, runtime, node, product, descriptor, and data-resource labels.
6. Reconciled health-checkable nodes now schedule
   `ReconcileNode -> WaitForHealthy`. The router update exposed this gap:
   current graph advancement must depend on post-reconcile provider health, not
   on a demo-side retry after advancement.
7. The live controller attaches to runtime networks only while CPK-owned graph
   containers exist there. It detaches before runtime-network removal so the
   harness probe endpoint does not block legitimate graph teardown. The Docker
   adapter does not disconnect arbitrary foreign endpoints.

The resulting local Docker interpreter can now perform these real product
operations:

```text
StartRuntime / ReconcileRuntime
StartNode / ReconcileNode
WaitForHealthy for HTTP checks
WaitForHealthy for Postgres checks via injected operations-side checker
StopNode / RemoveNodeResource
StopRuntime / RemoveRuntimeResource
```

Validation evidence before PR:

```text
control-plane-kit-servers PR #19
  git diff --check
  ./test.sh
  GitHub docker-tests passed

./control-plane-kit-core/test.sh
  385 tests passed
  compileall passed
  control-plane-kit-core import ok

./control-plane-kit-operations/test.sh
  132 tests passed
  compileall passed
  control-plane-kit-operations import ok

git diff --check
python3 -m py_compile examples/activity_seeded_live.py

./activity-seeded-live-test.sh
  seeded ACTIVITY scenarios passed
  ACTIVITY seeded live proof passed

./test.sh
  1219 tests passed in 206.343s
```

#878 handoff:

#878 must republish cpk-server because #876 and #877 changed backend/runtime
behavior below the cpk-server image. The publish lane should update the
cpk-server Dockerfile source pin to the merged #877 commit, publish a new GHCR
image, record the immutable digest, update `products/cpk_server/product.cpk.json`,
refresh descriptor and catalogue checksums, and run the published-image smoke
with local rebuild disabled.

## #878 Published cpk-server Activity Baseline

#878 republished the cpk-server OCI image from the merged #877
control-plane-kit source commit:

```text
control-plane-kit source commit:
  fc85788e7b39324091d397f8afa4b1b9b56b3cb7

cpk-server image:
  ghcr.io/openj92/control-plane-kit-servers/cpk-server@sha256:6d09435ccb579c318b4e4914435e56e1f758ac9d8241e29aae5755b9662c45b0

cpk-server descriptor sha256:
  003d1673d7c17d12031f14f746c5724375e376fae9678463a2454134b4c6727b

packaged catalogue checksum:
  48d9569f970f985011cc8abd6dd248c8715578f1c8a47a6885ad061d5f0ba87b
```

The publication work landed in `control-plane-kit-servers` PR #20. The
descriptor and packaged catalogue were updated together. During review, the
new core `ProductDescriptorCodec` correctly rejected a descriptor rewritten
with a trailing newline. The final descriptor is canonical compact JSON and the
catalogue digest was recomputed from those exact bytes.

Validation evidence:

```text
control-plane-kit-servers PR #20
  git diff --check
  focused cpk-server product tests
  scripts/cpk_server_published_image_smoke.sh
  ./test.sh
```

The published-image smoke pulled by immutable digest and disabled local rebuild:

```text
CPK_SERVER_BUILD_IMAGE=0
docker pull ghcr.io/openj92/control-plane-kit-servers/cpk-server@sha256:6d09435...
```

Important boundary finding:

The published cpk-server image still contains the explicit
`_UnsupportedExecutionAdapter` seam. That is not a missed import. Real Docker
execution from inside the hosted cpk-server image would require a declared
Docker-host capability, socket/access policy, and runtime/interpreter package
boundary. ACTIVITY proves local Docker realization through operations and the
seeded live harness; it does not yet make the published cpk-server image a
Docker-host controller. That remains a handoff to the runtime/interpreter lane.

## #879 ACTIVITY Closeout

ACTIVITY now establishes the first real extracted operator workflow over
durable operations:

```text
create workspace
  -> import product descriptor
    -> start operation session
      -> set desired graph
        -> plan transition
          -> request approval
            -> manager reviews pending approval / plan detail
              -> manager approves or rejects
                -> admit approved plan
                  -> claim run
                    -> start run
                      -> execute activities
                        -> record observations
                          -> advance current graph only after accepted success
                            -> read final state
```

Capabilities now available:

```text
core
  pure product, graph, plan, command, read, and route contract language

operations
  Postgres-backed workspace/product/session/graph/approval/admission/lifecycle
  services, local Docker product realization, observations, and explicit current
  graph advancement

cpk-server
  FastAPI and MCP wrappers over the same operations command/read boundary

control-plane-kit-servers
  digest-pinned OCI descriptors for cpk-server, hello-server,
  http-active-router, http-multiplexer, and postgres-server
```

Real local Docker product operations proven by #877:

```text
StartRuntime / ReconcileRuntime
StartNode / ReconcileNode
WaitForHealthy for HTTP checks
WaitForHealthy for Postgres checks through the operations-side health checker
StopNode / RemoveNodeResource
StopRuntime / RemoveRuntimeResource
```

Seeded live scenarios exercised:

```text
Postgres data service + Hello server
Router deployment: Hello blue -> active router
Multiplexer deployment: primary Hello + observer Hello -> multiplexer
Router transition: active router retargets from blue to green
Router teardown
```

HTTP/MCP evidence:

#876 proved the public route workflow over both HTTP-shaped and MCP-shaped
boundaries. Both surfaces traverse the same `CpkServerOperationsApplication`
service boundary and do not carry duplicate command vocabulary. #877 then
proved real Docker activity realization through operations with seeded product
descriptors. The published cpk-server image was republished from the same
activity-capable operations source, but hosted Docker execution remains
deferred until cpk-server has a coherent runtime/interpreter capability.

Security and data-engineering review:

- Approval is part of the workflow and admission still rejects missing,
  rejected, stale, or mismatched approval evidence.
- Current graph advancement is explicit and guarded by completed run evidence.
- Observations extend operational evidence and do not rewrite desired graph
  truth.
- Product descriptors remain secret-free. Postgres password material is handled
  through explicit secret delivery in the descriptor and a local-development
  resolver in the live harness.
- Stores remain UnitOfWork-owned; stores do not commit independently.
- The Docker adapter records durable intent before bounded external effects and
  records result/observation/projection afterward.
- Docker ownership compatibility ignores plan/graph provenance labels while
  preserving stable owner/workspace/runtime/node/product/descriptor/data
  compatibility.
- Docker cleanup removes only proven-owned ACTIVITY resources and preserves
  unrelated containers and volumes.

Residual risks and explicit handoffs:

```text
#676 recursive cpk-server acceptance
  deferred until hosted cpk-server has a coherent runtime/interpreter capability

#806 runtime/interpreter extraction
  owns Docker-host access, future Docker SDK/CLI choice, cloud runtimes, and
  runtime capability publication

#882 future control portals / ingress
  owns remote over-the-wire control access into CPK-enabled servers

larger topology stress tests
  should reuse the seeded descriptors and add richer mixed topologies after the
  runtime/interpreter boundary is explicit

frontend work
  can consume the approval queue, plan detail, workflow state, and read models
  after operations closeout
```

ACTIVITY should close as local Docker realization plus public workflow
composition, not as recursive or remote hosted runtime execution.

## #897/#898 Interpreter Runtime Dry Run

#897 and #898 refresh the runtime/interpreter extraction before creating the
`control-plane-kit-interpreters` package. The dry run confirms the intended
authority chain:

```text
cpk-server
  -> configured operations application
    -> ExecutionCoordinator
      -> RuntimeInterpreterDispatcher
        -> DockerRuntimeInterpreter
          -> Python Docker SDK
```

The important boundary decision is that operations owns dispatch because it owns
durable ActivityPlan execution, UnitOfWork, run lifecycle, observations, and
current graph advancement. The interpreters package owns concrete runtime
effects such as Docker SDK calls, probe execution, configuration materialization,
secret materialization, host publication, and cleanup. `cpk-server` remains a
FastAPI/MCP process wrapper that receives configured runtime authority; it does
not become the owner of Docker behavior merely because its image can be run by
Docker.

The dry-run artifact is:

```text
artifacts/extraction/interpreter-runtime-dry-run.json
```

It records the #897/#898 topology, law cards, current file anchors, frozen
inspiration sources, and the Docker SDK coverage assessment. The topology is
coherent without adjustment before #899:

```text
#897 -> #898 -> #899 -> #900 -> #901 -> #902 -> #903 -> #904 -> #905
  -> #906 -> #907 -> #908 -> #909 -> #910 -> #911
```

The ordering matters. #900 introduces the operations-owned runtime dispatcher
before any concrete Docker SDK implementation. #901 stabilizes the current
operations-local Docker adapter seam before replacing CLI mechanics. #908 wires
cpk-server to receive a proven dispatcher instead of inventing server-local
Docker behavior. #910 is only the recursive readiness dry run; full recursive
cpk-server acceptance remains #676.

Frozen `DockerRuntimeInterpreter.up` / `down` remains useful inspiration, but it
is not the production workflow shape. The canonical workflow remains pinned
ActivityPlan execution through the coordinator.

The Docker SDK covers the ordinary Docker substrate well: network, container,
volume, image, port binding, inspection, log, and timeout surfaces. It does not
by itself solve secret-file or configuration-artifact materialization. Those
remain explicit interpreter laws for #904 and #905, where the implementation must
prove bounded materialization without leaking secrets through descriptors,
events, logs, labels, or process arguments.

## #900 Runtime Interpreter Dispatcher

#900 introduced the operations-owned dispatcher seam without importing Docker
SDK behavior into operations or cpk-server:

```text
cpk-server
  -> configured operations application
    -> ExecutionCoordinator
      -> RuntimeInterpreterDispatcher
        -> DockerRuntimeInterpreter
          -> Python Docker SDK
```

`RuntimeInterpreterDispatcher` is itself an `ActivityExecutionAdapter`, so the
existing coordinator continues to own durable execution, worker authority,
UnitOfWork boundaries, event recording, observations, and advancement evidence.
The dispatcher only answers one pure question from pinned graph material:

```text
ActivityRealizationContext x Activity.operation -> RuntimeKind
RuntimeKind x configured interpreters           -> ActivityExecutionOutcome
```

The graph source is operation-specific:

```text
start / reconcile / health work -> desired graph
stop / remove work              -> base graph
```

This preserves graph-drift resistance. Runtime dispatch is derived from the same
approved plan material the coordinator is executing, not from current mutable
workspace truth. Missing runtime targets and unconfigured runtime kinds return
explicit unsupported evidence instead of falling through to Docker or inventing
a default.

The focused proof lives in:

```text
control-plane-kit-operations/tests/test_runtime_interpreter_dispatcher.py
```

It proves desired-graph dispatch for start work, base-graph dispatch for removal
work, runtime-record dispatch, explicit missing-interpreter evidence, and
explicit unsupported evidence for operations that are not runtime interpreter
work. #901 can now stabilize the existing local Docker realization adapter
against this seam before concrete Docker SDK behavior moves into the
`control-plane-kit-interpreters` package.

## #901 Docker Realization Contract

#901 hardened the current operations-local Docker adapter as the compatibility
target for the future SDK-backed interpreter work. No Docker SDK behavior moved
yet; the point was to make the existing seam explicit before #902 changes the
backend.

The preserved spine remains:

```text
cpk-server
  -> configured operations application
    -> ExecutionCoordinator
      -> RuntimeInterpreterDispatcher
        -> DockerRuntimeInterpreter
          -> Python Docker SDK
```

The focused contract now says:

```text
RuntimeInterpreterDispatcher({RuntimeKind.DOCKER: DockerProductRealizationAdapter})
  -> DockerProductRealizationAdapter.execute(ActivityRealizationContext)
    -> DockerRealizationClient structural backend
```

`DockerRealizationClient` is the small backend boundary that #902 can implement
with the Python Docker SDK:

```text
inspect/create network
inspect/create volume
pull image
inspect/run/start/stop/remove container
remove network
```

The #901 proof also pins graph-source behavior at the adapter boundary. A
teardown activity with the same node id in base and desired graphs must remove
using the base graph's product material. If the adapter accidentally used the
desired graph, ownership labels would point at the replacement product and the
old owned container would not be removed.

The strengthened tests live in:

```text
control-plane-kit-operations/tests/test_docker_realization.py
```

They prove the exact client protocol surface, dispatcher-to-adapter composition
without a cpk-server branch, and base-graph teardown material. #902 should
implement a Docker SDK client behind this boundary rather than changing
coordinator, cpk-server, graph, approval, lifecycle, or advancement behavior.

## #902-#906 Docker SDK Interpreter Foundation

#902 through #906 moved the concrete Docker substrate into
`control-plane-kit-interpreters` while preserving the same authority chain:

```text
cpk-server
  -> configured operations application
    -> ExecutionCoordinator
      -> RuntimeInterpreterDispatcher
        -> DockerRuntimeInterpreter
          -> Python Docker SDK
```

#902 introduced the lazy Docker SDK client. The package root and Docker module
can be imported without importing the optional `docker` dependency; the concrete
SDK is imported only when `DockerSdkClient()` is constructed without an injected
client. The client owns inspection, create/pull/run/start/stop/remove calls for
Docker networks, volumes, images, and containers. It still imports no operations
stores, UnitOfWork, cpk-server process modules, or product-server code.

#903 added concrete probe and verification adapters. Core still owns probe
intent and verification value languages; interpreters own bounded TCP, UDP,
HTTP, Redis, and Postgres checks against authorized endpoint material.

#904 and #905 added Docker materialization for configuration artifacts and
secret-file deliveries. Configuration uses immutable, bounded, secret-free core
`ConfigurationArtifact` values. Secrets are resolved only at runtime from
authorized `SecretReference` material. Both paths use helper containers and
`put_archive`, not process argv, and both verify durable evidence by digest.

#906 added explicit host publication and endpoint observation support. The
Docker SDK boundary is now:

```text
DockerSdkPortBinding
  -> Docker SDK ports argument
    -> DockerSdkPublishedPort inspection facts
      -> verify_published_ports(requested, observed)
        -> runtime_endpoint_observations(...)
```

This keeps private endpoints, host-local endpoints, and public endpoints as
distinct runtime observations. TCP and UDP are matched by typed `Transport`;
UDP publication is never inferred from TCP publication on the same numeric port.
Endpoint observations remain evidence for operations to persist and project.
They do not rewrite desired graph truth.

#906 live evidence:

```text
git diff --check
control-plane-kit-interpreters ./test.sh: 38 tests, compileall, import checks
tests/live_docker_publication.py: published TCP 8000 and UDP 5353, 2 host observations
host-publication Docker residue audit: no labeled containers, networks, or volumes
control-plane-kit ./test.sh: core 385, operations 141, root 1224
```

The live publication proof uses Docker inspection as the source of truth. It
refuses to count a host endpoint unless Docker reports the requested transport,
bind address, and fixed host port when one was requested.
