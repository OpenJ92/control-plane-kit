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

## #1022 Local Runtime-Island Gateway Boundary

#1007 exposed a real reachability boundary in the seeded stress lane:

```text
postgres-server starts inside a DockerRuntime network
  -> parent cpk-server tries PostgresQueryCheck(select-one)
    -> parent process cannot rely on postgres:5432 being reachable
      -> semantic readiness fails even though the runtime node exists
```

The direct diagnostic workaround is to attach parent cpk-server to the workload
network. That proves the failure is reachability, but it should not become the
architecture. Parent cpk-server should not join every runtime island merely to
probe private sockets.

The first-pass replacement is a local runtime-island gateway:

```text
cpk-server owns topology/history/approval/read models
  -> DockerRuntime contains gateway + workload nodes
    -> Docker interpreter realizes all nodes
      -> gateway receives graph-derived target map
        -> gateway probes private local sockets
```

The gateway is an ordinary graph/product node. It is not a second control plane.

### First-Pass Laws

- gateway does not own desired graph truth;
- gateway does not own current graph truth;
- gateway does not maintain a local topology graph;
- gateway does not spawn nodes;
- gateway receives graph-derived target material at startup/configuration time;
- gateway accepts only closed semantic probe/control commands;
- gateway may call only declared local targets;
- gateway returns bounded redacted results;
- parent cpk-server records observations and history;
- gateway is not an arbitrary HTTP/TCP proxy;
- database sockets remain private by default.

### Target Map Shape

The target map is derived from explicit graph/provider socket facts, not from
product-specific gateway behavior. A first-pass map can be read as:

```json
{
  "postgres.postgres": {
    "protocol": "postgres",
    "host": "postgres",
    "port": 5432
  },
  "router.internal": {
    "protocol": "http",
    "url": "http://router:8000"
  }
}
```

The identity is `node_id.provider_socket`. The target material may include a
runtime-private host, port, URL, protocol, and source edge/provider evidence. It
must not contain raw secrets, credentials, tokens, inline database passwords,
Docker authority material, or public ingress assumptions.

### Scenario Matrix

The first gateway implementation should prove these scenarios before #1007
resumes:

| Scenario | Runtime Island | Gateway Probe | Expected Result |
| --- | --- | --- | --- |
| Postgres readiness | gateway + postgres-server | `postgres.select-one` against `postgres.postgres` | SELECT 1 passes without public DB exposure |
| HTTP readiness | gateway + hello-server or router | HTTP status/health against `*.internal` | HTTP probe passes through private runtime DNS |
| Unknown target | gateway + any seeded node | probe undeclared target id | request fails closed |
| Unsupported probe | gateway + any seeded node | unsupported probe kind | request fails closed |

### Handoff Topology

The #1021 parent is split into:

```text
#1022 local gateway boundary
  -> #1023 cpk-local-gateway product and closed probe API
  -> #1024 graph-derived gateway target-map language
      -> #1025 runtime-effect target-map materialization
          -> #1026 live gateway-mediated private probes
              -> #1027 close gateway foundation and resume seeded stress
```

After #1027, #1007 should resume by replacing direct parent runtime-network
reachability with gateway-mediated Postgres readiness. #1012 should then treat
Cloudflare as ingress to the gateway control endpoint, not as one ingress per
private workload socket.

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

## #916-#919 Runtime Effect Boundary Settlement

#916 moved the cross-package runtime boundary into pure core language:

```text
RuntimeEffectRequest
  = pinned durable source
  x activity operation
  x runtime kind
  x selected registered product material
```

and:

```text
RuntimeEffectResult
  = effect id
  x EffectResultKind
  x bounded evidence
  x optional failure
  x runtime endpoint observations
```

Operations translates from pinned durable execution material into that core
request without querying stores inside the translator:

```text
ActivityRealizationContext -> RuntimeEffectRequest
```

The translator selects product material from the `RegisteredProduct` values that
the coordinator already loaded in a short transaction. It matches graph node
metadata by `ProductReference` identity and descriptor digest. That preserves:

```text
pinned truth in -> pure request out
```

#917 implemented the concrete Docker interpreter in the separate
`control-plane-kit-interpreters` repository:

```text
RuntimeEffectRequest -> IO RuntimeEffectResult
```

The Docker SDK, Docker ownership checks, image pulls, container/network/volume
mutation, configuration materialization, secret resolution, and endpoint
inspection now live outside operations.

#919 then removed the old operations-owned Docker adapter and CLI client. The
current spine is:

```text
cpk-server
  -> configured operations application
    -> ExecutionCoordinator
      -> RuntimeInterpreterDispatcher
        -> RuntimeEffectRequest
          -> injected runtime interpreter
            -> RuntimeEffectResult
```

Operations still owns durable workflow, UnitOfWork boundaries, journals,
observations, and advancement. It does not own Docker implementation behavior.
The dispatcher converts returned runtime results into the existing operations
activity outcome language:

```text
ActivityJournal x RuntimeEffectResult -> ActivityJournal'
```

The #919 tests prove:

- interpreters receive `RuntimeEffectRequest`, not `ActivityRealizationContext`;
- missing interpreters and unsupported targets fail closed;
- effect-id mismatches become uncertainty rather than accepted success;
- runtime endpoint observations become operations observation records;
- `control-plane-kit-operations` imports no Docker SDK, interpreter package,
  cpk-server process package, server-product package, subprocess runtime client,
  FastAPI, HTTPX, MCP, or Uvicorn.

Validation evidence:

```text
git diff --check
control-plane-kit-operations ./test.sh: 124 tests, compileall, import check
```

## #918 External Docker Runtime Live Harness

#918 routed the seeded ACTIVITY live harness through the external
`control-plane-kit-interpreters` Docker implementation instead of the removed
operations-local Docker adapter.

The live composition is now:

```text
operations emits RuntimeEffectRequest
  -> RuntimeInterpreterDispatcher
    -> DockerRuntimeInterpreter
      -> Python Docker SDK
        -> RuntimeEffectResult
          -> operations records activity result and observations
```

The harness composes the interpreter at the outside edge:

```python
adapter = RuntimeInterpreterDispatcher(
    {RuntimeKind.DOCKER: DockerRuntimeInterpreter(DockerSdkClient())}
)
```

and `activity-seeded-live-test.sh` mounts the sibling interpreter repository
read-only, exposing it only through `PYTHONPATH`. This proves the acceptance
composition without making `control-plane-kit-operations` depend on the
concrete interpreter package.

#918 also recorded two pure material-shape decisions needed by the live
scenario:

- product instantiation now renders private socket endpoints as
  `scheme://node_id:container_port` when a provider runtime port is known;
- socket-derived environment bindings are part of pinned
  `RuntimeProductMaterial`, so routers and multiplexers receive dependency
  URLs from graph edges rather than product-specific Docker branches.

The Docker interpreter now owns the runtime-side behavior for:

- `StartRuntime`, `ReconcileRuntime`, `StopRuntime`, and
  `RemoveRuntimeResource`;
- `StartNode`, `ReconcileNode`, `StopNode`, and `RemoveNodeResource`;
- HTTP `WaitForHealthy` checks against runtime-private container addresses;
- strict current-material ownership checks where exact material is being reused;
- stable resource ownership checks where teardown or reconciliation must handle
  an older graph fingerprint for the same workspace/runtime/node.

Teardown materialization now resolves stop/remove runtime activities from the
base graph. That keeps removal tied to the runtime that actually exists rather
than the desired empty graph used by teardown.

Validation evidence:

```text
control-plane-kit-core ./test.sh: 390 tests, compileall, import check
control-plane-kit-operations ./test.sh: 125 tests, compileall, import check
control-plane-kit-interpreters ./test.sh: 51 tests, compileall
activity-seeded-live-test.sh: ACTIVITY seeded live proof passed
```

Residual handoff for #908:

- cpk-server still needs a process/bootstrap injection seam for the dispatcher;
- the live harness proves the desired composition, but cpk-server has not yet
  been republished with that composition;
- interpreter package dependency metadata still needs normal release/commit
  choreography after the core changes land.

## #908 cpk-server Runtime Dispatcher Injection

#908 moved the external interpreter seam into the hosted cpk-server process
composition without making cpk-server own Docker runtime semantics.

The process-level shape is now explicit:

```text
cpk-server
  -> configured operations application
    -> ExecutionCoordinator
      -> RuntimeInterpreterDispatcher
        -> DockerRuntimeInterpreter
          -> Python Docker SDK
```

The cpk-server bootstrap contract gained a closed runtime interpreter selector:

```text
CPK_RUNTIME_INTERPRETERS = none | docker
```

`none` remains the descriptor default and returns a bounded unsupported
execution result. `docker` lazily imports `control-plane-kit-interpreters[docker]`
and constructs:

```python
RuntimeInterpreterDispatcher(
    {RuntimeKind.DOCKER: DockerRuntimeInterpreter(DockerSdkClient())}
)
```

This keeps Docker SDK imports isolated in `control-plane-kit-interpreters`.
The cpk-server product wrapper composes the dependency, but it does not inspect
containers, mutate Docker resources, or branch on product-specific runtime
behavior.

Important implementation decision:

- `control-plane-kit-interpreters` PR #9 aligned its core dependency pin to the
  same control-plane-kit commit used by cpk-server:
  `34a7701d1533a8cb4eb2d41c144e209b6432a658`.
- cpk-server now depends on interpreter commit
  `c74e4784855eda72881404310fb63370988b674d`.
- cpk-server descriptor bytes are canonical. Adding
  `CPK_RUNTIME_INTERPRETERS` required preserving the exact no-trailing-newline
  product descriptor boundary before updating catalogue checksums.

Validation evidence:

```text
control-plane-kit-interpreters git diff --check
control-plane-kit-interpreters ./test.sh: 51 tests passed
control-plane-kit-servers git diff --check
control-plane-kit-servers ./test.sh:
  21 root/catalogue/repository tests passed
  32 cpk-server tests passed
  8 hello-server tests passed
  7 http-active-router tests passed
  9 http-multiplexer tests passed
  7 postgres-server tests passed
  cpk-server image smoke passed
  Docker residue audit passed
```

Residual handoff for #909:

- cpk-server backend/runtime behavior changed, so #909 must publish a new
  GHCR image from the merged cpk-server branch;
- #909 must update `products/cpk_server/product.cpk.json` image digest,
  source commit, descriptor digest, catalogue entries, and packaged catalogue
  checksum;
- #909 acceptance must pull by immutable GHCR digest, not use a local rebuilt
  tag;
- recursive child cpk-server acceptance remains deferred until after the
  published dispatcher-capable image is proven.


## #909 Hosted cpk-server Docker Interpreter Acceptance

#909 proved the hosted process boundary after the interpreter split. The accepted
composition is now live through the published cpk-server OCI image:

```text
cpk-server
  -> configured operations application
    -> ExecutionCoordinator
      -> RuntimeInterpreterDispatcher
        -> DockerRuntimeInterpreter
          -> Python Docker SDK
```

The hosted acceptance script starts cpk-server from GHCR by immutable digest,
configures `CPK_RUNTIME_INTERPRETERS=docker`, mounts the Docker socket as an
explicit local-runtime authority, and then drives the public workflow through
HTTP and MCP:

```text
create workspace
  -> import hello-server descriptor
    -> start session
      -> set desired graph
        -> MCP plan
          -> HTTP approval request
            -> MCP pending approval queue
              -> MCP approval detail
                -> MCP approval decision
                  -> HTTP admit
                    -> HTTP claim
                      -> HTTP start
                        -> MCP execute
                          -> HTTP advance current graph
                            -> HTTP current graph readback
```

The hosted smoke intentionally does not import operations application internals,
PostgresUnitOfWork, or DockerRuntimeInterpreter directly. It talks to cpk-server
only through its public route surfaces. The only Docker SDK use in the controller
is harness-side network attachment so the controller and hosted cpk-server
container can reach the runtime-private Docker network created by the external
interpreter.

Important implementation decisions:

- cpk-server now composes `CurrentGraphAdvancementCommandService` at the process
  boundary. Advancement remains an operations command service and is still an
  explicit public command after accepted execution evidence.
- `CPK_RUNTIME_INTERPRETERS=docker` is the hosted runtime authority for this
  acceptance path. The product descriptor default remains `none`, so runtime
  execution is never inferred from a descriptor alone.
- Docker Desktop exposes `/var/run/docker.sock` inside the cpk-server container
  as root-owned group `0` in this environment. The smoke grants that local
  runtime authority with `--group-add ${CPK_DOCKER_SOCKET_GROUP:-0}`.
- GHCR server-product packages are currently private. The hosted smoke creates a
  minimal read-only Docker auth config from `gh auth token` when available and
  mounts it only for the hosted cpk-server process. Secret material stays out of
  product descriptors, graph truth, events, observations, and logs.
- The acceptance uses the published cpk-server image by digest rather than a
  local rebuilt tag.

Published artifact evidence:

```text
server PR: OpenJ92/control-plane-kit-servers#22
server merge commit: 9b7aa88edbcfd55d72b4d6ac7e2c82f9422848bf
cpk-server image: ghcr.io/openj92/control-plane-kit-servers/cpk-server@sha256:def866baeeda659d61a821a29a07a8ceb780bcb440ab7fe0c63a8fa8989e7c7a
cpk-server descriptor sha256: 10dafb59f3d98a527e9dc39fe87ab93668774afc8ee5b688bf663bdb1553c159
packaged catalogue checksum: 1c3d0dd880caf0b2a065a80403d326db8dd47358e2418afa712f7af0818c4bfc
```

Validation evidence:

```text
control-plane-kit-servers git diff --check
control-plane-kit-servers sh scripts/cpk_server_published_image_smoke.sh:
  cpk-server published image smoke passed
control-plane-kit-servers sh scripts/cpk_server_hosted_activity_smoke.sh:
  hosted cpk-server Docker activity smoke passed
  Docker residue audit passed
control-plane-kit-servers ./test.sh:
  21 root/catalogue/repository tests passed
  34 cpk-server tests passed
  8 hello-server tests passed
  7 http-active-router tests passed
  9 http-multiplexer tests passed
  7 postgres-server tests passed
  cpk-server image smoke passed
  Docker residue audit passed
GitHub OpenJ92/control-plane-kit-servers#22 docker-tests passed
```

Residual handoff for #910:

- operations still has no concrete Docker dependency; #910 should verify this
  from package imports and architecture checks;
- Docker SDK remains isolated to `control-plane-kit-interpreters`; #910 should
  include this as closeout evidence;
- hosted acceptance has proven the external interpreter path for a one-node
  hello deployment, but recursive cpk-server acceptance, control portals,
  cloud runtimes, and larger topology stress tests remain deferred;
- any future cpk-server backend/runtime change must repeat the publish-by-digest
  sequence before acceptance.


## #910 EXTRACT.INTERPRETERS Closeout

#910 closes the interpreter extraction vertical around a simple algebraic split:

```text
core:
  RuntimeEffectRequest

interpreter:
  RuntimeEffectRequest -> IO RuntimeEffectResult

operations:
  ActivityJournal x RuntimeEffectResult -> ActivityJournal'
```

The runtime composition established by the vertical is:

```text
cpk-server
  -> configured operations application
    -> ExecutionCoordinator
      -> RuntimeInterpreterDispatcher
        -> DockerRuntimeInterpreter
          -> Python Docker SDK
```

What is now true:

- `control-plane-kit-core` owns the pure request/result language and runtime kind
  values. It does not own Docker, subprocess execution, cpk-server process code,
  stores, or adapter effects.
- `control-plane-kit-operations` owns the coordinator, runtime dispatcher
  protocol, activity journal folding, observations, stores, and UnitOfWork. Its
  package-boundary tests reject imports of `control_plane_kit_interpreters`,
  `docker`, `subprocess`, FastAPI, HTTPX, MCP, Uvicorn, and server-product code.
- `control-plane-kit-interpreters` owns concrete effect execution. The Docker
  runtime interpreter consumes `RuntimeEffectRequest` values and returns
  `RuntimeEffectResult` values; Docker SDK is isolated there.
- `cpk-server` is an API/MCP process wrapper that composes dependencies at
  startup. It can select `CPK_RUNTIME_INTERPRETERS=none|docker`, but it does not
  inspect containers, own Docker semantics, or branch on product-specific runtime
  behavior.
- `control-plane-kit-servers` owns product descriptors, Dockerfiles, OCI images,
  and catalogue metadata.

Live evidence now spans both composition styles:

```text
activity-seeded-live-test.sh
  operations harness + external DockerRuntimeInterpreter

scripts/cpk_server_hosted_activity_smoke.sh
  published cpk-server OCI + HTTP/MCP workflow + external DockerRuntimeInterpreter
```

The hosted proof uses:

```text
ghcr.io/openj92/control-plane-kit-servers/cpk-server@sha256:def866baeeda659d61a821a29a07a8ceb780bcb440ab7fe0c63a8fa8989e7c7a
```

with descriptor sha256
`10dafb59f3d98a527e9dc39fe87ab93668774afc8ee5b688bf663bdb1553c159` and packaged
catalogue checksum
`1c3d0dd880caf0b2a065a80403d326db8dd47358e2418afa712f7af0818c4bfc`.

#910 introduced `artifacts/extraction/extract-interpreters-closeout-report.json`
and `tests/test_extract_interpreters_closeout.py` so the closeout assertions are
queryable rather than only prose.

Final #910 validation added `control-plane-kit ./test.sh`: packaging acceptances
plus 1230 tests passed.

Residual deferred work remains intentionally outside this vertical:

- recursive cpk-server acceptance;
- future control portals / ingress;
- cloud runtime interpreters;
- larger topology stress tests;
- frontend work.


