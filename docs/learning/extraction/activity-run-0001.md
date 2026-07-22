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
