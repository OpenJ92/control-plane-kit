# Roadmap 0008: Activity Execution And Runtime Mutation

Status: Implemented through Gate F; post-closeout Gate G planned in #402; awaiting operator review in draft PR #228
Depends on: Roadmap 0005, Roadmap 0006, Roadmap 0007

## Motivation

Planning explains what should happen. Execution changes the world.

This roadmap is where `control-plane-kit` becomes operationally dangerous, so it
must be built under the data-engineering, security, and approval laws already
defined.

The key distinction:

```text
ActivityPlan
  describes approved intended effects

ActivityRun
  records one execution attempt

ActivityEvent
  records bounded observations during execution

ObservedState
  records what the system saw afterward
```

Execution must not be a hidden side effect of saving a graph. It must be an
approved, claimed, event-emitting workflow.

Execution has two different data-engineering regimes:

```text
ActivityRun / ActivityEvent / ObservedState writes
  -> ordinary Postgres unit-of-work transactions owned by execution services

Docker, block control routes, cloud APIs, filesystem state, and secrets
  -> saga/external-effect steps with durable progress events
```

Sagas do not replace the Postgres unit of work. They coordinate effects that no
single database transaction can protect.

## Goal

Implement the first executable activity pipeline.

This roadmap should provide:

- execution request/claim mechanics,
- activity run lifecycle,
- saga/compensation grammar,
- runtime executor interface,
- dependency-aware multi-node activity planning,
- block control client interface,
- Docker-backed safe activities,
- explicit host-port publication for intentionally exposed local endpoints,
- real health/readiness observation rather than optimistic started-is-healthy
  state,
- control-route-backed safe activities,
- activity events,
- observed-state updates,
- pause/failure/resume shape,
- and at least one live local smoke example.

## Non-Goals

- Do not implement every runtime provider.
- Do not implement full cloud execution.
- Do not promise perfect rollback.
- Do not execute unapproved plans.
- Do not run destructive activities without stronger approval.
- Do not require a graph database.
- Do not hide partial failure.

## Canonical Issue Topology

The source-level dry run after Roadmap 0007 replaced the initial equal-sized
brainstorm with the following ordered topology:

```text
#211 typed execution values and codecs
  -> #212 Postgres execution schema and stores
    -> #213 claim/event/transition concurrency hardening
      -> #214 execution request and approval admission
        -> #215 claimed ActivityRun lifecycle
          -> #239 event-authoritative run settlement
            -> #242 fail-closed expired-claim semantics
              -> #243 guarded operation-session transitions

#211 + #243 -> #216 generic saga adaptation
          -> #217 resumable dependency scheduler
            -> #218 runtime/control effect protocols

#218 -> #257 AST architecture/integrity hardening
  -> #260 reusable AST source and policy model
  -> #261 package dependency and transport isolation
  -> #258 generated Python server template validation
  -> #264 UnitOfWork commit and environment access ownership
  -> #262 test-integrity declaration audit
  -> #259 read-only FastAPI route audit
  -> #263 integrated review

#215 + #218 + #263 -> #244 evidence-backed current-graph advancement
  -> #219 durable execution coordinator with fake effects
#218 -> #245 failure-explicit contract/resource mutation
  -> #280 versioned staged contract mutation values
    -> #281 prepare derived resources and publish one projection
      -> #282 cleanup uncertainty, idempotency, and concurrency

#219 + #282 -> #295 graph-pinned effect materialization

#295 -> #283 control-address and transport policy
  -> #284 bounded block-control effect interpreter
    -> #285 protocol and adversarial live hardening

#295 -> #286 narrow Docker operation mapping
  -> #287 Docker ownership and idempotent inspection
    -> #288 retained-resource and deletion policy
      -> #289 explicit host publication and live compatibility

#285 + #289 + #295 -> #290 truthful probe intents and evidence
  -> #291 coordinator-backed bounded probes
    -> #292 observation history, freshness, and graph-truth hardening
      -> #293 Gate D live Docker Hello/router smoke
        -> #294 mandatory Gate D milestone review

#294 operator approval -> #316 Gate E milestone
  -> #223 recovery and compensation phase
    -> #317 closed recovery decisions and authorization
      -> #318 pinned typed compensation programs
        -> #319 durable recovery events and Postgres projections
          -> #328 strict recovery schema and journal hardening
            -> #320 transactional recovery command services
              -> #329 decision concurrency and expired-claim hardening
                -> #321 durable compensation coordinator
                  -> #330 compensation crash-window hardening
                    -> #322 operator recovery projections
                      -> #323 integrated recovery hardening
  -> #224 execution acceptance phase
    -> #324 execution scenario expectation algebra
      -> #325 Postgres coordinator scenario runner
        -> #331 scenario isolation and canonical-service hardening
          -> #326 failure, uncertainty, and compensation corpus
            -> #327 mandatory Gate E milestone review

#323 -> #324

#327 operator approval -> #225 Gate F milestone
  -> #353 closed deployment-program values
    -> #354 Plan and Approve stages
      -> #355 Admit and Claim stages
        -> #356 Execute stage
          -> #357 Advance stage
            -> #358 parameterized Deploy composition
              -> #359 four-transition laws
                -> #360 AST application boundaries
                  -> #361 scenario runner migration
                    -> #362 live Docker router switch through Deploy
                      -> #226 hardening milestone
                        -> #363 live boundary matrix
                          -> #364 cross-module review
                            -> #379 transition-owned resumption hardening
                              -> #365 public API and guide
                                -> #366 complete acceptance corpus
                                  -> #367 roadmap closeout
                                    -> #385 durable DeploymentProgram lifecycle
                                      -> #227 Roadmap 0009 handoff

#227 -> #246 guarded instance lifecycle evidence

#227 -> #387 generated Hello topology stress vertical
  -> #388 parameterized Hello dependencies
    -> #389 generated valid and invalid graph corpus
      -> #390 Postgres-backed DeploymentProgram coverage
        -> #396 protocol-aware readiness prerequisite
          -> #398 startup and runtime edge-binding prerequisite
            -> #391 live generated graph proof
              -> #392 generated topology hardening

#392 -> #402 Gate G heterogeneous topology acceptance
  -> #403 package-owned capability-contract audit
    |-> #451 -> #452 -> #453 -> #443 connection-protocol foundation
    |-> #454 -> #455 -> #456 -> #444 configuration-artifact foundation

#443 -> #457 closed package-owned verification contract
#444 + #436 -> #458 secret bootstrap and runtime secret-file material

#403
    |-> #404 + #413 + #414 + #415 + #420..#425 + #445 + #457
    |     -> #437 HTTP policy and resilience acceptance
    |-> #426 + #428 + (#443 + #444 -> #427) + #457
    |     -> #438 service and application-infrastructure acceptance
    `-> #429 + #436
          + (#443 + #426 + #444 -> #430)
          + (#444 + #436 + #458 -> #431)
          + (#443 + #436 + #458 -> #432 + #434 + #435)
          + (#443 + #444 -> #446 -> #447 + #448 + #449 -> #433)
          + #457
          -> #439 protocol and data-product acceptance

#437 + #439 -> #440 compositional API-gateway recipe
#437 + #438 + #439 -> #441 compositional service-edge recipe

#437 + #438 + #439 + #440 + #441
      -> #405 heterogeneous scenarios and invalidities
        -> #406 ActivityPlan AST proofs
          -> #407 Postgres-backed DeploymentProgram proof
            -> #408 static live Docker topology
              -> #409 authenticated router mutation
                -> #410 hardening
                  -> #411 Gate G closeout and CPI handoff

#246 + #411 -> Roadmap 0009 CPI implementation
```

Parent issue: #210.

The ordering is intentional. Docker and HTTP mutation are not foundations.
They are concrete interpretations added only after durable admission, claims,
run lifecycle, scheduling, and effect protocols exist.

The completed Gate B.1 AST hardening vertical makes selected static laws executable
before Gate C. It does not replace behavioral, runtime, authorization, or
transaction tests. Issue #263 confirmed the checks are precise and the complete
suite remains green, so #244 may begin only after the required operator gate.

The mutation-integrity amendment is also intentionally staged. Issues #242 and
#243 repair dangerous primitives whose semantics are already knowable, so they
must complete before Gate B. Issue #244 depends on the pure effect language and
must complete before the coordinator can advance current topology. Issue #245
depends on typed effect outcomes and must complete before live control or Docker
mutation. Issue #246 belongs to the Roadmap 0009 CPI lifecycle handoff rather
than introducing a CPI-specialized execution path here.

Gate D adds one provider-neutral boundary that the earlier outline omitted.
Issue #295 materializes immutable effect input from the exact desired graph
pinned by an approved plan. Concrete HTTP, Docker, and health interpreters
consume that value; they do not query Postgres, select a newer graph, or invent
provider-specific topology lookup. Issues #293 and #294 add a visible adapter
smoke and mandatory review stop without replacing the full Gate F acceptance
scenario in #225.

Gate E deliberately extends the canonical `ActivityPlan`, `ActivityEvent`,
`ActivityRun`, saga, UnitOfWork, and coordinator models. It does not add a
recovery journal, compensation store, mutable resume cursor, or second run
state machine. Four focused hardening issues isolate proof obligations that
would otherwise be hidden inside broad implementation issues: strict closed
schema validation (#328), one-winner recovery decisions and expired claims (#329),
compensation crash windows (#330), and scenario-runner isolation (#331).

Gate E is implemented and has passed its mandatory #327 milestone review. The
review found the canonical services can be composed as `Deploy = Plan ->
Approve -> Admit -> Claim -> Execute -> Advance`, with explicit durable
suspensions for approval and recovery. The callable application composition and
live proof remain Gate F work; Gate E did not add a facade or bypass the
canonical services.

Gate F is implemented and has passed its complete acceptance review. It added
one public higher-order deployment program rather than another workflow or
executor model:

```text
Deploy(current, desired)
  = Plan
      -> suspend for Approve
        -> Admit
          -> Claim
            -> Execute
              -> suspend for Recovery when required
                -> Advance
```

The graph pair is the program parameter and the complete transition language:

```text
initial deployment = Deploy(EmptyGraph, desired)
update             = Deploy(current, desired)
teardown           = Deploy(current, EmptyGraph)
no-op              = Deploy(graph, graph)
```

`Deploy` composes existing durable command services. It does not combine them
into one long transaction, bypass approval or recovery, dispatch effects
directly, or introduce a second journal. Every suspension is bound to the same
typed graph pair, and each resumed command retains the authorization,
transaction, concurrency, and pinned-graph checks owned by its canonical
service.

Post-closeout issue #385 separates object lifetime from workflow lifetime:

```text
DeploymentProgram
  = long-lived capabilities

Deploy
  = DeploymentProgram specialized to current x desired

StoredDeployment(plan_id)
  = ephemeral handle that reloads durable truth per command
```

This makes hours- or days-later approval and execution ordinary HTTP request
boundaries without retaining a Python object as state. Reconstruction occurs
inside one read-only Postgres UnitOfWork and then delegates to the same approval,
admission, lifecycle, coordinator, and advancement services.

The common law is:

```text
mutation is acceptable for a current projection
iff authoritative history remains append-only
and the projection can be reconstructed from it
```

Post-closeout issues #387 through #392 extend acceptance from one fixed router
switch to generated bounded Hello trees with paired HTTP and Postgres
requirements. They prove protocol-aware readiness, startup environment binding,
real edge behavior, invalid-graph rejection, canonical execution, and owned
resource cleanup without changing the generic deployment language.

Gate G issue #402 deliberately extends that evidence to a typed server
catalogue and heterogeneous package-owned blocks before CPI packaging. The
mandatory mixed HTTP spine remains:

```text
inbound traffic logger
  -> rate limiter
    -> circuit breaker
      -> retry proxy
        -> weighted load balancer
          -> managed routing paths
            -> outbound traffic logger / multiplexer
              -> Hello applications + Postgres dependencies + request observer
```

Gate G is an acceptance composition, not a second execution model. It first
reconciles advertised capabilities with live server behavior. It then develops
three parallel tracks: HTTP policy/resilience, service/application
infrastructure, and protocol/data-product integrations. Each track converges
through its own acceptance issue before reusable API-gateway and service-edge
recipes expand into ordinary graph data. The resulting corpus proceeds through
validation, ActivityPlan AST dependencies, Postgres-backed `DeploymentProgram`,
real Docker execution, authenticated route mutation, and canonical teardown.

Every product needs closed descriptors, socket validation, capability truth,
invalidity tests, and ownership/retention/security laws. Representative products
from each family run live; Gate G does not require every vendor implementation
or every permutation to execute in one laptop-sized graph.

Roadmap 0009 may begin only after the independent guarded instance-lifecycle
prerequisite #246 and Gate G closeout #411 converge. Gate G must hand the mixed
valid/invalid corpus to CPI public API acceptance rather than creating a
CPI-specific planner, executor, or live fixture.

The circuit breaker, retry proxy, and traffic logger remain separate
package-owned blocks. Their composition order is topology with observable
consequences, not an implementation detail hidden inside a combined middleware
class. The traffic logger is one transparent forwarding server that works on an
incoming or outgoing edge according to graph position. It is distinct from the
terminal request observer used as a multiplexer copy target.

These scaffolds retain strict safety boundaries: the retry proxy never retries
non-idempotent work without an explicit idempotency contract; circuit state is
bounded and its control routes are authenticated; traffic evidence excludes
bodies, credentials, cookies, arbitrary headers, query strings, and raw
unbounded paths by default.

The expanded catalogue adds timeout/deadline, bulkhead, test-only fault
injection, bounded cache, policy gateway, idempotency gateway, service discovery,
OpenTelemetry Collector, durable webhook delivery, TCP switching, CoreDNS,
PgBouncer, Redis-compatible cache, typed broker products, MinIO/S3-compatible
storage, SMTP relay, and secrets-provider contracts. CPK implements small
teaching servers only where that is the declared product. DNS, pooling,
telemetry, brokers, object storage, and secret management use mature product
integrations rather than package-local replacements.

Frequently repeated arrangements are transparent recipes:

```text
Recipe -> tuple[DeployBlock, ...] x tuple[SocketConnection, ...]
```

The API-gateway and service-edge recipes introduce no hidden runtime, graph, or
execution language. Their expanded blocks remain inspectable and configurable.

A pre-implementation dry run found two necessary foundations. The existing
closed protocol language contains only HTTP, TCP, and Postgres; #443 must add
typed product connection protocols without degrading socket compatibility to
raw TCP. The Docker implementation can mount retained data volumes but cannot
yet materialize deterministic read-only generated configuration; #444 adds
immutable configuration artifacts rather than hiding CoreDNS, Collector,
PgBouncer, or broker configuration in shell commands or data mounts.

The same dry run added #445, a bounded authenticated load-generator
`ApplicationBlock`. It is not an inline proxy and accepts no arbitrary target
URL. Its sole target is graph-wired through an HTTP requirement socket, while
an authenticated control provider starts and cancels runs with hard limits on
request count, concurrency, rate, and duration. Gate G uses it to prove rate
limiting, balancing, timeout, and cancellation behavior without adding a
general denial-of-service tool.

Message brokers also require a product-family decomposition. #446 defines only
the shared typed grammar that is honest across products; #447, #448, and #449
retain exact NATS, RabbitMQ, and Kafka identities. Parent #433 converges those
integrations before protocol/data acceptance #439.

A second integration dry run conditioned on #443 and #444 decomposed those
foundations into executable child verticals. Protocol work is ordered as:

```text
#451 transport x application-protocol product
  -> #452 propagation through graph, durable, and read languages
    -> #453 transport-aware Docker endpoints, publications, and probes
      -> #443 complete
```

This avoids a flat enum that cannot represent DNS over TCP and UDP and prevents
Docker's current implicit TCP publication from becoming an accidental law.
Configuration work is ordered as:

```text
#454 immutable artifact algebra and descriptors
  -> #455 owned read-only Docker realization
    -> #456 strict typed Jinja2 rendering and security hardening
      -> #444 complete
```

Conditioning on both foundations exposed two additional convergence contracts.
#457 supplies a closed package-owned verification language for bounded HTTP,
DNS, Postgres, Redis, broker, object-storage, and SMTP checks. Targets always
come from graph sockets; it is not an arbitrary command or test runner. #458
separates pre-bootstrapped control-plane secret authority from the graph it
unlocks and adds runtime-only secret-file material for products that cannot use
environment secrets.

After these additions, no integration requires a new graph, planner, activity,
coordinator, or Docker lifecycle model. Package servers reuse current control
and server patterns. Mature products become exact typed specs over the shared
Docker implementation, artifact, secret, probe, and verification boundaries.

Before PgBouncer, the package-consolidation vertical establishes an acyclic
ownership DAG and representative physical boundaries:

```text
core         <- domains
core/domains <- operations
core/...     <- interpreters <- products <- entrypoints
```

The arrows denote permitted dependency direction, not mandatory equal-depth
rings. Webhook delivery proves a substantial five-part product; the auth gateway
proves declaration/process separation; CoreDNS proves a product-specific domain
projection. Closeout removes cycle allowances, keeps the package root a
lightweight pure facade, records deferred non-duplicate relocations honestly,
and requires all new products beginning with PgBouncer to use the canonical
`products.servers` exterior.

Request or response body transformation is deliberately outside the
package-owned Gate G server catalog. XML/JSON conversion, field mapping, schema
evolution, defaults, and semantic validation are application behavior. A user
may provide a transformer as an ordinary `ApplicationBlock` with HTTP provider
and requirement sockets. CPK compiles, deploys, wires, observes, and optionally
executes its declared verification contract without understanding or owning the
mapping semantics. Issue #417 and PR #418 record the rejected generic-server
alternative.

## Initial Issue Brainstorm (Superseded By Canonical Topology)

The following list is retained as design motivation. Use the canonical issue
numbers above for execution order.

1. Review and adapt saga grammar.
   - Compare `pottery-factory-saga` with control-plane execution needs.
   - Add generic saga program/activity/interpreter modules.
   - Preserve the law: Saga describes work; Saga does not decide domain truth.

2. Add activity execution request model.
   - Durable request before effects.
   - Idempotency key.
   - Approved plan reference.
   - Actor identity.
   - The request must be written in the same unit of work as the command that
     asks for execution.

3. Add activity run lifecycle.
   - `queued`, `claimed`, `running`, `paused`, `succeeded`, `failed`,
     `compensating`, `compensated`, `partially_failed`, `cancelled`.
   - Guarded status transitions.
   - Prevent duplicate concurrent runs for the same plan unless explicitly
     retrying.
   - Guard status transitions with Postgres transactions and row-level locking
     or equivalent compare-and-set semantics.

4. Add executor interface.
   - Claim approved plan.
   - Compile plan to executable activity sequence/saga.
   - Execute activities.
   - Emit events.
   - Update observed state.
   - Open small explicit transactions for claim/run/event state changes.
   - Keep external effects outside those transactions, with durable events on
     both sides of each effect.

5. Add activity event writer.
   - Step started.
   - Step succeeded.
   - Step failed.
   - Compensation started.
   - Compensation succeeded/failed.
   - Run paused/resumed.
   - Run completed.
   - Each event append must participate in an explicit transaction owned by the
     execution service.

6. Add runtime executor capabilities.
   - Start node.
   - Stop node.
   - Restart node.
   - Wait for health using bounded timeout, interval, and failure policy.
   - Distinguish process/container start from application readiness.
   - Drain node as advisory first.
   - Keep provider interface narrow.

7. Add dependency-aware multi-node activity planning.
   - Distinguish communication edges from explicit startup/readiness
     dependencies.
   - Compile provider-before-consumer ordering where blocks declare it.
   - Wait for required provider readiness before starting dependent consumers.
   - Detect dependency cycles and require an explicit simultaneous/deferred
     policy rather than choosing an arbitrary order.
   - Plan reverse-order compensation or stop where safe.
   - Keep the scheduler generic; it must not know about CPI, Auth, or Postgres
     domain names.

8. Add block control client capabilities.
   - Read block capabilities.
   - Register target.
   - Switch target.
   - Patch runtime variable where supported.
   - Query health/status.
   - Respect control-route auth.

9. Add Docker local runtime executor.
   - Safe start/stop for local demo nodes.
   - Publish declared host ports only when the topology/runtime policy requests
     them; do not expose every provider socket by default.
   - Preserve container-private provider addresses separately from
     host-observed addresses.
   - Probe declared health paths and report unknown/unhealthy instead of marking
     every started container healthy.
   - Retained Postgres handling where needed.
   - No accidental deletion of retained data.

10. Add control-route-backed router switch example.
   - Start candidate service.
   - Wait for health.
   - Register target.
   - Switch router.
   - Record events.

11. Add failure and compensation behavior.
    - Failed step records partial state.
    - Completed compensatable steps run compensation in reverse completion
      order.
    - Compensation failures are recorded.

12. Add observed-state updates.
    - Record runtime evidence after execution.
    - Mark stale/unknown where appropriate.
    - Do not silently rewrite desired topology from observed state.

13. Add live local smoke example.
    - Use package-provided blocks.
    - Demonstrate a safe switch or replacement.
   - Record activity events.
   - Exercise an intentionally host-published endpoint with real HTTP health
     evidence so Roadmap 0009 can reuse the same mechanism for CPI.

## Target Execution Flow

```text
approved ActivityPlan
  -> execution request
  -> executor claims request
  -> ActivityRun opened
  -> activities interpreted as saga where useful
  -> runtime/control-route calls
  -> ActivityEvent*
  -> ObservedState update
  -> ActivityRun closed
```

## Saga Shape

The saga module should remain generic:

```python
program = then(
    step(start_api_v2),
    step(wait_for_api_v2),
    step(register_router_target),
    step(switch_router_target),
)

result = await interpret(program)
```

Activity-specific compensation is supplied by adapters:

```text
start api-v2
  compensation: stop api-v2 if still unused

switch router to api-v2
  compensation: switch router back to prior target if healthy
```

Some activities may be non-compensatable. They must say so.

## Implementation Notes

- Follow ADR 0008 strictly.
- Import desired-state graph concepts from `control_plane_kit.topology` and
  activity-plan concepts from `control_plane_kit.planning`.
- Do not place execution, runtime mutation, workflow persistence, or store code
  inside either pure algebra package.
- Store-local execution state uses normal Postgres unit-of-work transactions.
- Saga steps only wrap external effects that cannot be made ACID by Postgres.
- Execution claims need locking or guarded status transitions.
- Effects happen after durable execution request.
- Every external effect emits an event.
- Event payloads are bounded and redacted.
- Runtime providers and block clients are capability boundaries.
- Destructive activities must require stronger approval.
- Local package server blocks are teaching/demo blocks unless explicitly marked
  production-grade.

## Validation

The reusable planning corpus in `examples/scenarios/` is the acceptance basis
for execution. Roadmap 0008 must preserve each scenario's typed operations,
dependency order, risk, and readiness while extending it with expected runtime
evidence:

```text
PlanningScenario
  + ActivityRun expectation
  + ActivityEvent partial order
  + ObservedState expectation
  + compensation/failure expectation
```

Blocked scenarios must remain non-executable and must not acquire approval or
run records merely because an executor exists.

The database scenario is initially limited to switching between endpoints that
the desired graph already treats as provisioned. Data copy, replication
catch-up, schema migration, consistency verification, and old-database
retirement are separate external effects. Roadmap 0008 must not infer those
effects from an ordinary Postgres socket change.

The first executable boundary should therefore be:

```text
operator or provider establishes migration readiness
  -> durable, typed readiness evidence
    -> approved endpoint cutover
      -> bounded health/consistency observation
```

A future database-migration capability may produce and execute the readiness
evidence itself. Until that capability exists, migration remains explicitly
user/provider-managed and database replacement plans must fail closed rather
than treating a newly started empty database as a valid target.

- Approved plan can be claimed once.
- Unapproved plan cannot execute.
- Duplicate execution request is idempotent.
- Executor emits events for each step.
- Multi-node plans honor declared readiness dependencies and reject unresolved
  cycles.
- Docker start does not imply health; declared health checks produce observed
  healthy, unhealthy, timeout, or unknown state.
- Host ports are published only by explicit policy and remain distinct from
  Docker-private endpoint addresses.
- Socket-derived environment assignments reach the started container without
  appearing unredacted in activity descriptors or events.
- Failure records partial state.
- Compensation runs in reverse completion order where possible.
- Compensation failure is visible.
- Observed state updates are recorded separately from graph truth.
- Live local smoke example passes when Docker is available.
- `./test.sh`
- `python3 -m compileall control_plane_kit tests`
- `git diff --check`

## Handoff

Roadmap 0009 will package the control-plane instance server as an ordinary
`ApplicationBlock` and use this executor to make its lifecycle ordinary
approved activity work. The handoff must include stable
activity-run/event descriptors, idempotent execution requests, saga boundaries,
runtime provisioning capabilities, capability-route expectations, and which
blocks are demo-only versus durable protocol.

The handoff must specifically prove that Roadmap 0009 can:

```text
start Postgres
wait for Postgres readiness
start an ApplicationBlock with socket-derived environment
publish one explicitly requested host endpoint
wait on the application's declared health path
record the private and public observations separately
retain Postgres across application replacement where policy requires
```

Roadmap 0009 must not repair those generic Docker/runtime behaviors with a CPI
startup script, a Hub-specific executor, or a special control-plane-instance
node path.

Roadmap 0009 must also consume #246 before exposing CPI lifecycle commands.
The raw instance-registry lifecycle setter is scaffolding, not an acceptable
command boundary. Stop, pause, deconstruct, archive, and delete require guarded
transitions, append-only evidence, authorization, idempotency, and explicit
retained-data policy.