## #927 Private OCI Pull Authority: Core Language

#927 begins the #926 private OCI registry pull authority hardening pass. The dry
run found that image identity and pull authority were still conflated by the
hosted smoke harness rather than the language: product descriptors already carry
digest-pinned `OciImageReference` values, while cpk-server hosted acceptance
mounted Docker auth config into the process as bootstrap scaffolding.

The first implementation slice adds only pure core language:

```text
OciImageReference
  = immutable image identity / digest truth

ImagePullAuthority
  = registry/repository scope
  x opaque CredentialReference
```

`ImagePullAuthority` lives in `control_plane_kit_core.runtime_effects` because it
is interpreter-bound runtime material, not product descriptor truth. It reuses
the existing `CredentialReference = SecretReference` vocabulary, so no new
secret-reference language or secret store is introduced.

`RuntimeProductMaterial` now carries an optional `pull_authority` field. The
descriptor form contains only the registry scope and secret reference id; it does
not carry a token, Docker config JSON, password, auth blob, or resolved
credential.

Validation:

```text
./control-plane-kit-core/test.sh:
  393 tests passed
  compileall passed
  import check passed
```

Handoff to #928:

- operations should admit workspace/runtime pull authority as durable operational
  truth;
- product descriptors must remain unchanged and secret-free;
- runtime-effect translation should select only opaque pull-authority references
  from pinned context and registered authority, not resolve credentials;
- missing/revoked/wrong-scope authority should fail closed before blind replay.


## #928 Private OCI Pull Authority: Operations Admission

#928 adds durable operations ownership for image-pull authority without changing
product descriptors or resolving credentials. The new operations truth is:

```text
RegisteredImagePullAuthority
  authority_id
  workspace_id
  ImagePullAuthority
  admitted_by
  admitted_at
  status
```

The Postgres table `cpk_image_pull_authorities` stores only the authority
descriptor and indexed registry/repository scope. It carries a
`credential_reference` string beginning with `secret://`; it never stores a
token, Docker config JSON, password, auth blob, or resolved credential.

The coordinator now loads active pull authorities beside active registered
products and includes them in `ActivityRealizationContext`. Runtime-effect
translation selects the most-specific admitted authority whose scope permits the
product image:

```text
ActivityRealizationContext
  x RegisteredProduct
  x RegisteredImagePullAuthority*
    -> RuntimeProductMaterial(pull_authority=ImagePullAuthority | None)
```

Important boundary decision: operations does not infer that a registry is
private from a host name such as `ghcr.io`. It selects authority if the workspace
has admitted one for the image scope. Interpreter-side #929 is responsible for
failing closed when runtime material requires a pull authority but the
configured resolver cannot satisfy it.

Validation:

```text
./control-plane-kit-operations/test.sh:
  131 tests passed
  compileall passed
  import check passed
```

Handoff to #929:

- `RuntimeProductMaterial.pull_authority` is populated from durable operations
  truth when a matching active authority exists;
- the interpreter should resolve the `credential_reference` only at Docker pull
  time;
- missing resolver, denied credential, or wrong scope should fail closed before
  container creation;
- resolved credentials must not enter runtime results, events, observations,
  logs, or reprs.

## #937 Recursive cpk-server Scenario Matrix

#937 dry-ran the recursive cpk-server acceptance shape against the live extracted
repositories. The accepted proof is intentionally small and opaque:

```text
parent cpk-server
  -> public HTTP/MCP workflow
    -> desired graph:
         child-postgres: postgres-server
         child-cpk:      cpk-server
         child-postgres.postgres -> child-cpk.workplace-store
         child-postgres.postgres -> child-cpk.activity-history-store
         child-postgres.postgres -> child-cpk.observer-state-store
         child-postgres.postgres -> child-cpk.graph-topology-store
      -> RuntimeInterpreterDispatcher
        -> DockerRuntimeInterpreter
          -> child containers
```

The parent may observe the child cpk-server's readiness and liveness endpoints,
but must not inspect child graph truth, operation sessions, approvals, activity
history, or current graph. That preserves the recursive boundary:

```text
parent owns the child as a deployable node
child owns its own control-plane truth
```

The dry run chose one `postgres-server` data-service node instead of four. The
cpk-server descriptor has four Postgres requirement sockets, and the graph
compiler already turns socket connections into socket-derived environment
bindings. Four explicit edges from the single Postgres provider therefore
produce the four required `CPK_*_DATABASE_URL` values without cpk-server-specific
shell wiring.

Current seed coordinates:

```text
cpk-server image:
  ghcr.io/openj92/control-plane-kit-servers/cpk-server@sha256:12e9eb53d1b61d662d10f007dccec91e9858e5a6bc015b96a703add341421899

cpk-server descriptor sha256:
  a5d87c6593a07a7c5aa98228fe1350cfb75ea734ab43aedd6358c1e31013d12f

postgres-server image:
  docker.io/library/postgres@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777

postgres-server descriptor sha256:
  942c32d198c185bb98afdec03d310a69ed11d3c41a14040644212187c842b193

server catalogue checksum:
  3b52f41d84469ac1d2386652cb022a7a47cb6c8cd8a7a904b15e3dae1da210e7
```

The child cpk-server should run with `CPK_RUNTIME_INTERPRETERS=none` for #936.
Recursive acceptance proves the parent can realize and observe a child
control-plane process. It does not require the child to spawn grandchildren.

The important prerequisite is #948. `postgres-server` correctly declares
`POSTGRES_PASSWORD` as a descriptor secret delivery, but the current external
Docker interpreter still rejects all secret-bearing products before container
creation. #948 must implement generic secret delivery resolution at the Docker
interpreter boundary:

```text
product descriptor secret reference
  -> RuntimeEffectRequest remains secret-free
    -> DockerRuntimeInterpreter resolves secret material at IO boundary
      -> Docker SDK receives in-memory environment/file material
```

This must not become a Postgres branch, cpk-server branch, env-file shortcut, or
operations-level secret materialization. Secret values must stay out of graph
descriptors, runtime requests, events, observations, read models, route
responses, logs, and errors.

The machine-readable scenario matrix is recorded in
`artifacts/extraction/recursive-cpk-server-scenario-matrix.json`.

## #952 Explicit Postgres Verification Credentials

#938 dry-run evidence showed that the recursive child cpk-server cannot be
accepted with a generic Postgres semantic readiness check unless the pure
verification language carries secret-free authentication intent. The seeded
`postgres-server` product has a real password requirement, and a future RDS-like
data service will have the same shape: endpoint reachability is not enough to
prove semantic database readiness.

#952 therefore adds a closed authentication contract to `PostgresQueryCheck`:

```text
PostgresQueryCheck
  = check identity
    x provider socket
    x operation
    x optional PostgresPasswordAuthentication
    x verification policy

PostgresPasswordAuthentication
  = database
    x username
    x SecretReference(password)
```

The descriptor records only:

```json
{
  "kind": "password",
  "database": "cpk",
  "username": "cpk",
  "password_reference_id": "secret://verification/postgres/password"
}
```

No raw password enters graph descriptors, product descriptors, runtime effect
requests, events, observations, read models, logs, or route responses. The
interpreter leg will resolve the `SecretReference` at the Docker/Postgres IO
boundary and use it only in memory.

Validation evidence:

```text
./control-plane-kit-core/test.sh
  395 tests passed

git diff --check
  passed

./test.sh
  1232 tests passed
```

Handoff:

- `control-plane-kit-servers` must update `postgres-server` to include the new
  explicit authentication descriptor before recursive acceptance.
- #950 must teach `DockerRuntimeInterpreter` to execute `postgres-query`
  readiness through the concrete Postgres verification adapter using this
  contract.

## #950/#938 Recursive cpk-server Acceptance Evidence

#950 completed the concrete Postgres semantic readiness path in
`control-plane-kit-interpreters`. A live recursive smoke then exposed that the
official Postgres image needs real startup pacing: five immediate `SELECT 1`
attempts were not enough. The fix belongs in the generic Postgres verification
interpreter, not in a recursive script or product-specific Docker branch:

```text
PostgresQueryCheck
  -> PostgresVerificationInterpreter
    -> resolve SecretReference at IO boundary
      -> bounded select-one attempts
        -> one-second pacing between attempts
```

The same live smoke then showed the child `cpk-server` descriptor had a
one-attempt HTTP readiness contract. That was too narrow for a normal Uvicorn
startup window. The product descriptor now advertises ten bounded attempts for
`/health/live` and `/health/ready`, matching the existing HTTP retry behavior
without changing runtime semantics.

#938 adds a recursive local-Docker harness in `control-plane-kit-servers`:

```text
parent cpk-server published OCI image
  -> public HTTP/MCP workflow
    -> import cpk-server and postgres-server descriptors
      -> plan / approval queue / approve / admit / claim / start / execute
        -> RuntimeEffectRequest
          -> RuntimeInterpreterDispatcher
            -> DockerRuntimeInterpreter
              -> child postgres-server
              -> opaque child cpk-server
                -> parent observes /health/live and /health/ready only
```

The child remains opaque. Parent acceptance does not inspect child graph truth,
activity history, operation sessions, or descendant workflow state.

OCI coordinate handling was tightened during #938. Server-products now treats
`coordinates/server-products.json` as the source of truth for upstream package
pins and product image coordinates. The cpk-server smoke scripts derive their
default image from `products/cpk_server/product.cpk.json`, so digest updates do
not scatter across tests and shell fixtures.

Published cpk-server evidence for #938:

```text
image:
  ghcr.io/openj92/control-plane-kit-servers/cpk-server@sha256:a92139b66b5fb0e631bb4fe1a401e3c9968ac99227cf0c9dd5b85e52f506b0f4

interpreter commit:
  4da9711281ba40286f5331c9bc842d588d1f4090

cpk-server descriptor sha256:
  updated through coordinates/server-products.json and scripts/apply_coordinates.py

catalogue checksum:
  98b016107885f44cae2737d7b38d15b56063ec4707c7a5f4b04e31de3a614cae
```

Validation evidence:

```text
control-plane-kit-interpreters ./test.sh
  67 tests passed

control-plane-kit-servers ./test.sh
  passed

scripts/cpk_server_recursive_activity_smoke.sh
  recursive cpk-server Docker activity smoke passed
  control-plane-kit-servers Docker residue audit passed
```

Handoff:

- #939 should harden observation and cleanup assertions around the same
  recursive harness, including parent-recorded runtime result evidence and
  proof that only owned recursive resources are cleaned.
- #940 should close recursive acceptance and hand off to #941 seeded topology
  stress.
- Future control portal work remains out of scope; the parent observes the child
  only through public health endpoints.

## #939 Recursive Observation And Cleanup Evidence

#939 kept the #938 recursive topology and tightened what the parent must prove
through public CPK read surfaces:

```text
parent cpk-server
  -> read.activity
    -> session
      -> plan
        -> run
          -> events
            -> child-postgres created + postgres readiness evidence
            -> child-cpk created + HTTP live/ready readiness evidence
```

The important shape correction was that `read.activity` exposes runs under
`sessions[].plans[].runs[]`, not directly under `sessions[].runs[]`. The
recursive harness now follows the canonical read model instead of inventing a
flat testing shape.

The parent observation assertions verify:

- `child-postgres` was created from the pinned official Postgres OCI digest;
- `child-postgres` recorded the semantic `select-one` Postgres readiness check;
- `child-cpk` was created from the pinned published cpk-server GHCR digest;
- `child-cpk` recorded both `/health/live` and `/health/ready` HTTP checks;
- recorded container names remain under the recursive workspace ownership
  prefix.

The recursive smoke script also has explicit label-scoped cleanup guards:

```text
docker ps -aq --filter "label=$WORKSPACE_LABEL"
docker volume ls -q --filter "label=$WORKSPACE_LABEL"
docker network ls -q --filter "label=$WORKSPACE_LABEL"
```

It still forbids broad cleanup such as `docker system prune` or
`docker volume prune`. This preserves Pottery Factory and unrelated Docker
resources.

Validation evidence:

```text
PYTHONPATH=src python3 scripts/apply_coordinates.py --check
git diff --check
python3 -m unittest \
  products.cpk_server.tests.test_image_bootstrap.CpkServerImageBootstrapTests.\
test_recursive_activity_controller_uses_parent_routes_and_opaque_child -v
./test.sh
scripts/cpk_server_recursive_activity_smoke.sh
  recursive cpk-server Docker activity smoke passed
  control-plane-kit-servers Docker residue audit passed
```

Published cpk-server evidence remains:

```text
ghcr.io/openj92/control-plane-kit-servers/cpk-server@sha256:a92139b66b5fb0e631bb4fe1a401e3c9968ac99227cf0c9dd5b85e52f506b0f4
catalogue checksum: 98b016107885f44cae2737d7b38d15b56063ec4707c7a5f4b04e31de3a614cae
```

Handoff:

- #940 can close recursive cpk-server acceptance using #937 through #939.
- #940 should explicitly report that the parent can spawn and observe an opaque
  child cpk-server, but does not own or inspect the child's graph truth,
  activity history, operation sessions, or descendant workflow state.
- #941 seeded topology stress remains the next topological issue after #940.

## #940 Recursive cpk-server Acceptance Closeout

Recursive cpk-server acceptance is closed at the local-Docker runtime boundary.
The accepted behavior is:

```text
parent cpk-server
  -> public HTTP/MCP workflow
    -> operations application services
      -> RuntimeInterpreterDispatcher
        -> DockerRuntimeInterpreter
          -> published child cpk-server OCI image
          -> Postgres data-service descriptor
```

The parent can spawn and observe an opaque child cpk-server. The parent does
not own or inspect the child's graph truth, activity history, operation
sessions, or descendant workflow state. The child is treated as an ordinary
product node whose public health endpoints are the acceptance boundary.

Immutable coordinates at closeout:

```text
cpk-server image:
  ghcr.io/openj92/control-plane-kit-servers/cpk-server@sha256:a92139b66b5fb0e631bb4fe1a401e3c9968ac99227cf0c9dd5b85e52f506b0f4

cpk-server descriptor_sha256:
  0ebaa90f824f5a10e7ae7a1c91d83d5aaa8e5c8fc83035b062a23266c3327970

server-products catalogue/products.json sha256:
  0fa521b5800f909160ed959cd25700b38cc39e8345dd699400946d3338ed1e96
```

Review findings:

- Architecture: cpk-server remains an ordinary product descriptor. Core has no
  cpk-server specialization.
- Data engineering: the child cpk-server owns its own Postgres-backed truth.
  Parent acceptance observes only the child's public health surface.
- Transactionality: runtime effects occur through the external Docker
  interpreter after operations emits durable intent; no Postgres transaction is
  held across Docker, network, or health effects.
- Security: product descriptors remain secret-free. Postgres passwords and
  private OCI credentials are resolved only at interpreter/bootstrap IO
  boundaries and are not written into events, observations, read models, logs,
  or route responses.
- Docker ownership: recursive cleanup is scoped to the
  `org.openj92.cpk.workspace=recursive-cpk-server` label and forbids broad prune
  commands.
- Retained data: Postgres remains a retained data-service descriptor. Cleanup
  distinguishes owned ephemeral compute from retained data resources.
- Test integrity: acceptance uses the published GHCR image digest, public
  HTTP/MCP routes, and the real `read.activity` shape. It does not use local-tag
  acceptance, direct operations imports, skips, or weakened assertions.

Validation evidence from #939 remains the closeout proof:

```text
PYTHONPATH=src python3 scripts/apply_coordinates.py --check
git diff --check
python3 -m unittest \
  products.cpk_server.tests.test_image_bootstrap.CpkServerImageBootstrapTests.\
test_recursive_activity_controller_uses_parent_routes_and_opaque_child -v
./test.sh
scripts/cpk_server_recursive_activity_smoke.sh
  recursive cpk-server Docker activity smoke passed
  control-plane-kit-servers Docker residue audit passed
```

Residual limitations:

- This is local-Docker acceptance. Remote Docker, cloud runtimes, Kubernetes,
  Cloudflare/control portals, and public over-the-wire control ingress remain
  future work.
- The child cpk-server is intentionally opaque. Recursive grandchild spawning is
  not part of this acceptance.
- Seeded topology stress with hello/router/multiplexer/data-service products
  continues in #941.

## #942 Seeded Topology Stress Dry Run

#942 dry-ran the post-recursive seeded topology stress lane and recorded the
scenario matrix in:

```text
artifacts/extraction/seeded-stress-942-scenario-matrix.json
```

The existing issue topology is mostly correct:

```text
#942 -> #943
#943 -> #944
#943 -> #945
#944 + #945 -> #946 -> #947
```

The dry run found one necessary refinement. Multiplexer observer delivery is
proven today in the product unit tests with an in-process recording server, but
the hosted seeded stress lane needs package-owned live evidence. A `hello-server`
observer can receive the copied request, but it currently has no public request
receipt endpoint or bounded request evidence surface. That makes a live
observer-delivery assertion impossible without weakening the test.

The corrected topology therefore inserts a focused product visibility child
between #943 and #945:

```text
#942 -> #943
#943 -> #944
#943 -> observer-visibility child -> #945
#944 + #945 -> #946 -> #947
```

Blue/green router stress does not require a new core concept. Core already
allows `ProductInstanceConfiguration` to change public environment values while
preserving the product contract key set. #944 should configure `hello-blue` and
`hello-green` with distinct `HELLO_MESSAGE` values, then prove the router
response changes after the graph transition.

Current seed coordinates:

