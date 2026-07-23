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