```text
catalogue/products.json sha256:
  0fa521b5800f909160ed959cd25700b38cc39e8345dd699400946d3338ed1e96

cpk-server:
  descriptor 0ebaa90f824f5a10e7ae7a1c91d83d5aaa8e5c8fc83035b062a23266c3327970
  image sha256:a92139b66b5fb0e631bb4fe1a401e3c9968ac99227cf0c9dd5b85e52f506b0f4

hello-server:
  descriptor 7c878cddfa597002f4536c1ac7aea0728df3bbe0e594f6dbc56b646968dab0cc
  image sha256:0b5d62c2706bdfc5b53b67c7e0a72e36b8af7d13f8b2abf26eaa6e6eb7dda5f0

http-active-router:
  descriptor c965218c439ea650421220d9977f641330564b86f6397bef05e4a87edbd43c6b
  image sha256:9edd29c8b62f6413c7acb4009bfa655c065a31a0eac8728ec9d4350122e0a60d

http-multiplexer:
  descriptor 0b74269a7b8c9b775d431a04382eaa268339330c55d4995a6a52ee6de79abc9d
  image sha256:2b6466d87c7642691c4ce2ee52022450d7b7cf1055f1f25a1449adbb5c8131ec

postgres-server:
  descriptor 96307788eb5a6603f3617e1d4b5fd02420997175b969e72304f8e5609acc5f40
  image sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777
```

Handoff:

- #943 should refactor/extend the hosted public workflow controller so the
  richer graph scenarios do not duplicate workspace/product/approval/run code.
- #944 should prove router target binding and blue-to-green transition through
  distinct per-instance Hello messages.
- The new observer-visibility child should add the smallest package-owned
  evidence surface needed for live observer-delivery proof and republish the
  affected product image if its runtime behavior changes.
- #945 should then prove multiplexer primary/observer binding and Postgres
  data-service retained/secret behavior.

## #943 Hosted Workflow Helpers

#943 moved the hosted cpk-server ACTIVITY smoke script from a single inline
workflow into a reusable public-boundary controller in the server-products
repository:

```text
HostedWorkflow
  -> create workspace
  -> import products
  -> start session
  -> set desired graph
  -> plan
  -> request/list/detail/decide approval
  -> admit
  -> claim
  -> start run
  -> execute to completion
  -> advance current graph
  -> read current graph
```

The helper remains intentionally outside cpk-server internals. It talks over
HTTP and MCP only, carries `workspace_id` and `worker_id` through the execution
polling loop, and still relies on the same hosted Docker acceptance script for
network attachment during local-Docker smoke tests.

Validation:

```text
control-plane-kit-servers:
  python3 -m unittest products.cpk_server.tests.test_image_bootstrap.CpkServerImageBootstrapTests.test_hosted_activity_controller_drives_public_workflow_over_http_and_mcp -v
  python3 -m compileall scripts/cpk_server_hosted_activity.py
  PYTHONPATH=src python3 scripts/apply_coordinates.py --check
  git diff --check
  ./test.sh
  scripts/cpk_server_hosted_activity_smoke.sh
```

Handoff:

- #944 can now drive blue/green router transitions without duplicating the
  workspace/product/approval/run lifecycle.
- #955 should add the smallest package-owned observer receipt evidence before
  #945 attempts the live multiplexer observer proof.

## #944 Router Transition Stress

#944 proved the first seeded topology transition with graph-driven HTTP
dependency binding:

```text
hello-blue(HELLO_MESSAGE="Hello from blue")
  -> router(active -> blue)

transition:

hello-green(HELLO_MESSAGE="Hello from green")
  -> router(active -> green)
```

The initial live failure was useful. The hosted scenario reached the router, but
the router returned the Hello descriptor default:

```text
http://router:8000/ -> "Hello, world!\n"
```

That showed the socket edge was working while the selected product-instance
environment was not reaching the realized Hello container. The structural fix
landed across the language/interpreter boundary:

```text
ProductInstanceConfiguration.public_environment
  -> RuntimeProductMaterial.public_environment
    -> RuntimeEffectRequest descriptor
      -> DockerRuntimeInterpreter container environment
```

The important decision is that Docker now uses the selected runtime material,
not the product descriptor defaults, when it starts a node. The descriptor still
defines the contract; the instance supplies the chosen public values. This keeps
the blue/green assertion honest without manual shell environment injection or
product-specific branches in core, operations, or the Docker interpreter.

Validation evidence:

```text
control-plane-kit:
  git diff --check
  ./control-plane-kit-core/test.sh
    395 tests, compileall, import check
  ./control-plane-kit-operations/test.sh
    132 tests, compileall, import check
  ./test.sh
    1232 tests

control-plane-kit-interpreters:
  git diff --check
  ./test.sh
    68 tests

control-plane-kit-servers:
  git diff --check
  python3 -m unittest \
    products.cpk_server.tests.test_image_bootstrap.CpkServerImageBootstrapTests.\
test_hosted_activity_smoke_uses_published_image_and_docker_runtime_authority \
    products.cpk_server.tests.test_image_bootstrap.CpkServerImageBootstrapTests.\
test_hosted_activity_controller_drives_public_workflow_over_http_and_mcp -v
  python3 -m compileall scripts/cpk_server_hosted_activity.py
  CPK_HOSTED_ACTIVITY_SCENARIO=router-transition \
    scripts/cpk_server_hosted_activity_smoke.sh
    hosted cpk-server Docker activity smoke passed: router-transition
    control-plane-kit-servers Docker residue audit passed
  ./test.sh
```

Published cpk-server coordinate after #944:

```text
source commit:
  794fb88033115080a5aad829a0e6e8a47dd350c2

image:
  ghcr.io/openj92/control-plane-kit-servers/cpk-server@sha256:112cad7c5b52e456cc6ea38980d04900de46f25ccdbfe40918e481d83a31ef19

catalogue checksum:
  f8aa411067e8937971ebe4763d67489d8b177c7e2908c101a4c2af0bbdd96001
```

Review findings:

- Router target binding remains derived from `SocketConnection` data.
- Hello message binding now comes from product instance public environment.
- The smoke script does not inject `ACTIVE_TARGET_URL` or `HELLO_MESSAGE`.
- The hosted execute MCP call uses a longer client timeout because it may pull
  and reconcile Docker resources; this is a boundary timeout, not a semantic
  retry or assertion weakening.
- A transient local image smoke once failed to read a Docker-published host port
  during the full suite. The isolated rerun and final full suite passed, and the
  final acceptance evidence uses the published router-transition coordinate.

Handoff:

- #955 should add package-owned observer receipt evidence before multiplexer
  observer delivery is asserted live.
- #945 should then prove multiplexer primary/observer binding and Postgres
  data-service behavior using the seeded products and the same hosted public
  workflow controller.

## #961 Runtime Authority Dry Run

#961 dry-ran the gap between opaque recursive cpk-server acceptance and an
execution-capable child cpk-server. It intentionally changed no production
behavior. The machine-readable map is:

```text
artifacts/extraction/runtime-auth-961-dry-run.json
```

Current parent bootstrap:

```text
outer smoke harness
  -> docker run parent cpk-server
       -v /var/run/docker.sock:/var/run/docker.sock
       CPK_RUNTIME_INTERPRETERS=docker
       optional Docker config mount for GHCR pull authority
       local-development product secret resolver
       four Postgres store URLs
```

Code anchors:

```text
control-plane-kit-servers/scripts/cpk_server_recursive_activity_smoke.sh:108
control-plane-kit-servers/scripts/cpk_server_recursive_activity_smoke.sh:114
control-plane-kit-servers/scripts/cpk_server_recursive_activity_smoke.sh:120
control-plane-kit-servers/scripts/cpk_server_recursive_activity_smoke.sh:121
control-plane-kit-servers/scripts/cpk_server_recursive_activity_smoke.sh:122
```

Current cpk-server bootstrap parses `CPK_RUNTIME_INTERPRETERS` as a closed
process-bootstrap string:

```text
control-plane-kit-servers/products/cpk_server/src/control_plane_kit_servers_cpk_server/server.py:79
control-plane-kit-servers/products/cpk_server/src/control_plane_kit_servers_cpk_server/server.py:108
```

When set to `docker`, cpk-server is currently the composition root that lazy
imports the Docker interpreter and gives operations a dispatcher:

```text
control-plane-kit-servers/products/cpk_server/src/control_plane_kit_servers_cpk_server/server.py:343
control-plane-kit-servers/products/cpk_server/src/control_plane_kit_servers_cpk_server/server.py:353
control-plane-kit-servers/products/cpk_server/src/control_plane_kit_servers_cpk_server/server.py:366
```

Operations dispatch is already correctly abstract:

```text
ActivityRealizationContext
  -> RuntimeEffectRequest(runtime_kind=...)
    -> RuntimeInterpreterDispatcher
      -> interpreter.execute(request)
```

Anchors:

```text
control-plane-kit-operations/src/control_plane_kit_operations/coordinator.py:269
control-plane-kit-operations/src/control_plane_kit_operations/coordinator.py:295
control-plane-kit-operations/src/control_plane_kit_operations/coordinator.py:307
```

Runtime-effect translation currently carries product material and OCI pull
authority, but no runtime-control authority:

```text
control-plane-kit-operations/src/control_plane_kit_operations/runtime_effects.py:42
control-plane-kit-operations/src/control_plane_kit_operations/runtime_effects.py:53
control-plane-kit-operations/src/control_plane_kit_operations/runtime_effects.py:117
control-plane-kit-operations/src/control_plane_kit_operations/runtime_effects.py:138
```

Core already has the precedent for secret-free authority references with
`ImagePullAuthority`:

```text
control-plane-kit-core/src/control_plane_kit_core/runtime_effects.py:54
control-plane-kit-core/src/control_plane_kit_core/runtime_effects.py:93
```

Operations already has the durable admission precedent with
`RegisteredImagePullAuthority`:

```text
control-plane-kit-operations/src/control_plane_kit_operations/products.py:219
```

The child cpk-server remains intentionally opaque today. The recursive
controller requires direct child health/readiness only and asserts that the
child reports no runtime interpreters:

```text
control-plane-kit-servers/scripts/cpk_server_recursive_activity.py:387
control-plane-kit-servers/scripts/cpk_server_recursive_activity.py:392
control-plane-kit-servers/products/cpk_server/tests/test_image_bootstrap.py:514
control-plane-kit-servers/products/cpk_server/tests/test_image_bootstrap.py:519
```

The cpk-server product descriptor confirms why: the descriptor default is
`CPK_RUNTIME_INTERPRETERS=none` and there is no runtime-authority requirement:

```text
control-plane-kit-servers/products/cpk_server/product.cpk.json:95
control-plane-kit-servers/products/cpk_server/product.cpk.json:107
```

The corrected conceptual split is:

```text
Interpreter Availability
  software installed/enabled in a cpk-server process

RegisteredRuntimeAuthority
  workspace/operator-admitted concrete runtime target and credential references
```

Why the parent can execute Docker effects today:

- the harness mounts Docker socket authority into the parent;
- the harness sets `CPK_RUNTIME_INTERPRETERS=docker`;
- the cpk-server image includes the Docker interpreter package;
- cpk-server composes `RuntimeInterpreterDispatcher({DOCKER:
  DockerRuntimeInterpreter(...)})`.

Why the child cannot execute Docker effects today:

- its descriptor defaults to `CPK_RUNTIME_INTERPRETERS=none`;
- it receives no graph-visible runtime authority;
- operations has no `RegisteredRuntimeAuthority` store or command;
- core runtime requests have no runtime authority reference/material;
- Docker SDK client construction currently uses `docker.from_env()` at the
  interpreter boundary.

The target path for #962 through #969 remains coherent:

```text
#962
  pure runtime authority reference language

#963
  operations RegisteredRuntimeAuthority admission/store/read model

#964
  operations-owned dispatcher bootstrap with lazy interpreter imports

#965
  Docker local socket and remote TLS authority interpretation

#966
  cpk-server interpreter-availability product variants

#967
  cpk-server HTTP/MCP runtime authority registration

#968
  execution-capable child cpk-server and bounded recursive depth probe

#969
  closeout and handoff to cloud/runtime stress
```

#962 should explicitly decide whether the runtime authority reference belongs
on `RuntimeContext`/`DockerRuntime`, on `RuntimeEffectRequest`, or both. The dry
run points toward:

```text
graph runtime
  -> pure authority_ref
    -> operations lookup RegisteredRuntimeAuthority
      -> RuntimeEffectRequest carries secret-free reference/material
        -> interpreter resolves concrete authority only at IO boundary
```

Non-goals remain sharp:

- no test-only `spawn child` route;
- no raw Docker socket path, Docker config JSON, TLS key, token, cloud
  credential, or password in descriptors, events, observations, read models,
  logs, or route responses;
- no operations service/store/coordinator imports of Docker SDK, boto3, Google
  SDKs, Kubernetes clients, or concrete runtime code;
- no parent inspection of child graph truth, activity history, operation
  sessions, or descendant workflow state.

## Runtime Authority: Reference Value

#971 introduced the first pure runtime-authority value:

```python
RuntimeAuthorityReference("mac-mini-docker")
```

The reference is intentionally only a stable, secret-free name for an admitted
runtime authority. It is not durable registration truth, not Docker endpoint
material, not image-pull authority, and not a secret reference. The descriptor
shape is:

```python
{"reference_id": "mac-mini-docker"}
```

Malformed values fail closed when they look like endpoint URLs, host paths,
Docker config JSON, token/password/secret material, or TLS key material. This
preserves the compatibility path where an omitted runtime authority still means
the existing ambient/local interpreter configuration, while future execution-
capable child and remote runtime scenarios can opt into explicit
`authority_ref` values.

Validation evidence:

```text
git diff --check
./control-plane-kit-core/test.sh
  397 tests, compileall, import check
```

## Runtime Authority: Graph Reference

#972 propagated the pure authority reference into the runtime graph language:

```python
DockerRuntime(
    runtime_id="remote-mac-mini",
    authority_ref=RuntimeAuthorityReference("mac-mini-docker"),
    children=(api,),
)
```

The compiled graph now preserves the reference on `RuntimeRecord`, and the
durable graph descriptor carries:

```python
"authority_ref": {"reference_id": "mac-mini-docker"}
```

An omitted authority remains `None`, preserving the existing ambient/local
Docker acceptance path without pretending local Docker is the semantic default.
Changing the reference is now an explicit graph diff field:

```text
StructuralField.RUNTIME_AUTHORITY
```

Registration is still not validated by core. That belongs to operations in
#963. Core only proves the reference is well-formed, JSON-shaped, and
secret-free.

Validation evidence:

```text
cd control-plane-kit-core && \
  PYTHONPATH=src python3 -m unittest \
    tests.test_runtime_effects \
    tests.test_graph_codec \
    tests.test_graph_diff \
    tests.test_milestone_closeout
  35 tests
git diff --check
./control-plane-kit-core/test.sh
  400 tests, compileall, import check
```

## Runtime Authority: Effect Request Material

#973 decided the first request-level material boundary conservatively:

```text
RuntimeEffectRequest.authority_ref: RuntimeAuthorityReference | None
```

The request carries the graph-selected authority name, not concrete Docker
daemon material. This keeps the request pure and secret-free while still
pinning which admitted runtime authority operations must use once #963 adds
durable `RegisteredRuntimeAuthority` truth.

The translation now reads:

```text
graph.runtimes[runtime_id].authority_ref
  -> RuntimeEffectRequest.authority_ref
    -> interpreter boundary
```

When `authority_ref` is omitted, the request descriptor carries `None`, which
preserves the existing ambient/local interpreter compatibility path. That is
not a claim that local Docker is the semantic default. Execution-capable child,
remote Docker, and cloud scenarios should use explicit authority references
after registration exists.

Concrete authority material remains deferred:

```text
#963 RegisteredRuntimeAuthority
  -> resolve workspace authority truth
    -> future request authority material
      -> #965 Docker local/remote authority interpretation
```

Validation evidence:

```text
cd control-plane-kit-core && \
  PYTHONPATH=src python3 -m unittest tests.test_runtime_effects
  11 tests
cd control-plane-kit-operations && \
  PYTHONPATH=src:../control-plane-kit-core/src \
  python3 -m unittest tests.test_runtime_effect_translation
  5 tests
git diff --check
./control-plane-kit-core/test.sh
  401 tests, compileall, import check
./control-plane-kit-operations/test.sh
  133 tests, compileall, import check
./test.sh
  root suite: 1232 tests
```

The first full-suite attempt failed during Docker image export with a Docker
Desktop storage error. The recovery inspected Docker resources, confirmed the
running containers were Pottery Factory containers, found no CPK-named volumes,
and pruned only Docker build cache before rerunning the suite from the
beginning. Pottery Factory containers were left running.

## Runtime Authority: Durable Admission

#963 introduced operations-owned runtime authority truth:

```text
RegisteredRuntimeAuthority
  workspace_id
  authority_ref: RuntimeAuthorityReference
  runtime_kind
  authority_kind
  authority storage data
  credential_references
  admitted_by / admitted_at
  status
```

The first closed authority variants are intentionally Docker-shaped because the
current interpreter lane is Docker-only:

```text
LocalDockerSocketAuthority
RemoteDockerTlsAuthority
```

This does not move Docker execution into operations. It admits operator
runtime-authority truth so later interpreter work can resolve it at the IO
boundary:

```text
workspace registered runtime authority
  -> runtime effect request authority_ref
    -> interpreter authority resolver
      -> Docker SDK client configuration
```

The durable store may retain a remote Docker TCP endpoint because the
interpreter cannot use a remote daemon without it. Public descriptors and read
models do not publish that endpoint:

```python
RemoteDockerTlsAuthority(...).descriptor()["endpoint"] == "<redacted>"
```

Only `SecretReference` identities are accepted for TLS credential material.
Raw TLS keys, tokens, Docker config JSON, local socket paths, and cloud
credentials do not enter runtime authority descriptors, read models, events, or
runtime-effect requests.

The Postgres table is `cpk_runtime_authorities`. It preserves workspace scope,
closed status values, closed runtime/authority kinds, active-reference
uniqueness, and caller-owned transaction behavior through the existing
`PostgresUnitOfWork` bundle.

Validation evidence:

```text
./control-plane-kit-operations/test.sh
  140 tests, compileall, import check
./test.sh
  1232 tests
git diff --check
```

Handoff:

```text
#964
  operations bootstrap can compose dispatcher availability

#965
  Docker interpreter resolves RegisteredRuntimeAuthority into concrete SDK
  client material at IO time

#967
  cpk-server exposes registration/read workflow over HTTP and MCP
```

## Runtime Authority: Dispatcher Bootstrap

#974 introduced the operations-owned runtime dispatcher bootstrap value:

```text
RuntimeDispatcherBootstrapConfiguration
  runtime_kinds: tuple[RuntimeKind, ...]
```

This is process capability configuration, not workspace authority. It answers:

```text
which runtime interpreter families may this operations process compose?
```

It does not answer:

```text
which Docker daemon, cloud account, TLS credential, token, or socket may this
workspace use?
```

That second question remains durable `RegisteredRuntimeAuthority` truth from
#963 and concrete IO interpretation work for #965.

The process value stays deliberately small:

```text
none
docker
docker,kubernetes
```

`none` is explicit disabled dispatch and cannot be combined with runtime kinds.
Unknown runtime names fail closed. Duplicate runtime names converge to the
canonical closed `RuntimeKind` tuple, ordered by generated descriptor identity.

The bootstrap API imports only operations/core language. It does not import
`control_plane_kit_interpreters`, Docker SDK, Kubernetes SDK, boto3, FastAPI, MCP,
HTTPX, Uvicorn, or server-product code. #975 may use this value at the
cpk-server/bootstrap composition edge to load optional providers lazily, but
operations itself remains free of concrete SDK dependencies.

Validation evidence:

```text
./control-plane-kit-operations/test.sh
  145 tests, compileall, import check
./test.sh
  1232 tests
git diff --check
```

Docker storage note:

```text
The first full-suite attempt exhausted Docker Postgres storage. Inspection found
only Pottery Factory containers running, and hundreds of unused 64-hex anonymous
Docker volumes with zero links. Cleanup removed only those anonymous volumes and
unused control-plane-kit/nginx images. Named Pottery Factory, Starcraft, and
BuildKit volumes were preserved. Pottery Factory containers and the running
Pottery Postgres image were preserved.
```

Handoff:

```text
#975
  consume RuntimeDispatcherBootstrapConfiguration at the cpk-server/process
  bootstrap edge and implement lazy provider loading / missing-extra behavior

#976
  harden package-boundary tests so Docker SDK and concrete interpreter imports
  stay out of operations source
```

## Runtime Authority: cpk-server Dispatcher Bootstrap Behavior

Issue #975 moved cpk-server process bootstrap onto the generic operations
runtime dispatcher language introduced by #974. The key distinction is now
explicit:

```text
CPK_RUNTIME_INTERPRETERS
  -> RuntimeDispatcherBootstrapConfiguration
    -> RuntimeKind family availability for this cpk-server process
```

This value is not a workspace runtime authority. It answers only which concrete
interpreter families the process may compose. Endpoint, socket, TLS, token,
cloud account, and credential truth remain workspace-scoped
`RegisteredRuntimeAuthority` facts and are interpreted later at the IO boundary.

The cpk-server wrapper now parses `CPK_RUNTIME_INTERPRETERS` through
`RuntimeDispatcherBootstrapConfiguration`. It still reports the configured set
through readiness as a compatibility string such as `none` or `docker`, but the
internal value is closed `RuntimeKind` data. Unknown process values fail closed
at the operations bootstrap parser. Known runtime kinds without a cpk-server
provider fail at adapter construction with an explicit missing-provider error.

Docker remains lazy. The cpk-server source imports
`control_plane_kit_interpreters.docker` only inside the Docker provider function,
so a `none` process does not construct Docker clients and future runtime kinds
can be added without making Docker the semantic shape of runtime authority.

Cross-repo coordinate note: #975 required aligning the interpreters package core
pin with the #974 control-plane-kit merge commit. The server-products coordinate
manifest now points at:

```text
control-plane-kit:              1c78f9584a0334446f468f98ba2a0b9505bb727f
control-plane-kit-interpreters: 8e545b7eb3ac33d857c82cfac5af8448cf40d29f
```

Validation evidence:

```text
control-plane-kit-interpreters ./test.sh: 68 tests passed
control-plane-kit-servers ./test.sh: package suites, cpk-server image build/smoke,
  and Docker residue audits passed
```

Handoff: #976 should add broader guardrails around this boundary. In particular,
it should keep process capability, workspace runtime authority, and interpreter
IO authority visibly separate; prevent SDK imports from creeping into operations;
and preserve `none`, Docker, unknown-kind, and known-kind-without-provider
semantics.

## #976 Dispatcher Import Guardrails

#976 pins the runtime-authority split as executable package policy rather than
tribal memory.

The distinction is:

```text
process capability
  CPK_RUNTIME_INTERPRETERS
    -> RuntimeDispatcherBootstrapConfiguration
      -> RuntimeKind families available to the cpk-server process

workspace authority
  RegisteredRuntimeAuthority
    -> durable workspace-scoped authority record
      -> secret-free descriptors and read models

interpreter IO
  RuntimeEffectRequest + RegisteredRuntimeAuthority
    -> concrete SDK/client boundary
      -> RuntimeEffectResult
```

The new guardrails prove that ordinary `control-plane-kit-operations` source has
no imports of concrete runtime providers or SDK roots such as Docker, AWS,
Kubernetes, or the external interpreters package. Operations owns the dispatcher
protocol, durable authorities, stores, UnitOfWork, coordinator, read models, and
application services; it does not own concrete SDK clients.

The cpk-server guardrails prove the process wrapper may lazily compose concrete
providers at bootstrap, but only inside approved provider/resolver functions:

```text
_runtime_adapter
  -> _docker_runtime_interpreter
    -> control_plane_kit_interpreters.docker

_image_pull_credential_resolver
  -> control_plane_kit_interpreters.secrets

_product_secret_resolver
  -> secret-free process config + local development resolver
```

Disabled runtime dispatch (`CPK_RUNTIME_INTERPRETERS=none`) still returns the
unsupported execution adapter and does not import the Docker provider. The
readiness route reports only runtime capability (`runtime_interpreters`) and does
not expose store endpoints, Docker config paths, tokens, TLS material, socket
paths, or secret resolver payloads.

A non-blocking residue note remains from earlier seeded topology work: a
standalone nginx workaround was observed during prior local debugging. It is not
part of RUNTIME.AUTH and should be cleaned up or eliminated in the seeded
server-product/topology cleanup lane rather than folded into runtime-authority
semantics.


## #965 Docker Runtime Authority Interpretation

#965 moves admitted runtime-authority use to the concrete interpreter IO boundary.
The governing shape remains:

```text
cpk-server
  -> configured operations application
    -> ExecutionCoordinator
      -> RuntimeInterpreterDispatcher
        -> DockerRuntimeInterpreter
          -> Python Docker SDK
```

Operations now loads active `RegisteredRuntimeAuthority` records into the pinned
activity realization context and supplies a matching authority only when the pure
`RuntimeEffectRequest.authority_ref` requests one. Missing registrations fail
closed before interpreter IO with `runtime.authority-missing`; a configured
interpreter that cannot consume authorities fails closed with
`runtime.authority-interpreter-unsupported`. Requests without an authority
reference keep the existing ambient/local runtime path.

The Docker interpreter consumes the authority structurally rather than importing
operations models:

```text
RuntimeEffectRequest + authority-like object
  -> local-docker-socket: reuse ambient DockerSdkClient
  -> remote-docker-tls: resolve ca/cert/key SecretReference values
    -> DockerTlsClientConfig
      -> docker.DockerClient(base_url=..., tls=...)
```

The runtime authority is still generic at the operations boundary. Docker is only
the first concrete authority family implemented. Remote TLS material remains
secret-free until interpreter IO: descriptors, runtime requests, events,
observations, read models, route responses, and failure messages contain only
bounded references and status codes, never certificate/key bytes.

Validation evidence:

```text
control-plane-kit-operations ./test.sh: 150 tests passed, compileall, import ok
control-plane-kit-interpreters ./test.sh: 74 tests passed, compileall, import ok
git diff --check: passed for control-plane-kit-interpreters before closeout
```

Handoff: #966/#967 can now exercise runtime authorities through cpk-server and
published-image acceptance without teaching operations or core about Docker SDK
clients. Future AWS/GCP/Kubernetes/remote-Docker authority variants should reuse
the same split: generic durable authority admission in operations, concrete SDK
client construction in the matching interpreter package.


## #967 Runtime Authority HTTP/MCP Registration Surface

#967 exposes runtime-authority admission through the same cpk-server application
boundary used by the rest of the operator workflow. The resulting split is:

```text
Interpreter Availability
  = process-level installed/enabled interpreter families

RegisteredRuntimeAuthority
  = workspace-scoped operational truth for one concrete runtime target
```

Core now names the public command/read surfaces and focused permission scopes:

```text
runtime-authority.register
runtime-authority.revoke
read.runtime-authorities
read.runtime-authority-detail

runtime-authority:register
runtime-authority:read
runtime-authority:revoke
runtime-authority:use
```

Operations owns the durable service and transaction boundary. Registration and
revocation require their focused scopes; graph execution permission alone does
not admit or revoke runtime authority truth. The cpk-server adapter only parses
HTTP/MCP shaped payloads and calls operations services:

```text
HTTP/MCP route
  -> CpkServerPlanningService / CpkServerReadService
    -> RuntimeAuthorityRegistrationService / InstanceReadService
      -> Postgres UnitOfWork
```

Readback remains intentionally redacted. A remote Docker TLS authority can store
the endpoint and secret references in operations-owned storage, but public
descriptors expose only bounded metadata and `SecretReference` ids. The tests
prove that route responses do not contain the endpoint host/port or secret
material.

Important decision: #967 does not perform Docker effects. It registers the
authority a later execution may use. Concrete authority materialization still
belongs to the interpreter IO boundary established by #965.

Validation evidence:

```text
control-plane-kit-core ./test.sh: 401 tests passed, compileall, import ok
control-plane-kit-operations ./test.sh: 152 tests passed, compileall, import ok
git diff --check: passed
```

Handoff: #968 can now prove that an already-running Docker-capable cpk-server can
register a local/remote Docker authority through public routes and then execute a
child topology against that registered authority. If the cpk-server image embeds
the updated operations commit for that acceptance, republish the image and update
server-product coordinates before the live smoke.


## #988 Authority Delivery Dry Run

#988 records the missing concept exposed by the local recursive cpk-server chain
experiment. The experiment attempted this operator story:

```text
parent cpk-server
  -> spawns child cpk-server-docker
    -> controller enters child
      -> child registers local-docker-socket authority
        -> child attempts to spawn the next cpk-server
```

The child correctly admitted a workspace runtime authority, but execution failed
when the child interpreter tried to connect to local Docker:

```text
docker.runtime-authority-uncertain
DockerException: Error while fetching server API version:
FileNotFoundError(2, "No such file or directory")
```

The failure is not a bug in `DockerSdkClient.from_authority(...)`. It proves the
new law:

```text
RegisteredRuntimeAuthority
  = a workspace/operator has admitted a runtime target

RuntimeAuthorityAccessDelivery
  = a specific cpk-server process has received the capability material needed
    to use that target
```

Law cards:

| Law | Evidence | Consequence |
| --- | --- | --- |
| Authority admission does not imply delivery | child could register `local-docker-socket`, then failed to connect | add a distinct delivery concept |
| Interpreter availability does not imply delivery | child reported `runtime_interpreters=docker` but had no socket | never infer socket mount from `CPK_RUNTIME_INTERPRETERS=docker` |
| Local Docker socket is capability material | missing `/var/run/docker.sock` stopped execution | model delivery as privileged runtime access, not ordinary env |
| Remote TLS is the same policy shape with different material | TLS proof passes by endpoint plus `SecretReference` certs | keep one authority/delivery vocabulary across local and remote Docker |
| Cloud runtimes need the same split | AWS/GCP/Kubernetes will require role/session/token delivery | do not make the concept Docker-local only |
| Product identity must not drive delivery | cpk-server needs access only when explicitly granted | no `if cpk-server then mount socket` branch |

Chosen vocabulary for the next issues:

```text
authority = approved runtime target
delivery  = how this process receives access material for that authority
```

The uncommitted local-chain scaffold in `control-plane-kit-servers` is useful,
but it belongs to #992 after #989, #990, and #991 introduce the language,
operations truth, and interpreter materialization. It should not be committed as
part of #988 because it currently assumes delivery without representing it.

Confirmed topology:

```text
#988 dry-run authority delivery laws and recursive local failure
  -> #989 add pure runtime authority delivery contract language
    -> #990 admit/expose delivery intent in operations
      -> #991 materialize local Docker socket delivery in DockerRuntimeInterpreter
        -> #992 prove bounded local recursive cpk-server chain
          -> #993 close authority delivery lane
```

Handoff: #989 should add only the pure contract language. It should reject raw
host paths, TLS keys, tokens, Docker config JSON, cloud keys, kubeconfigs, and
other capability material in durable descriptors. #992 should later convert the
existing local-chain harness rather than rebuilding it from scratch.


## #989 Runtime Authority Delivery Contract Language

#989 adds the pure contract vocabulary that #988 found missing:

```python
RuntimeAuthorityAccessDelivery(
    authority_ref=RuntimeAuthorityReference("mac-mini-docker"),
    delivery_kind=RuntimeAuthorityAccessDeliveryKind.LOCAL_DOCKER_SOCKET_MOUNT,
)
```

That descriptor is intentionally capability-shaped, not material-shaped:

```python
{
    "authority_ref": {"reference_id": "mac-mini-docker"},
    "delivery_kind": "local-docker-socket-mount",
    "secret_references": [],
}
```

Remote Docker TLS and future cloud sessions use the same algebra with labeled
`SecretReference` identities:

```python
RuntimeAuthorityAccessDelivery(
    authority_ref=RuntimeAuthorityReference("mac-mini-docker"),
    delivery_kind=RuntimeAuthorityAccessDeliveryKind.REMOTE_DOCKER_TLS_SECRET_FILES,
    secret_references=(
        RuntimeAuthorityDeliverySecretReference(
            "client-key",
            "secret://local/workspace-a/docker/client-key",
        ),
    ),
)
```

The descriptor still does not contain the key value, mounted file path, Docker
host, socket path, token, Docker config JSON, or cloud credential material. The
strict codec rejects unknown fields such as `host_path`, `endpoint`, `token`, or
secret-reference target paths. This keeps delivery visible in the language
without converting it into an escape hatch.

The new law is:

```text
RuntimeAuthorityReference
  = which admitted runtime target a graph/runtime effect wants

RuntimeAuthorityAccessDelivery
  = which authority access capability this cpk-server process should receive

delivered material
  = interpreter/bootstrap IO, never durable descriptor truth
```

Validation:

```text
git diff --check
./control-plane-kit-core/test.sh
```

`./control-plane-kit-core/test.sh` passed 405 unit tests, compileall, and the
package import check after the #989 language addition.

Handoff: #990 should admit and expose delivery intent in operations as
workspace-scoped, secret-free truth. #991 should materialize
`LOCAL_DOCKER_SOCKET_MOUNT` inside the Docker interpreter/process delivery
boundary without introducing product-specific cpk-server branches.


## #990 Runtime Authority Delivery Admission

#990 adds operations-owned durable truth for authority delivery:

```text
RegisteredRuntimeAuthority
  = workspace admits target

RegisteredRuntimeAuthorityDelivery
  = workspace admits that a process may receive access material for that target
```

The Postgres schema now includes `cpk_runtime_authority_deliveries`, with one
active delivery per `(workspace_id, authority_ref)`. Delivery registration
requires an active registered runtime authority in the same workspace; missing
or revoked authority fails closed before any runtime effect.

The application service extends `RuntimeAuthorityRegistrationService` with:

```python
register_delivery(RegisterRuntimeAuthorityDeliveryCommand(...))
revoke_delivery(RevokeRuntimeAuthorityDeliveryCommand(...))
```

and the cpk-server operations adapter now recognizes route-shaped commands and
reads:

```text
command.runtime-authority-delivery.register
command.runtime-authority-delivery.revoke
read.runtime-authority-deliveries
read.runtime-authority-delivery-detail
```

The permission split is explicit:

```text
runtime-authority:register
  != runtime-authority-delivery:register
  != runtime-authority-delivery:read
  != runtime-authority-delivery:revoke
  != runtime-authority:use
```

The read model runs delivery descriptors through the existing redaction policy.
That means delivery secret-reference lists are stored for later interpreter or
bootstrap IO, but public readback reports them as `"<redacted>"`. This is stricter
than merely exposing `SecretReference` identities and keeps the later secrets
service path clean.

Validation:

```text
git diff --check
./control-plane-kit-operations/test.sh
./control-plane-kit-core/test.sh
```

Operations passed 158 tests, compileall, and import check. Core passed 405 tests,
compileall, and import check.

Handoff: #991 should consume this operations truth and materialize
`LOCAL_DOCKER_SOCKET_MOUNT` at the Docker interpreter/process boundary. It should
not introduce `if cpk-server then mount socket`; delivery records, not product
identity or interpreter availability, must drive the capability handoff.

## #991 Runtime Authority Delivery Materialization

#991 adds the missing pure bridge between operations-owned delivery admission
and concrete runtime interpretation:

```text
RegisteredRuntimeAuthorityDelivery
  -> ActivityRealizationContext.runtime_authority_deliveries
    -> RuntimeEffectRequest.authority_deliveries
      -> DockerRuntimeInterpreter authority-delivery mount material
```

The `RuntimeEffectRequest` still does not contain `/var/run/docker.sock`,
`unix://...`, host paths, TLS material, tokens, or secret values. It carries only
the closed `RuntimeAuthorityAccessDelivery` descriptor for the request's
matching `authority_ref`.

Important implementation decision:

```text
authority_ref mismatch
  -> delivery is not included in the request

delivery without authority_ref
  -> invalid RuntimeEffectRequest

local-docker-socket-mount
  -> interpreter-owned bind mount constant, not durable graph truth
```

Validation so far:

```text
./control-plane-kit-core/test.sh
./control-plane-kit-operations/test.sh
control-plane-kit-interpreters ./test.sh
```

Core passed 407 tests, compileall, and import check. Operations passed 160
tests, compileall, and import check. Interpreters passed 79 tests, compileall,
and Docker-first package validation.

Hardening found during #991:

```text
unsupported delivery
  -> validate before secret resolution, image pull auth, network creation,
     configuration volume materialization, or container creation
```

The first interpreter test run showed unsupported delivery was detected too late,
after configuration helper materialization. The final implementation validates
authority delivery mounts immediately after selecting node product material.

Handoff to the interpreter half of #991: materialize
`LOCAL_DOCKER_SOCKET_MOUNT` only from `RuntimeEffectRequest.authority_deliveries`.
Handoff to #992: convert the local recursive cpk-server experiment by registering
both `local-docker-socket` authority and `local-docker-socket-mount` delivery
inside each child before executing its next graph.

## #1004 Seeded Stress Public Workspace Matrix

#1004 refreshes #941 after RUNTIME.AUTH and authority delivery closeout. The
important topology change is that seeded stress is no longer a single hosted
scenario. It is now a public multi-workspace acceptance lane driven by one
published `cpk-server-docker`:

```text
one published cpk-server-docker
  -> public HTTP/MCP workflow
    -> workspace A router transition
    -> workspace B multiplexer observer delivery
    -> workspace C postgres data-service retained/secret behavior
    -> workspace D negative cases and cleanup
```

The current coordinate truth is recorded in:

```text
artifacts/extraction/seeded-stress-1004-public-workspace-matrix.json
```

Current server-product coordinates at dry run:

```text
cpk-server-docker descriptor:
  0581717ca9dfd2b374ad3913e7b82b9355cf15c4aa83638e724b9ff6436bde88

cpk-server image:
  ghcr.io/openj92/control-plane-kit-servers/cpk-server@sha256:9eacda293d09953289a50adb9476a290b73a2406698ce352bb97904f27c1415b

catalogue/products.json:
  03695d4aa51577556e2cb8149f8127be6f9eb8d4a355ee0ba957829555a31515
```

This supersedes the older #942 matrix checksums. The older artifact remains
historical evidence; #1004 is the governing matrix for the post-delivery stress
lane.

The existing hosted activity controller already has the public workflow spine:

```text
create workspace
  -> import product
    -> set desired graph
      -> MCP plan
        -> request approval
          -> MCP pending/detail
            -> MCP approve
              -> admit
                -> claim
                  -> start
                    -> MCP execute
                      -> advance current graph
                        -> read current graph
```

#1005 should factor that controller into reusable multi-workspace helpers. It
does not need new runtime semantics. It does need to make workspace identity,
runtime authority registration, runtime authority delivery registration, image
pull authority registration, scenario selection, and cleanup labels reusable
rather than fixed to `cpk-hosted-activity-basic`.

#955 remains required before #1008. The current `hello-server` exposes
`/health/live`, `/health/ready`, `/dependencies`, and `/`, but no bounded request
receipt endpoint. Without #955 or equivalent product-owned observer evidence,
the multiplexer proof would collapse to primary-response-only, which is not
acceptable for #941.

The refined order is:

```text
#1004 -> #1005

#1005 -> #1006
#1005 -> #1008
#1005 -> #1007

#1006 + #1008 + #1007 -> #1009 -> #1010
```

Relationship to old children:

```text
#945 = superseded by #1008 and #1007 once complete
#946 = superseded by #1009 once complete
#947 = superseded by #1010 once complete
```

The closeout should explicitly connect the proof back to Pottery Factory uptime:

```text
Deploy(current, saved_good_topology)
```

and the future shape:

```text
auth
  -> router/load balancer
    -> pottery-factory-api/services
```

## #1024 Gateway Target-Map Language

#1024 confirmed that the gateway target map belongs in core as pure runtime
effect language. It is not durable registration truth, not a product descriptor,
and not gateway process implementation. It is the bounded value operations can
derive from graph/socket truth and hand toward a runtime interpreter.

The first-pass shape is intentionally narrow:

```text
GatewayTargetMap
  GatewayHttpTarget(target_id, node_id, provider_socket, url, source_edges)
  GatewayPostgresTarget(target_id, node_id, provider_socket, host, port, source_edges)
```

Important laws now tested in `control-plane-kit-core`:

- target id is `node_id.provider_socket`;
- HTTP and Postgres protocols are closed and decoded strictly;
- duplicate target ids fail closed;
- unsupported protocols fail closed;
- HTTP URL credentials fail closed;
- secret-shaped target values fail closed;
- the descriptor does not mention `cpk-local-gateway` product identity.

The Postgres target map stays secret-free. It carries host/port only. The
gateway process added in #1023 can read a password from a named environment
slot, but #1024 does not put that slot into core target-map truth. #1025 must
decide how explicit graph/secret delivery material populates gateway runtime
configuration while preserving the no-secret-descriptor law.

Handoff:

```text
core:
  GatewayTargetMap

operations:
  graph/socket truth -> GatewayTargetMap

interpreter:
  GatewayTargetMap -> cpk-local-gateway runtime configuration

gateway product:
  closed semantic probes against declared private targets
```

## #1025 Gateway Target-Map Materialization

#1025 used the existing generic public-environment materialization path rather
than adding a Docker-specific gateway branch. The selected product instance opts
in by carrying the public environment name:

```text
CPK_GATEWAY_TARGETS_JSON
```

When operations starts a node with that environment binding, it derives a
gateway target map from the desired graph:

```text
same-runtime graph edges
  -> provider node id
  -> provider socket
  -> provider protocol
  -> registered provider runtime port
  -> gateway process JSON
```

The Docker interpreter does not know `cpk-local-gateway` exists. It already
knows how to pass `RuntimeProductMaterial.public_environment` into a container,
so the generated target map flows through the same path as any other
non-secret product configuration.

The first-pass process JSON remains aligned to the #1023 gateway process:

```json
{
  "postgres.postgres": {"protocol": "postgres", "host": "postgres", "port": 5432},
  "router.internal": {"protocol": "http", "url": "http://router:8000"}
}
```

Laws preserved:

- no target map is generated from product identity;
- no target map is delivered to ordinary products unless they opt in through
  the public environment contract;
- missing provider runtime ports fail closed;
- unsupported target protocols fail closed;
- target maps contain no raw secrets;
- Docker remains generic environment materialization.

Handoff to #1026:

The live Docker proof should instantiate `cpk-local-gateway` alongside Postgres
and an HTTP product, then use the gateway control endpoint to run private
Postgres `select-one` and HTTP status probes. If Postgres authentication is
needed, keep it in explicit secret delivery/environment material and do not put
the password into target-map JSON.

## #1027 Local Gateway Foundation Closeout

#1026 proved the focused local runtime-island gateway behavior in
`control-plane-kit-servers` through PR #41:

```text
host / parent probe client
  -> cpk-local-gateway public control endpoint
    -> private Docker network
      -> hello-server HTTP readiness
      -> postgres-server SELECT 1
```

The smoke intentionally exposed only the gateway on the host:

```text
127.0.0.1:$GATEWAY_PORT -> cpk-local-gateway:8000
```

`hello-server` and `postgres-server` remained private Docker-network targets.
The smoke rejected unknown targets and unsupported probe kinds, so the gateway
did not become an arbitrary HTTP/TCP proxy.

Final first-pass gateway shape:

```text
cpk-local-gateway:
  GET /health/live
  GET /health/ready
  POST /cpk/probes

closed probes:
  http-status
  postgres-select-one
```

The gateway still does not own graph truth, maintain local topology, spawn
nodes, or mutate runtime state. Parent `cpk-server` continues to own desired
graph, current graph, approval, history, and read models. The gateway receives
graph-derived target material and performs bounded local semantic probes from
inside the runtime island.

Security/data findings:

- gateway target maps are secret-free;
- Postgres password material stayed in explicit process environment for the
  direct smoke and did not enter target-map JSON or probe responses;
- probe responses were bounded and redacted;
- database sockets remained private by default;
- cleanup removed only labelled smoke resources and used no broad Docker prune.

Validation evidence:

```text
control-plane-kit-servers ./test.sh
scripts/cpk_local_gateway_private_probe_smoke.sh
git diff --check
```

Handoff back to #1007:

The paused workspace C Postgres retained-data stress should resume by routing
semantic readiness through the gateway:

```text
cpk-server hosted workflow
  -> graph contains cpk-local-gateway + postgres-server
  -> operations materializes CPK_GATEWAY_TARGETS_JSON from graph edges
  -> runtime starts both nodes on the same Docker network
  -> PostgresQueryCheck(select-one) or equivalent closed probe goes through
     cpk-local-gateway
```

Avoid `sync_runtime_networks` for this semantic readiness path except as a
documented diagnostic. Retained volume assertions and secret leak assertions
remain required.

Handoff to #1012:

Cloudflare/control ingress should target the gateway control endpoint, not every
private workload service. Per-service ingress remains an explicit
application-facing requirement, not the default control-plane probing strategy.

Handoff to #1020:

The gateway solves first-pass local probe reachability. It does not solve
runtime spawning delegation, local graph ownership, offline agent behavior, or a
full CPK client. Those remain future client/agent work.

## #1014 Named Public Ingress Core Language

#1014 added the provider-neutral ingress vocabulary needed before the Cloudflare
Model B interpreter lane:

```text
NamedPublicIngress
  -> IngressAuthorityReference
  -> PublicIngressTarget(node_id, provider_socket)
  -> hostname
  -> exposure
  -> lifecycle
```

The important naming decision is that core does not own
`CloudflareNamedIngress`. Cloudflare is the first concrete provider, but core
only says:

```text
expose this declared provider socket
at this stable public hostname
using this admitted ingress authority
```

The concrete provider work remains later:

```text
operations:
  RegisteredIngressAuthority(provider_kind="cloudflare")

interpreters:
  CloudflareNamedIngressInterpreter

server-products:
  cloudflared connector product
```

The new language preserves the gateway boundary: sockets still describe private
topology wiring, and named public ingress is socket-adjacent exposure. It does
not replace socket compatibility, make the gateway a graph owner, or expose
private workload sockets directly.

Security findings:

- `IngressAuthorityReference` is secret-free;
- `NamedPublicIngress` descriptors reject provider-specific fields such as
  tunnel tokens and API tokens;
- `PublicIngressObservation` stores bounded endpoint evidence only;
- Cloudflare API tokens and generated tunnel tokens remain future
  secret-reference/IO-boundary work for #1035, #1016, and #1037.

## #1035 Cloudflare Ingress Authority Admission

#1035 adds the operations-owned admission surface for named public ingress
authority. Core remains provider-neutral: it knows only ingress authority
references, named ingress requests, targets, and observations. Operations now
admits a concrete Cloudflare zone authority as workspace-scoped durable truth:

```text
RegisteredIngressAuthority
  -> authority_ref
  -> provider_kind="cloudflare"
  -> zone_name
  -> allowed_hostname_pattern
  -> api_token_ref
```

The authority is intentionally not an interpreter. It does not create tunnels,
DNS records, or Docker containers. It records that a workspace may later ask an
ingress interpreter to allocate hostnames matching the admitted policy.

Security and authorization decisions:

- Cloudflare API token material is represented only by `SecretReference`.
- Read models expose bounded authority metadata and the secret reference id, not
  token values.
- `ingress-authority:register`, `ingress-authority:read`,
  `ingress-authority:revoke`, and `ingress-authority:use` are distinct from
  graph execution permission.
- Hostname selection fails closed when the admitted pattern does not match.
- `cpk-gateway-*.openj92.dev` is allowed for this lane; nested
  `gateway-001.cpk.openj92.dev` remains out of scope until wildcard certificate
  coverage is explicitly added.

Validation evidence:

- `./control-plane-kit-core/test.sh` passed 417 tests, compileall, and import.
- `./control-plane-kit-operations/test.sh` passed 172 tests, compileall, and
  import.
- Full root `./test.sh` remains diagnostic evidence for this surface rather
  than a child-issue merge gate when unrelated generated live-process tests are
  red. The #1035 retry ran 1232 tests and failed in three pre-existing live
  readiness paths:
  `test_http_bulkhead_server_block`,
  `test_http_load_generator`, and `test_idempotency_gateway`. None exercised
  ingress authority registration, readback, revocation, or secret redaction.

Handoff:

- #1015 can introduce the `cloudflared` connector product descriptor without
  storing tunnel tokens in descriptors.
- #1016 can implement `CloudflareNamedIngressInterpreter` by resolving the
  admitted `RegisteredIngressAuthority` token reference only at the IO boundary.
- #1036 must record owned Cloudflare resource evidence before any teardown.

## #1036 Cloudflare Owned Resource Evidence

#1036 adds the first operations-side cleanup policy for named public ingress.
Core remains provider-neutral; Cloudflare names stay at the operations and
interpreter boundary.

The evidence shape is intentionally bounded and secret-free:

```text
CloudflareOwnedIngressResource
  workspace_id
  runtime_id
  ingress_id
  tunnel_name
  tunnel_id
  dns_record_id
  hostname
  zone_id
  lifecycle = ephemeral | retained | external
  created_at
  observed_at
```

Teardown is now an evidence-derived plan rather than a broad search:

```text
ephemeral owned resource
  -> delete DNS record by recorded dns_record_id
  -> delete tunnel by recorded tunnel_id

retained/external resource
  -> observe and skip deletion
```

The policy fails closed when ownership evidence is missing, the zone does not
match the admitted authority, the hostname falls outside the admitted pattern, or
the tunnel name lacks the CPK-owned prefix. It does not delete or mutate
`auth-potteryfactory`, permanent `cpk.openj92.dev`, unrelated Cloudflare
resources, or Pottery Factory resources.

Validation evidence:

- `git diff --check` passed.
- `./control-plane-kit-operations/test.sh` passed 176 tests, compileall, and
  import.

Handoff:

- #1037 can attach generated tunnel-token delivery to this ownership evidence.
- #1017 can use the evidence plan for two-island live tunnel cleanup.
- A durable ingress-resource store remains deferred until live execution proves
  the exact persistence boundary.

## #1037 Cloudflare Tunnel Token Delivery

#1037 adds the first explicit delivery plan for a generated Cloudflare tunnel
token. The token itself remains IO-bound secret material. Operations records and
passes only a `SecretReference` through a closed `SecretEnvironmentDelivery`:

```text
Cloudflare API allocation
  -> generated tunnel token value
    -> record as secret material outside graph/runtime descriptors
      -> SecretEnvironmentDelivery("TUNNEL_TOKEN", SecretReference(...))
        -> start cloudflared connector
```

The delivery plan preserves deterministic activity ordering:

```text
allocate-named-ingress
  -> record-tunnel-token-secret
    -> start-cloudflared-connector
```

This keeps the split crisp:

- Docker materialization receives an ordinary secret delivery and never calls
  Cloudflare APIs.
- Cloudflare interpretation creates ingress resources and never starts Docker
  containers.
- The connector cannot start unless exactly one explicit `TUNNEL_TOKEN` delivery
  is present.
- Zone and hostname authority checks still gate token delivery.

Validation evidence:

- `git diff --check` passed.
- `./control-plane-kit-operations/test.sh` passed 179 tests, compileall, and
  import.

Handoff:

- #1017 can now run two runtime islands by combining Cloudflare allocation,
  owned-resource evidence, explicit tunnel-token secret delivery, and Docker
  connector startup.
- A real secret provider/service remains future work; this pass models the
  secret reference and delivery contract that such a provider will satisfy.

## #1044 Named Public Ingress Graph Topology

#1044 attaches provider-neutral public ingress intent to the graph language.
`NamedPublicIngress` is graph-level desired exposure, not a Cloudflare object and
not an ordinary product node:

```text
NamedPublicIngress
  authority_ref -> RegisteredIngressAuthority
  target -> node provider socket
  connector_node_id -> ordinary connector node in the same runtime island
  hostname -> stable desired public name
  lifecycle -> ephemeral | retained | external
```

The connector remains explicit because the runtime still has to realize an
ordinary workload that can attach to the allocated ingress. For Cloudflare, that
ordinary workload is the `cloudflared-connector` product. Core does not name
Cloudflare or tunnel resources; it only records that a named public ingress
requires a connector node and a target provider socket.

The graph codec now preserves `public_ingresses` and validates:

- target node exists;
- target provider socket exists;
- connector node exists;
- connector and target share a runtime island;
- provider-specific keys such as `provider_kind` are rejected at the core graph
  boundary.

This keeps sockets as private topology wiring while making public exposure a
socket-adjacent graph obligation. Later issues can compile this graph obligation
into operations activity:

```text
NamedPublicIngress
  -> allocate provider ingress
    -> deliver generated tunnel token secret
      -> start connector node
        -> observe public hostname reaching the private gateway socket
```

Validation evidence:

- `git diff --check` passed.
- `./control-plane-kit-core/test.sh` passed 420 tests, compileall, and import.
- `./control-plane-kit-operations/test.sh` passed 179 tests, compileall, and
  import.

Handoff:

- #1045 should persist owned allocation evidence before later translation work
  attaches `NamedPublicIngress` to ordered activity.
- #1047 should keep the Cloudflare interpreter attached to the ingress authority
  and generated secret delivery path.
- #1048 should verify route parity without letting cpk-server own ingress
  semantics.

## #1045 Public Ingress Allocation Evidence

#1045 moves Cloudflare ingress allocation evidence from value-only planning into
operations-owned Postgres truth. The resource evidence remains bounded and
secret-free:

```text
CloudflareOwnedIngressResource
  workspace_id
  runtime_id
  ingress_id
  authority_ref
  provider_kind = cloudflare
  hostname
  zone_id
  tunnel_name
  tunnel_id
  dns_record_id
  lifecycle
  created_at / observed_at
  source_run_id / source_activity_id / source_event_id
```

The new `IngressResourceStore` records this evidence through the same
UnitOfWork-backed Postgres connection as the rest of operations. It never
commits independently. Replaying the exact same allocation evidence is
idempotent; conflicting replacement for the same `(workspace_id, ingress_id)`
fails closed until a future explicit replacement policy exists.

Schema added:

```text
cpk_cloudflare_ingress_resources
  primary key (workspace_id, ingress_id)
  provider_kind check = cloudflare
  lifecycle check = PublicIngressLifecycle
  metadata jsonb object check
```

This gives teardown and later interpreter integration a durable owned-resource
record:

```text
allocation effect result
  -> short transaction records ids and source event
    -> future teardown deletes only by recorded owned ids
```

Validation evidence:

- `git diff --check` passed.
- `./control-plane-kit-operations/test.sh` passed 180 tests, compileall, and
  import.

Handoff:

- #1046/#1047 can use `IngressResourceStore.record_cloudflare(...)` after the
  Cloudflare allocation effect succeeds and before connector startup depends on
  generated token delivery.
- #1036 cleanup policy can now be backed by durable evidence rather than
  in-memory smoke artifacts.
- Replacement, retention read UX, and a durable secret provider remain separate
  future work.

## #1053 Public Ingress Diff And Activity Language

#1053 makes named public ingress visible to the pure graph diff and activity
planning language. This is still provider-neutral core language:

```text
DeploymentGraph.public_ingresses
  -> PublicIngressSubject(ingress_id)
    -> PublicIngressValue(NamedPublicIngress)
      -> AllocatePublicIngress(PublicIngressActivityTarget)
      -> RemovePublicIngress(PublicIngressActivityTarget)
```

The compiler now treats added public ingress as an allocation obligation and
removed public ingress as a removal obligation. Allocation waits for the private
target node to be healthy before public exposure begins, and the connector node
starts only after allocation has produced the later operations-owned delivery
material:

```text
start target
  -> wait target healthy
    -> allocate public ingress
      -> start connector
```

Teardown reverses the public reachability first:

```text
remove public ingress
  -> stop connector / target
```

No Cloudflare provider type entered core. The graph value remains
`NamedPublicIngress`; provider-specific authority resolution, Cloudflare API IO,
generated tunnel-token recording, and Docker connector token delivery remain in
the following #1046 child issues.

Validation evidence:

- `git diff --check` passed.
- `./control-plane-kit-core/test.sh` passed 423 tests, compileall, and import.
- `./control-plane-kit-operations/test.sh` passed 180 tests, compileall, and
  import.

Handoff:

- #1054 must define how operations records generated tunnel tokens as
  `SecretReference` without exposing raw token material.
- #1055 must call the provider interpreter outside the Postgres transaction and
  fold bounded ingress allocation evidence into operations.
- #1056 must deliver the generated token reference to the connector startup path
  without inferring behavior from product identity or
  `CPK_RUNTIME_INTERPRETERS`.

## #1054 Generated Ingress Token Secret Recording

#1054 defines the operations-owned boundary for provider-generated tunnel
tokens. The Cloudflare interpreter may receive a raw token from provider IO, but
operations must immediately convert that value into reference-only evidence:

```text
Cloudflare API effect result
  -> SecretValue(<redacted>)
    -> GeneratedSecretRecorder
      -> SecretReference
        -> GeneratedIngressSecretReference
```

The durable Postgres surface records only lineage and the opaque reference:

```text
cpk_generated_ingress_secret_references
  workspace_id
  purpose = cloudflared-tunnel-token
  secret_ref
  recorded_at
  source_run_id / source_activity_id / source_event_id
```

This intentionally stays separate from `cpk_cloudflare_ingress_resources`.
Cloudflare resource evidence answers “what public resource do we own and may
delete?” Generated secret evidence answers “which secret reference was produced
for this activity?” Keeping those records separate avoids making cleanup depend
on secret delivery and avoids leaking secret-shaped state through resource read
models.

The current `InMemoryGeneratedSecretRecorder` is a development boundary, not a
durable secret product. It accepts raw `SecretValue` instances and returns
deterministic `secret://generated/...` references. The raw value never enters
descriptors, graph data, runtime request descriptors, events, observations, read
models, route responses, logs, or errors.

Validation evidence:

- `git diff --check` passed.
- `./control-plane-kit-operations/test.sh` passed 182 tests, compileall, and
  import.

Handoff:

- #1055 should use `GeneratedSecretRecorder` after Cloudflare allocation returns
  a token and before connector startup is planned.
- #1055/#1056 should persist `GeneratedIngressSecretReference` in the same short
  operation transaction that records bounded allocation/delivery evidence.
- A future secret-service product should replace the in-memory recorder without
  changing the durable `SecretReference` evidence shape.

## #1055 Ingress Provider Effect Folding

#1055 adds the operations-owned adapter that turns public-ingress activities into
provider IO without letting operations own Cloudflare SDK behavior:

```text
AllocatePublicIngress
  -> load active RegisteredIngressAuthority in a short transaction
    -> call injected IngressProviderInterpreter outside Postgres
      -> record owned Cloudflare resource evidence in a short transaction
      -> record GeneratedIngressSecretReference in the same short transaction
        -> return bounded activity evidence
```

The adapter is intentionally provider-neutral at the coordinator boundary. It
receives an injected interpreter keyed by `IngressAuthorityProviderKind`; the
Cloudflare-specific API client still belongs outside operations. Operations only
resolves admitted authority truth, enforces hostname policy through the store,
records owned resource evidence, and records the generated connector material as
a reference.

A useful guardrail surfaced during validation: activity evidence rejected
`tunnel_token_ref` because secret-shaped keys cannot enter durable event
evidence. The final shape keeps the reference in
`cpk_generated_ingress_secret_references` and exposes only
`connector_material_recorded = true` in bounded outcome evidence. That prevents
observations/read models from becoming a secret-reference index while preserving
lineage for the later connector delivery issue.

Validation evidence:

- `git diff --check` passed.
- `./control-plane-kit-operations/test.sh` passed 183 tests, compileall, and
  import.
- `./test.sh` passed 1232 tests.

Handoff:

- #1056 should consume the generated ingress secret reference through the
  explicit delivery path and materialize it into the cloudflared connector
  startup without putting raw tokens or secret references into graph descriptors.
- The cpk-server composition pass must adapt the concrete Cloudflare interpreter
  into the operations `IngressProviderInterpreter` protocol; cpk-server should
  compose this dependency but not own provider semantics.

## #1056 Ingress Connector Token Delivery

#1056 threads generated Cloudflare tunnel material into connector startup through
the existing secret-delivery language instead of adding a new runtime secret
lane:

```text
NamedPublicIngress(connector_node_id=cloudflared)
  -> Cloudflare allocation records owned resource evidence
  -> GeneratedIngressSecretReference records SecretReference lineage
  -> StartNode(cloudflared)
    -> RuntimeProductMaterial.product.runtime_contract.secret_deliveries
      includes SecretEnvironmentDelivery("TUNNEL_TOKEN", secret://...)
        -> Docker interpreter resolves the value at IO only
```

The important split is now explicit:

```text
core/runtime request:
  carries SecretReference and SecretEnvironmentDelivery values

operations:
  decides which reference belongs to this node/activity from durable ingress
  authority, owned-resource, and generated-secret evidence

interpreter:
  resolves SecretReference -> SecretValue only while materializing the process
```

The delivery is not inferred from product identity or from an environment-name
convention. It happens only when the desired graph contains a
`NamedPublicIngress` whose `connector_node_id` is the node being started. If an
operator supplies an explicit `TUNNEL_TOKEN` delivery on the node, that supports
external/existing tunnel mode without requiring CPK-created Cloudflare resource
evidence.

Validation evidence:

- `git diff --check` passed.
- `./control-plane-kit-operations/test.sh` passed 183 tests, compileall, and
  import.

Handoff:

- The cpk-server hosted/stress path can now start a connector after an ingress
  allocation because runtime-effect translation has a concrete place to carry
  the generated `TUNNEL_TOKEN` reference.
- A later cleanup/hardening pass can refactor more product-specific startup
  magic into `SecretEnvironmentDelivery` and eventually replace the in-memory
  generated-secret recorder with a dedicated secret-service product.

## #1069 Owned Ingress Lifecycle Model

#1069 separates two concepts that were previously too close together:

```text
PublicIngressLifecycle
  desired graph policy: ephemeral | retained | external

OwnedIngressResourceStatus
  operations-owned provider resource state:
    allocating | active | removing | removed | uncertain | orphaned
```

The new status is operations language, not core algebra. Core still describes a
provider-neutral named public ingress and its desired lifecycle policy.
Operations now has the durable vocabulary needed to remember provider-resource
history without treating a removed ingress as if it still blocked all future
allocation forever.

The schema shape now makes Cloudflare owned-resource evidence epoch-bearing:

```text
cpk_cloudflare_ingress_resources(
  workspace_id,
  ingress_id,
  epoch,
  status,
  ...
)
```

with a primary key on `(workspace_id, ingress_id, epoch)` and an active partial
unique index over live states: `allocating`, `active`, and `removing`. This is
the data-engineering shape needed for the public gateway overlay toggle:

```text
G0 = workload + gateway + ingress
G1 = workload
G2 = workload + gateway + ingress
```

#1069 intentionally does not implement reallocation or lifecycle transitions.
It gives #1068/#1067 the durable state machine and schema target they need.

Validation evidence:

- `git diff --check` passed.
- `./control-plane-kit-operations/test.sh` passed 187 tests, compileall, and
  import.

Handoff:

- #1068 should add active/latest lookup and transition methods over the new
  epoch/status shape.
- #1068 should preserve idempotent same-epoch recording, reject active
  replacement, and permit reallocation only after a previous epoch is removed.
- #1067 should fold allocation/removal through short transactions around
  provider IO, using the live states to avoid the orphan window discovered by
  #1064.

## #1068 Owned Ingress Resource Epoch Stores

#1068 turns the #1069 model into executable store behavior:

```text
record active ingress with no history
  -> epoch 1 active

mark epoch 1 removed
  -> epoch 1 remains in history

record same ingress after removal
  -> epoch 2 active
```

The store now treats `allocating`, `active`, `removing`, `uncertain`, and
`orphaned` as blocking states. A removed epoch no longer satisfies active
lookup and no longer blocks reallocation. This preserves provider-resource
history while letting graph transitions remove and later recreate the same
public ingress overlay.

The new transition methods are intentionally small:

```text
require_active_cloudflare(workspace, ingress)
mark_removing(workspace, ingress, source_run_id)
mark_removed(workspace, ingress, removed_at, removed_by_run_id)
mark_uncertain(workspace, ingress, source_run_id)
```

They update durable ingress-resource state only. They do not call Cloudflare,
resolve secrets, start containers, or commit independently. Command services and
coordinator folding still own transaction boundaries.

Validation evidence:

- `git diff --check` passed.
- `./control-plane-kit-operations/test.sh` passed 190 tests, compileall, and
  import.

Handoff:

- #1067 should use `mark_removing` before Cloudflare teardown IO and
  `mark_removed` after successful provider deletion.
- #1067 should reserve or otherwise protect allocation epochs before Cloudflare
  creation, then mark active or uncertain after the provider result.
- #1067 should leave uncertain/orphaned rows visible and blocking until a
  deliberate reconciliation path exists.

## #1067 Owned Ingress Lifecycle Folding

#1067 wires the owned-resource lifecycle states into the public-ingress
realization adapter without moving Cloudflare semantics into operations:

```text
remove-public-ingress
  -> short tx: require active owned resource and mark removing
    -> CloudflareNamedIngressInterpreter.teardown(...)
      -> short tx: mark removed
```

If provider teardown fails, operations records the epoch as `uncertain` in a
separate short transaction. That preserves the exact provider-resource evidence
and blocks unsafe same-key reentry until a deliberate reconciliation path exists.

Allocation now preflights for existing blocking resource evidence before calling
the provider. If provider allocation succeeds but durable result folding fails,
the adapter attempts bounded compensation by asking the provider interpreter to
tear down the just-created resources, then returns an uncertain result. This is
not a full reconciliation system, but it closes the easy orphan window exposed
by the first public gateway toggle attempt.

The boundary remains:

```text
operations:
  load authority/resource truth
  fold lifecycle status
  record bounded evidence

Cloudflare interpreter:
  provider API calls only

Postgres:
  never held open across Cloudflare API IO
```

Validation evidence:

- `git diff --check` passed.
- `./control-plane-kit-operations/test.sh` passed 192 tests, compileall, and
  import.

Handoff:

- #1066 should resume the server-products `public-gateway-toggle` scenario.
- The expected third transition should now allocate a new active epoch after
  the first epoch is removed.
- If cpk-server image acceptance uses these operations changes, server-products
  coordinates must be updated and cpk-server variants republished before the
  final published-image smoke.

## #1075 Fresh Coordinator Evidence Between Activity Steps

#1075 tightened the evidence boundary exposed by the public ingress toggle
smoke. Cloudflare owned-resource rows are durable epoch history, so runtime
translation must treat only `active` ingress resources as connector-token
evidence. Removed epochs remain visible for audit and cleanup history, but they
cannot authorize a new cloudflared connector start.

The coordinator contract is:

```text
activity N writes owned-resource/generated-secret evidence
  -> transaction commits
    -> activity N+1 loads a fresh realization context
      -> no re-plan, no transaction across provider/Docker/network IO
```

Focused coverage now proves both halves: the coordinator passes side evidence
written by one adapter call to the next activity in the same pinned run, and
runtime-effect translation selects the active ingress epoch when a removed epoch
with the same ingress id remains in history.

Validation evidence:

- `git diff --check` passed.
- `./control-plane-kit-operations/test.sh` passed 195 tests, compileall, and
  import.

Handoff:

- #1076 should update server-products coordinates to the merged #1075
  operations commit, republish the cpk-server variants if needed, and retry the
  `public-gateway-toggle` smoke through the real cpk-server workflow.

## #1078 Cloudflare Allocation Postcondition Evidence

#1078 tightened the public-ingress allocation postcondition without changing the
provider behavior. Allocation success evidence now carries the same nonsecret
identity fields as the durable owned-resource row:

```text
runtime_id
tunnel_name
tunnel_id
dns_record_id
hostname
lifecycle
```

This gives later gateway-readiness and toggle acceptance code a bounded way to
reason about the exact active Cloudflare epoch that was created, without
reading secret material or relying on ad hoc log text.

The boundary remains:

```text
operations:
  records durable owned-resource truth and nonsecret postcondition evidence

Cloudflare interpreter:
  creates tunnel, DNS, and connector token at the IO boundary

secret material:
  remains referenced, not exposed in evidence/readback/logs
```

Validation evidence:

- `git diff --check` passed.
- `./control-plane-kit-operations/test.sh` passed 195 tests, compileall, and
  import.

Handoff:

- #1079 should improve connector/public-readiness observation pacing so fast
  non-ready HTTP responses do not exhaust the bounded wait loop before
  Cloudflare has attached the connector.
- #1080 should retry `public-gateway-toggle` after the #1078/#1079 fixes are
  available in the published cpk-server image used by the smoke.

## #1081 Public Ingress Lifecycle Acceptance

#1081 closed the focused public-ingress lifecycle retry. The successful live
acceptance path used the published cpk-server image, not a local rebuild:

```text
ghcr.io/openj92/control-plane-kit-servers/cpk-server
  @sha256:f67c5f75e7ffc1d6e0932a3042eb22b3bf5f0e0d5fafb83f57e435eeb0c68f8a
```

The accepted graph sequence was:

```text
Deploy(empty, workload + gateway + cloudflared + NamedPublicIngress)
  -> public gateway probe succeeds

Deploy(current, workload only)
  -> public gateway becomes unreachable
  -> workload remains alive privately

Deploy(current, workload + gateway + cloudflared + NamedPublicIngress)
  -> public gateway probe succeeds again
```

This proves the gateway/ingress pair is an access overlay, not the spawning or
control path. cpk-server continued to control the Docker runtime through the
registered runtime authority while the public access surface was removed and
re-created.

The readiness fix from #1079 matters here: Cloudflare can return fast non-ready
HTTP responses before the connector has attached. The public readiness loop now
paces all unsuccessful attempts, not only exceptions, so it is a real bounded
wait instead of a tight retry loop.

Coordinate evidence:

- control-plane-kit commit:
  `066f8a42cfa3a4767fb366ec5adccf02eccc9d99`
- control-plane-kit-interpreters commit:
  `994edc4d28fe9db48d32c853830558dff72f33ab`
- cpk-server image source commit:
  `9bd63b68d4817ac9925e303e7071b144ddf700ff`
- catalogue checksum:
  `66a2418a50a24ba7227cd0eb24a68a9ea140eee5925ff6161d8bda21debaa272`

Validation evidence:

- `PYTHONPATH=src python3 scripts/apply_coordinates.py --check` passed.
- `git diff --check` passed.
- `scripts/cpk_server_published_image_smoke.sh sha256:f67c5f75e7ffc1d6e0932a3042eb22b3bf5f0e0d5fafb83f57e435eeb0c68f8a`
  passed.
- `./test.sh` passed in `control-plane-kit-servers`.
- `CPK_HOSTED_ACTIVITY_SCENARIO=public-gateway-toggle
  CPK_HOSTED_ACTIVITY_BUILD_CONTROLLER=1
  scripts/cpk_server_hosted_activity_smoke.sh` passed.

Handoff:

- #1066 can close the lifecycle child topology with the overlay-toggle evidence.
- #1049 / #1038 can resume seeded stress with public gateway ingress available
  as a proven access overlay.
- Future lifecycle generalization should preserve this graph-transition shape
  for servers, runtimes, ingress, and cleanup rather than treating public
  access teardown as provider scripting outside the deploy program.

## #1049 Seeded Stress Public Ingress Rebase

#1049 rebased seeded stress acceptance onto public gateway ingress instead of
direct private-network reachability. The aggregate hosted scenario runs four
workspaces from one published cpk-server process:

```text
workspace-a-router
  router transition through public gateway ingress

workspace-b-multiplexer
  multiplexer observer scenario through public gateway ingress

workspace-c-postgres
  retained Postgres data-service scenario through public gateway ingress

workspace-d-negative-cleanup
  deploy workload + gateway + cloudflared + NamedPublicIngress
    -> public gateway probe succeeds
  deploy empty graph
    -> public gateway becomes unreachable
    -> Docker runtime networks are removed
```

The important proof is that public gateway ingress is now the observation path
for seeded stress, while runtime authority remains the control path for
realizing and cleaning the Docker island.

The aggregate smoke intentionally uses the published cpk-server image digest:

```text
ghcr.io/openj92/control-plane-kit-servers/cpk-server
  @sha256:f67c5f75e7ffc1d6e0932a3042eb22b3bf5f0e0d5fafb83f57e435eeb0c68f8a
```

No cpk-server image republish was needed for #1049 because the change was in
hosted acceptance scripts and static tests, not the cpk-server product runtime
or generated catalogue coordinates.

Validation evidence:

- `git diff --check` passed in `control-plane-kit-servers`.
- `python3 -m compileall scripts/cpk_server_hosted_activity.py` passed.
- Docker unittest slice
  `python -m unittest products.cpk_server.tests.test_image_bootstrap` passed
  32 tests.
- Full `./test.sh` passed in `control-plane-kit-servers`, including cpk-server
  image smoke and Docker residue audit.
- `CPK_HOSTED_ACTIVITY_SCENARIO=seeded-stress-public-ingress
  CPK_HOSTED_ACTIVITY_BUILD_CONTROLLER=1
  scripts/cpk_server_hosted_activity_smoke.sh` passed.

Handoff:

- #1050 can close seeded stress by treating the aggregate scenario as the
  current public-ingress acceptance spine.
- Pottery Factory application topology work should reuse this shape: saved
  desired graphs, runtime authority for realization, and gateway/public ingress
  as a removable access overlay.

## #1100 Trusted Identity Foundation

The authentication dry run confirmed a root trust defect in the live public
boundary:

```text
Bearer prefix presence
  -> payload actor_id + actor_scopes
    -> operations policy checks
```

The replacement language is provider-neutral and credential-free:

```text
CredentialVerifier
  -> AuthenticatedPrincipal
    -> PrincipalAuthorizer
      -> TrustedCommandContext
```

`AuthenticatedPrincipal` carries issuer, subject, a closed principal kind, and
workspace grants made only of `PolicyScope` values. `TrustedCommandContext`
proves that one command's workspace and scopes are exactly one of those grants;
it cannot amplify authority. Operator, service, and worker identities remain
distinct.

The migration inventory is recorded in
`docs/architecture/TRUSTED_IDENTITY_BOUNDARY.md`. The key decisions are:

- cpk-server validates opaque credentials and discards them before dispatch;
- operations derives command authority from the authenticated principal;
- request bodies keep workspace intent but lose authoritative actor/scopes;
- durable history keeps authenticated subject provenance, never credentials;
- gateway delegation remains a separate bounded capability under #1139.

Handoff:

- #1101 must implement one strict verifier path for HTTP and MCP and fail
  before operations dispatch.
- #1102 must remove caller-authored `actor_scopes` and actor provenance from
  public adapters while preserving existing pure policy checks.

## #1101 Strict Credential Validation

#1101 replaced bearer-prefix presence with one injected `CredentialVerifier`
used by both HTTP and MCP. The public process boundary extracts one bounded
ASCII bearer credential, validates it, attaches one credential-free
`AuthenticatedPrincipal`, and discards the credential before operations
dispatch.

The built-in static verifier is explicitly development-only. Production
configuration fails closed when no verifier is configured, when static
credential material is present with verification disabled, or when a static
credential is malformed. The verifier never returns credential bytes and
authentication failures remain bounded and secret-free.

HTTP and MCP now traverse the same authentication function and receive the same
principal for the same credential. Publishing the cpk-server OCI with this
merged dependency and supplying credential material through explicit secret
delivery remains #1103.

## #1102 Trusted Operations Command Context

#1102 removed caller-authored authority from the operations public adapters:

```text
authenticated request principal
  -> exact workspace grant
    -> TrustedCommandContext
      -> explicit route policy
        -> existing typed domain command
```

Every published command and read route has a closed authorization policy.
Workspace grants are checked before UnitOfWork/store access. Durable actor,
approval, registration, and worker provenance comes from the authenticated
subject, while request `actor_id`, `actor_scopes`, and `worker_id` fields are
temporarily accepted only as inert compatibility input.

The migration preserves permission separation:

- runtime and ingress authority register/read/use/revoke are independent;
- plan request, approval, destructive approval, and execution remain
  independent;
- worker lifecycle routes require a worker or service principal with
  `execution:operate`;
- operator principals cannot impersonate workers through request fields;
- admission requires authority-use permission when the transition contains a
  runtime authority or named public ingress.

No credential material enters operations, idempotency fingerprints, durable
history, runtime effects, logs, errors, or read models. #985 owns later removal
of redundant command-level actor/scope fields. #1103 owns published cpk-server
and hosted-controller adoption. #1139 must derive bounded gateway delegation
only after this operations authorization step and must never forward the
inbound bearer credential.

## #1140 Delegated Gateway Probe Language

#1140 defined the pure capability that bridges trusted cpk-server authorization
to a private runtime-island gateway without forwarding the operator credential:

```text
GatewayProbeRequest
  -> canonical request digest
    -> unsigned DelegatedGatewayProbeGrant
      -> outer signing and dispatch in later issues
```

The request vocabulary is closed to HTTP status and Postgres select-one. It
reuses `GatewayTargetId`; no second gateway target identity was introduced.
HTTP paths participate in the canonical digest, so kind, target, and path
substitution all change the bound request.

The grant binds issuer and key id, runtime-island audience, workspace,
originating operation/request, exact gateway node, kind, target, digest,
five-minute maximum lifetime, and `jti`. It contains no compact token,
signature, key material, provider name, transport header, or caller-selected
URL.

The threat model is recorded in
`docs/design/0004-delegated-gateway-probe-threat-model.md`. It explicitly limits
the first replay guarantee to bounded in-process evidence for idempotent
read-only probes. Gateway restart does not preserve replay history. Mutating
controls require a separate durable design.

Health disclosure is also explicit: minimal liveness may remain public, while
readiness and target metadata require delegated authority. #1141 must now
derive grants only after trusted operations authorization and must preserve the
short-transaction/external-effect/short-transaction boundary.

## #1141 Authorized Durable Gateway Probe Operations

#1141 added the operations-owned execution boundary for delegated gateway
probes:

```text
TrustedCommandContext(gateway-probe:use)
  -> lock request id and load exact current graph
    -> derive declared gateway target and private control endpoint
      -> commit durable intended attempt
        -> injected GatewayProbeDispatcher outside the transaction
          -> commit bounded terminal evidence
```

The command cannot accept caller-authored target URLs, runtime ids, issuer
identity, scopes, or signed capability material. Operations derives those facts
from authenticated context, current graph truth, registered products, and
configured delegation policy. HTTP and MCP-shaped adapters call the same
`GatewayProbeCommandService`.

The durable `GatewayProbeAttempt` records correlation, graph/gateway/runtime
identity, exact request digest, issuer/key id, `jti`, bounded validity, status,
and bounded result evidence. It deliberately excludes the compact signed
capability, signature, key material, private gateway endpoint, inbound bearer
credential, and target credentials. Duplicate request ids with identical
intent return the existing attempt without redispatch; changed intent fails
closed.

Docker/Postgres integration tests proved that the dispatcher sees zero active
transactions. They also proved missing scope, stale current graph, undeclared
targets, and incompatible probe kinds reject before dispatch, and that durable
readback remains bounded and secret-free.

Handoff:

- #1142 must implement the real signer, transport, and gateway verification
  path behind `GatewayProbeDispatcher`.
- Signed capabilities remain transient and must never be folded into durable
  attempt evidence.
- #1143 must prove the same service through a real cpk-server process and real
  private HTTP and Postgres targets.

## #1143 Delegated Gateway Verification And Source-Live Gate

#1143 completed the first executable gateway security path:

```text
operator -> cpk-server
  -> operations gateway-probe service
    -> signed delegated capability
      -> cpk-local-gateway verification
        -> private Hello/Postgres probe
          -> bounded observation/readback
```

The run exposed a useful split: runtime authority is the power to realize or
change a runtime island, while a gateway delegated capability is permission for
the gateway to touch a specific private target. The live harness now uses
separate operator and worker static-development principals, because lifecycle
commands are not the same authority as operator planning/approval commands.

Published evidence from this run:

```text
cpk-local-gateway:
  ghcr.io/openj92/control-plane-kit-servers/cpk-local-gateway@sha256:5698c1ee9e8b933920177d260bf44963e2ebbdfc58f791fc98bf6efd21156aa8

cpk-server:
  ghcr.io/openj92/control-plane-kit-servers/cpk-server@sha256:db0de3e0e34dbfe90945af46419576da799a484a7bc45b985767f1cf0131a92d

catalogue checksum:
  0c466216304ae1d4cc9bb5586134865196f283f819f6648fe46c78289c8c929b
```

Validation included focused gateway/cpk-server tests, source-live
`authenticated-gateway-private`, digest-pulled cpk-server image smoke, full
`./test.sh`, and Docker residue audit.

## #1113 Durable Secrets Threat Model

#1144 is intentionally blocked until durable secret custody exists. The
development path can sign gateway grants from a source-live `SecretReference`,
but production must not depend on process-local maps for signing keys,
Cloudflare tunnel tokens, Docker TLS certificates, private OCI credentials, or
Postgres passwords.

#1113 accepts the package direction:

```text
core:
  SecretReference and delivery language

operations:
  RegisteredSecretProvider, authorization, metadata, audit correlation

control-plane-kit-secrets:
  encrypted durable custody and authenticated scoped resolution

interpreters:
  resolve SecretReference -> SecretValue at IO boundary only
```

The first-flight secrets provider should be a sibling repository/distribution:
`OpenJ92/control-plane-kit-secrets`. Its master key should be supplied outside
its own database, preferably by a mounted file, so the provider does not depend
on itself to boot. The full threat model is recorded in
`docs/design/0005-durable-secret-provider-threat-model.md`.

## #1172 Durable Secret-Use Authorization Evidence

#1172 added the operations-owned decision immediately before provider or
interpreter IO:

```text
AuthorizeSecretUse
  -> select active workspace handle
    -> select its exact active provider registration
      -> validate reference prefix and SecretUseIntent
        -> append AuthorizedSecretUse
          -> commit
            -> provider resolution in a later issue
```

`(workspace_id, correlation_id)` is the scoped idempotency identity. Exact
retries return the same immutable evidence, while changed actor, purpose,
reference, registration, timestamp, or workflow correlation fails explicitly.
An advisory transaction lock makes concurrent retries obey the same law.

The evidence records the exact provider and handle registration ids,
`SecretReference`, closed `SecretUseIntent`, trusted actor subject, and bounded
operation/session/run/activity/effect/probe correlation. It stores no endpoint,
provider credential, resolved value, plaintext, or ciphertext. Historical
evidence survives revocation, but every new authorization reselects active
workspace admission. A provider replacement therefore invalidates handles
pinned to the superseded registration until they are explicitly readmitted.

`AuthorizedSecretUse` is audit evidence, not a reusable credential and not
proof that provider IO occurred. The provider's resolve audit remains
authoritative for actual material access. #1173 must derive the actor and use
scope from trusted cpk-server context, expose only bounded metadata, and must
not make this evidence a caller-controlled authorization token.

## #1173 Secret-Provider Public Metadata Boundary

#1173 exposed operations-owned provider and pre-existing-handle metadata through
cpk-server without turning the process wrapper into secret custody:

```text
authenticated HTTP or MCP request
  -> TrustedCommandContext
    -> focused provider/reference register, read, or revoke policy
      -> SecretProviderRegistrationService / secret-provider read projection
        -> one Postgres UnitOfWork
```

HTTP and MCP-shaped adapters traverse the same operations services. Caller
payload actor and scope fields remain inert; durable provenance comes from the
trusted principal. Public projection is purpose-built and returns only bounded
provider/reference metadata, including opaque endpoint and credential
`SecretReference` identities. It never returns provider credentials, endpoint
values, plaintext, ciphertext, or authorized-use evidence. The internal
`secret-provider:use` scope remains separate from metadata registration, read,
and revocation.

The server composition pass exposed a useful immutable-dependency law. A new
cpk-server image cannot directly pin core/operations from one commit while its
interpreter dependency pins the same `control-plane-kit-core==0.1.0` name from
another direct URL. Pip correctly rejected the contradictory candidates before
tests ran. The fix was not a local resolver override:

```text
merge operations/core boundary
  -> align and validate interpreters core coordinate
    -> merge interpreters
      -> update server coordinate manifest
        -> regenerate every dependency surface
          -> rebuild and validate cpk-server
```

Merged evidence:

- control-plane-kit PR #1178:
  `ffdbea260fd5f2679ecf5371251f92556a42c300`;
- control-plane-kit-interpreters PR #42:
  `6c6cb100b5861a08618bc4195d7c857b5b76a0c7`;
- control-plane-kit-servers PR #53:
  `24f563c15bdeda8b85cbabcaebc01347a61af30f`.

Validation passed 221 operations tests, 91 interpreter tests, and 139
server-product tests. The server gate also rebuilt the cpk-server image,
exercised authenticated HTTP and MCP against a real Postgres dependency,
verified non-root execution, cleaned owned resources, and passed the Docker
residue audit. GitHub's independent server Docker job passed.

Because cpk-server process composition changed, its published OCI must be
rebuilt before the later published-live acceptance gate. #1173 deliberately
does not present its source-built image as published acceptance. #1174 now owns
the aggregate security, authorization, data-engineering, migration,
transaction, secret-leak, HTTP/MCP parity, and test-integrity closeout before
#1116 replaces process-local resolvers with admitted provider clients.

## #1174 Secret-Provider Admission Closeout

The aggregate review found one durability defect in the otherwise coherent
admission boundary. Serial retries were idempotent, but two simultaneous
first-time registrations for the same provider or pre-existing secret reference
could both observe absence. The losing transaction then exposed a raw Postgres
unique-constraint failure instead of converging on the admitted fact.

The stores now take a 64-bit transaction-scoped advisory lock before reading or
creating one semantic admission identity:

```text
secret-provider:{workspace_id}:{provider_id}
secret-reference:{workspace_id}:{secret_reference}
secret-use:{workspace_id}:{correlation_id}
```

The lock does not commit, resolve a secret, or extend the UnitOfWork across IO.
It only serializes competing decisions for the same workspace identity. New
Docker/Postgres integration tests hold the first transaction open, prove the
second transaction is waiting on the admission lock, release the first
transaction, and verify both callers converge on one durable row. Before the
fix, both tests failed with raw `UniqueViolation`; after the fix, all 223
operations tests pass.

The security and architecture review confirmed:

- operations stores provider and pre-existing-handle metadata only;
- endpoint and credential coordinates remain opaque references;
- plaintext, ciphertext, provider tokens, and resolved values are absent from
  operations schema, events, observations, read models, logs, and public
  responses;
- provider register, read, revoke, and use permissions remain independent;
- HTTP and MCP-shaped adapters derive actor and grants from the same trusted
  context and invoke the same operations services;
- stores never commit, application services own commit/rollback, and no
  provider, Docker, filesystem, Cloudflare, or HTTP call occurs inside these
  transactions;
- provider and handle history is additive and explicitly superseded or revoked;
- schema installation remains idempotent and non-destructive.

`control-plane-kit-secrets` already exposes the provider-side contract #1116
must consume:

```text
POST /v1/workspaces/{workspace_id}/secrets/{secret_id}/resolve

input:
  closed intent
  caller subject
  correlation id
  optional version id

provider audit:
  provider/workspace/secret/version
  intent/caller/correlation
  bounded outcome/code
```

#1116 must add an IO-boundary provider client/resolver that selects the exact
active `RegisteredSecretProvider` and `RegisteredSecretReference`, commits
`AuthorizedSecretUse`, then performs provider IO using the same correlation id.
Resolved material may flow only from the provider client into the consuming
interpreter or signer. It must never return through operations or enter durable
operations evidence.

Validation passed `git diff --check`, all 443 core tests, all 223 operations
tests, compile/import checks, and the full live-code root Docker gate with 1,232
tests. The full gate exercised the current package, Postgres integration,
architecture policies, runtime/interpreter contracts, server blocks, and
workflow scenarios; it did not use the frozen implementation as success
evidence.

Residual hardening remains explicit. Public command envelopes carry an
idempotency key while provider/reference semantic identities currently provide
the actual convergence rule; generalized command-ledger enforcement belongs to
the broader idempotency hardening track. The additive `CREATE TABLE IF NOT
EXISTS` installer is safe for this first schema, but versioned migration policy
remains broader data-engineering work. Finally, the cpk-server source changes
from #1173 still require immutable OCI publication before published-live
secrets acceptance; source-built validation is not that evidence.

## #1181 Explicit Secret-Use Intent

The #1116 dry run found that value-resolving delivery language named a
destination and `SecretReference`, but not the purpose for which the value could
be resolved. Leaving purpose implicit would force operations or an interpreter
to infer policy from an environment name, file path, product identity, or
runtime kind.

The closed delivery language now makes purpose explicit:

```text
SecretEnvironmentDelivery
  = environment_name x SecretReference x SecretUseIntent

SecretFileDelivery
  = target_path x SecretReference x SecretUseIntent x file policy
```

The intent is required in constructors and strict descriptors; there is no
compatibility default. `SecretReferenceEnvironmentDelivery` remains unchanged
because it exposes only an opaque reference and performs no resolution. Intent
also participates in deterministic delivery ordering.

The first production constructor migrated here is the generated Cloudflare
tunnel-token delivery, which now states
`SecretUseIntent.CLOUDFLARE_TUNNEL_TOKEN`. Core graph/product/handoff fixtures
state their exact purposes as well. This gives later #1183 authorization
composition a pure, unambiguous `(reference, intent)` inventory before any
provider or downstream IO occurs.

## #1183 Authorized Secret Resolution Grants

#1183 bridges durable operations authorization to interpreter IO without
turning operations into a secret resolver:

```text
committed activity/probe intent
  -> enumerate exact SecretReference x SecretUseIntent uses
    -> one short authorization UnitOfWork per use
      -> commit or replay AuthorizedSecretUse
        -> project reference-only SecretResolutionGrant
          -> interpreter/provider IO
```

`SecretResolutionGrant` pins the workspace, active provider and reference
registrations, opaque provider endpoint and bootstrap credential references,
exact use intent, trusted actor, retry-stable correlation, and bounded workflow
identities. It contains no plaintext or ciphertext and is not a reusable bearer
credential. An interpreter must match both the reference and intent before it
may ask a provider to resolve material.

Runtime, named-ingress, and gateway-probe signing now share one operations-owned
`SecretUseResolutionAuthorizer` protocol and one deterministic correlation
derivation. Runtime translation enumerates explicit environment/file delivery,
OCI pull credentials, Postgres verification passwords, and remote Docker TLS
material. Cloudflare ingress authorizes its admitted API-token reference before
provider IO. Gateway probing commits its durable attempt before authorizing the
signing key and performs zero gateway IO when authorization fails.

The authorization fingerprint deliberately excludes `requested_at`. Time
remains durable audit evidence, but an exact retry with the same correlation and
workflow identities replays the original authorization instead of conflicting
only because wall-clock time advanced. Provider or reference revocation still
fails closed because active admission is reselected before replay evidence is
returned.

The transaction boundary remains:

```text
short intent transaction -> commit
short secret-use authorization transaction -> commit
provider/interpreter/network IO
short result transaction -> commit
```

Operations never imports a provider HTTP client and never receives
`SecretValue`. The later #1185 interpreter pass must consume these grants
through the bounded provider client from #1182 and remove silent production use
of process-local reference resolvers.

## #1184 Generated Cloudflare Token Custody

#1184 removes the last raw generated Cloudflare tunnel token from the operations
boundary. An admitted Cloudflare ingress authority now explicitly selects both
the workspace secret-provider registration and the allowed generated-reference
prefix. Provider choice is never inferred from product identity, provider count,
or a hard-coded `secret://generated` convention.

The allocation path is now:

```text
operations selects admitted provider and deterministic SecretReference
  -> operations emits reference-only SecretCustodyGrant
    -> Cloudflare interpreter creates tunnel, configuration, and DNS
      -> Cloudflare interpreter obtains the generated tunnel token
        -> concrete SecretCustodian writes it directly to provider custody
          -> interpreter returns SecretCustodyReceipt plus exact Cloudflare IDs
            -> operations validates the receipt
              -> one UnitOfWork records reference admission and ownership evidence
```

`SecretCustodyGrant` pins workspace, provider registration, opaque provider
endpoint and credential references, exact generated reference, intent, trusted
actor, correlation, and workflow identities. `SecretCustodyReceipt` returns only
provider/reference/version identity and status. Neither type contains plaintext
or ciphertext. The former in-memory generated-secret recorder and raw-token
allocation result have been removed.

The external-effect transaction law remains intact:

```text
short transactions select and authorize reference-only custody
  -> commit
    -> Cloudflare and secret-provider IO
      -> one short transaction atomically folds reference and ownership evidence
```

The Cloudflare interpreter treats allocation as a typed in-activity mini-saga:

```text
create tunnel
  -> configure tunnel
    -> upsert DNS
      -> fetch tunnel token
        -> write provider custody
```

Failure compensates completed steps in reverse order. Any attempted provider
write triggers exact revocation, including ambiguous writes that return no
receipt; an already-absent reference is an idempotent clean result. A mismatched
receipt is rejected, compensated, and cannot admit a secret reference or
Cloudflare resource. Compensation never broad-lists or deletes unrelated
Cloudflare resources.

This does not yet make provider substeps restart-durable. If compensation itself
is uncertain, the activity reports uncertainty without claiming clean absence.
#1095 owns durable staged external-resource evidence, reverse compensation,
orphan persistence, and reconciliation. #1092 owns attempts, leases, fencing,
and interrupted-effect recovery. #1096 may later compose the generic saga
language into the executable DeploymentProgram only after those guarantees
exist.

Validation passed all 447 core tests, 229 operations tests, and 105 interpreter
tests, including real Postgres rollback, exact receipt matching, provider-write
failure compensation, idempotent revocation, package-boundary checks, compile,
and import validation. No live Cloudflare resources were mutated in #1184.

## #1185 Provider-Backed Secret Consumers

#1185 completed the interpreter-side migration from reference-only grants to
provider-backed, immediate secret use:

```text
operations commits exact SecretResolutionGrant
  -> interpreter validates workspace, reference, intent, correlation, and use
    -> AuthorizedSecretResolver contacts the admitted provider
      -> plaintext enters only the immediate SDK/client/signer call
        -> only bounded, secret-free evidence returns to operations
```

The migration landed as four independently reviewed changes:

- #1191, commit `f488b3f4a9923fce4ed88dd989a73a956f755579`,
  established the provider-backed authorized resolver;
- #1192, commit `1fffde9df0773a2d46ea128b6832c0e5c97ea804`,
  migrated generic environment/file delivery, Docker TLS, private OCI pulls,
  Postgres verification passwords, and application control tokens;
- #1193, commit `e557f639a73a093fa815adde670b79c8a0a1ff65`,
  migrated Cloudflare API authentication while keeping generated tunnel-token
  custody separate;
- #1194, commit `b0611cb5beb2db47fdbcc0b1f5a9271c3715d9bf`,
  migrated gateway probe signing and preserved all pre-signing substitution
  checks.

Exact authorization is now shared across these consumer families. A wrong
workspace, provider, reference, intent, correlation, or workflow identity fails
before Docker, Postgres, Cloudflare, gateway, or target IO. When an authorized
resolver is configured, no compatibility resolver is consulted after denial or
provider failure.

The remaining legacy resolver occurrences have explicit classifications:

- core `SecretResolver`, `require_resolved_secret`, and
  `LocalDevelopmentSecretResolver` are compatibility protocol/helper and
  explicitly named development-fixture surfaces;
- interpreter compatibility resolver parameters remain reachable only when no
  authorized production resolver is supplied;
- generated-secret records in operations are custody/reference metadata, never
  plaintext or ciphertext;
- probe endpoint-secret resolution is not production-composed and remains a
  future authorization/composition decision;
- local maps, Docker-config discovery, generated-memory custody, composite
  first-success selection, and grant-discarding wrappers in cpk-server remain
  production composition liabilities owned by #1186.

#1186 must compose deterministic provider endpoint and bootstrap-credential
registries, pass exact committed grants through Docker, Cloudflare, and gateway
wrappers, and remove silent production fallback to local maps, Docker config,
generated-memory custody, or first-provider selection. Development fixtures may
remain only behind explicit development configuration. cpk-server must remain a
composition boundary: it may wire clients and resolvers, but it must not store
secret values or own secret-use authorization policy.

Validation passed 123 interpreter tests, 229 operations tests, and the complete
1,232-test coordination Docker suite, including architecture, ownership,
transaction, and test-integrity checks. Focused live Docker materialization
also proved a real read-only secret-file mount with bounded digest evidence and
self-cleanup. No Cloudflare resource was mutated and no cpk-server source-live
claim is made before #1186/#1187.

## #1186 Production Secret-Provider Composition

#1186 replaces cpk-server's collection of independent secret mechanisms with
one explicit composition root:

```text
cpk-server bootstrap
  -> bounded material-provider routes and protected bootstrap files
    -> one SecretProviderBootstrapRegistry
      -> one ControlPlaneKitSecretsResolver
      -> one ControlPlaneKitSecretsCustodian
        -> operations commits exact grants
          -> Docker, Cloudflare, and gateway consumers perform immediate IO
```

`SecretUseAuthorizationService` remains operations-owned. cpk-server constructs
it once and passes it to runtime dispatch, named-ingress realization, and
gateway probing. The adapters now preserve `SecretResolutionGrant` and
`SecretCustodyGrant` instead of reducing them to bare references. cpk-server
does not receive provider plaintext or ciphertext.

The production path no longer composes:

- process-local generated-secret custody;
- first-success composite resolution;
- Docker-config credential discovery;
- reference-only gateway signing;
- a second compatibility resolver entrance.

The only process-local value map is explicitly selected by
`CPK_PRODUCT_MATERIAL_RESOLVER=local-development`. Production Docker and
Docker-plus-Cloudflare descriptor variants select `provider`; no production
descriptor embeds provider routes, protected-file paths, secret references, or
secret values.

Core's public-environment guard exposed an important naming law during the
implementation. A mode selector named `CPK_PRODUCT_SECRET_RESOLVER` was rejected
because secret-shaped environment names belong to `SecretEnvironmentDelivery`.
The selector carries no secret, so the final closed bootstrap vocabulary is:

```text
CPK_PRODUCT_MATERIAL_RESOLVER
CPK_MATERIAL_PROVIDER_ROUTES_JSON
CPK_MATERIAL_PROVIDER_BOOTSTRAP_FILES_JSON
```

The route map contains opaque endpoint-reference to bounded base-URL entries.
The bootstrap-file map contains credential-reference to protected mounted-file
paths. Actual credential contents remain outside both maps. These names keep
non-secret bootstrap routing eligible for future graph instance configuration
without weakening secret-delivery validation.

Readiness exposes only bounded composition status:
`material_provider=disabled|configured|development-fixture`. It does not expose
provider URLs, references, file paths, credentials, tokens, or store endpoints.
Bootstrap parsing rejects duplicate keys, oversized maps, missing pair members,
inline values in provider mode, and all former Docker credential environment
fallbacks.

Focused tests prove deterministic grant routing when multiple provider routes
and bootstrap files exist: the grant's endpoint and credential references select
exactly one provider. The complete current server-products suite passed,
including 68 cpk-server tests and all product suites. A freshly rebuilt
cpk-server source image passed its HTTP/MCP smoke, and the Docker residue audit
was clean. Frozen/reference suites are no longer release gates after extraction;
current package, architecture, security, coordinate, image, and live acceptance
are authoritative.

#1187 must replace source-live harness fixtures with a real
`control-plane-kit-secrets` process, exercise the public cpk-server workflow,
prove restart and denial behavior, and label the evidence source-built rather
than published-image acceptance. cpk-server OCI republication remains required
after that source-live gate and belongs to the later publication topology.

## #1187 Source-Live Durable Provider Acceptance

#1187 proves the production provider composition through real source-built
processes:

```text
operator -> cpk-server HTTP/MCP-shaped workflow
  -> Postgres operations admission and use truth
    -> committed SecretResolutionGrant
      -> control-plane-kit-secrets HTTP process
        -> provider audit with the same correlation
          -> DockerRuntimeInterpreter
            -> Postgres receives the resolved password in memory
              -> semantic SELECT 1 succeeds
```

The controller registers provider and reference metadata through cpk-server,
reads the bounded metadata through MCP, and then restarts the provider before
the first use. The provider retains encrypted custody in SQLite while operations
retains only provider/reference/use metadata. A successful provider audit
correlation must be present in the committed
`cpk_secret_use_authorizations` rows before the live proof is accepted.

The denial matrix covers missing `secret-provider:use`, wrong workspace,
unsupported intent, revoked provider, revoked reference, missing secret, wrong
provider credential, and unavailable provider. Cases rejected by operations
prove that provider audit counts do not change. Every denied case proves that no
Postgres workload container was created.

The source-live harness contains no process-local secret-value map, development
resolver, or obsolete Docker-config resolver. Bootstrap provider credentials
are mounted as read-only files and selected through opaque references. Generated
test values are scanned against cpk-server logs, provider logs, the operations
database dump, and the provider database; no plaintext was found. Activity and
provider/reference readback remain bounded and secret-free.

Two integration details were exposed by the live run:

1. operations correlation evidence uses the current `use_intent` schema column;
   acceptance must query current durable truth rather than preserve stale
   harness vocabulary;
2. cpk-server and the controller must detach from the realized runtime network
   before `Deploy(current, empty)` can remove that network. This is a harness
   reachability concern, not a reason to bypass graph-driven teardown.

The complete current server-products suite passed, including its authenticated
image smoke and Docker residue audit. The focused cpk-server provider/bootstrap
suite passed 41 tests. The secrets provider suite passed 27 tests, including
real-process restart, rotation, revocation, tamper resistance, authorization,
and leak checks. The source-live provider scenario passed its success, denial,
teardown, and residue gates.

This evidence is intentionally source-built. It does not claim immutable
published OCI acceptance. #1117 retains cpk-server publication, published-digest
acceptance, stronger rotation/revocation/concurrency coverage, generated
Cloudflare-token custody, private OCI credentials, Docker TLS material, and
broader multi-consumer validation.

## #1208 Explicit Verification Retry Cadence

The #1202 restart dry run separated three facts that had been conflated:
container lifecycle, process bootstrap, and semantic readiness. Docker may
report a replacement container running before its application is listening;
neither Docker `StartedAt` nor a successful `start()` call proves product
readiness.

Core verification policy now makes retry cadence explicit:

```text
VerificationPolicy(
    timeout_seconds=...,
    interval_seconds=...,
    maximum_attempts=...,
    maximum_evidence_bytes=...,
)
```

The interval name follows the existing provider-neutral `TimeoutPolicy`
vocabulary. It applies only between completed failed attempts. Core remains
pure and performs no sleeping or clock access.

Legacy three-field verification policy descriptors remain decodable with the
historical one-second cadence. Canonical encoding always emits the interval,
so newly generated product descriptors and catalogue digests make the timing
contract explicit. A separate startup-grace field was rejected for now because
the live evidence requires bounded cadence, not a second pre-attempt delay.

## #1202 Durable Secret Lifecycle Acceptance

The #1208/#1209 prerequisite closed the restart timing gap without treating
Docker lifecycle state as application readiness:

```text
stop old container process
  -> start replacement container lifecycle
    -> attempt cpk-server semantic readiness immediately
      -> wait VerificationPolicy.interval_seconds between failures
        -> accept only /health/ready success
```

Concrete HTTP, Redis, Postgres, and Docker HTTP verification now share one
interpreter-layer attempt iterator. There is no delay before attempt one and no
delay after success or exhaustion. Current server descriptors declare cadence
explicitly; cpk-server uses ten attempts at a two-second interval, while
existing product behavior retains a one-second interval. The hosted controller
consumes the same descriptor policy. Its generic Docker restart helper proves
only that the old process stopped and a new container lifecycle began.

With that prerequisite merged, the source-built #1202 acceptance proved:

- cpk-server and the durable secrets provider can restart after provider and
  reference admission, then continue through the public deployment workflow;
- a Postgres effect resolved version A, rotation created version B without
  erasing A, exact correlation replay remained pinned to A, and a new effect
  resolved B;
- revocation before unresolved use failed closed before any new Docker
  container, network, or volume mutation;
- concurrent resolve/revoke produced one provider transaction order with
  truthful bounded audit evidence;
- three workspaces resolved distinct references and versions concurrently
  without cross-delivery;
- operations authorization correlations, provider audit/version metadata,
  activity outcomes, and public readback agreed;
- teardown removed only owned resources and the final Docker residue audit was
  clean.

The provider now receives production-style credentials through an owner-only
mounted file rather than development JSON environment configuration. Generated
test values were scanned against cpk-server logs, provider logs, the operations
database dump, and provider storage. No plaintext was found. Operations
continues to retain only provider/reference/use metadata; encrypted custody and
version history remain provider-owned; plaintext exists only at provider and
interpreter IO boundaries.

This remains source-built acceptance. It does not claim immutable published OCI
acceptance. #1203 next proves generated Cloudflare tunnel-token custody through
the same admitted provider path.
