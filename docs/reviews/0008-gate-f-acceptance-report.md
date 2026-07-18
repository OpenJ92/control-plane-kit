# Roadmap 0008 Gate F Acceptance Report

## Reproducible Commands

```bash
git diff --check
./test.sh
./gate-f-live-test.sh
```

All commands were run from the Roadmap 0008 branch after #365 merged.

## Complete Docker/Postgres Suite

```text
Ran 707 tests in 94.003s
OK
skipped: 0
```

The suite builds the Python 3.14 test image, starts a real Postgres 16
container, installs the current schema, exercises command services and stores,
and cleans its test resources on exit.

## Planning Algebra Corpus

Eleven typed graph-pair scenarios compile deterministically:

```text
fresh-deployment
backend-switch
scale-out-load-balancer
insert-rate-limiter
add-request-observer
move-service-runtime
switch-database-endpoint
partial-scale-in
full-teardown
no-change
unsupported-implementation-transition
```

The corpus spans initial deployment, ordinary update, cross-runtime movement,
socket insertion/removal, fan-out, database endpoint cutover, destructive
teardown, no-op, and an explicit review-blocked unsupported transition.

## Execution Corpus

Seventeen cases run through the canonical public `Deploy` composition, real
Postgres command services, canonical coordinator, projections, and a typed fake
effect provider:

```text
canonical:fresh-deployment
canonical:backend-switch
canonical:scale-out-load-balancer
canonical:insert-rate-limiter
canonical:add-request-observer
canonical:move-service-runtime
canonical:switch-database-endpoint
canonical:partial-scale-in
canonical:full-teardown
canonical:no-change
canonical:unsupported-implementation-transition
independent-leaf-failure
shared-leaf-failure
uncertain-paused
uncertainty-resolved-and-resumed
reverse-order-compensation
compensation-failure
```

These cases prove:

- all four `DeploymentTransition` variants;
- explicit review blockers and no fabricated execution;
- external readiness gating for database endpoint cutover;
- no-op planning without approval, admission, run, effect, or advancement;
- successful guarded current-graph advancement;
- failed work never advancing current graph;
- uncertain effects pausing without blind replay;
- typed operator resolution and reconstruction after restart;
- reverse durable compensation order;
- independently visible forward and compensation failures.

The fake provider is not a mock of application services. It implements the
closed effect capability protocol and records typed requests. Atomic adapter,
materialization, Docker, HTTP, probe, store, and coordinator behavior remains
covered independently.

## Architecture And Test Integrity

Executable AST policies passed for:

- package dependency direction and transport ownership;
- application composition boundaries;
- store/commit/environment/current-graph ownership;
- read-only API/CLI/MCP route surfaces;
- scenario prohibition on stores, schedulers, private coordinator methods, and
  manual deployment orchestration;
- skips, empty tests, placeholders, swallowed exceptions, and mock evidence.

The canonical Docker run installed optional server dependencies, so all three
reviewed conditional FastAPI tests executed. No skip, weakened assertion,
fixture-only substitute, or production compatibility alias was introduced.

## Concurrency, Transactions, And Recovery

Real-Postgres tests passed for:

- one-winner approval, admission, claim, expired-claim recovery, and graph
  advancement decisions;
- event ordinal serialization across independent connections;
- complete rollback after late writes and commit failure;
- stores never committing independently;
- no UnitOfWork or lock spanning an adapter call;
- crash before dispatch, during effect, and after effect-before-result;
- stable idempotency identities and deterministic reconstruction;
- uncertainty requiring operator evidence before resumption;
- compensation admission as pure journal data rather than a mutable cursor.

## Live Docker Proof

`./gate-f-live-test.sh` passed both live harnesses:

```text
explicit loopback host publication: passed
bootstrap: deploy-graph-1 / deploy-plan-1 / deploy-run-1
switch:    switch-graph-1 / switch-plan-1 / switch-run-1
teardown:  teardown-graph-1 / teardown-plan-1 / teardown-run-1
same public route: Hello, blue! -> Hello, green!
unauthorized mutation: 401, blue remained active
cleanup: passed
```

The blue-to-green mutation ran through `Deploy`, the canonical coordinator, and
the real authenticated control HTTP adapter. The script did not call the router
mutation route as an imperative bypass. Cleanup removed only exact label-proven
owned ephemeral resources.

## Definition Of Done

Current deployment recipes and the complete Roadmap 0008 scenario corpus remain
runnable or have functionally identical migrated execution through `Deploy`.
The package can plan, explicitly approve, admit, claim, execute, suspend,
recover, compensate, observe, and guard advancement for graph transitions.

## Residual Risks And Deferred Scope

- Initial live bootstrap and final teardown still use a split harness because
  the controller cannot join a Docker network before the initial effect creates
  it. The ordinary blue-to-green update is canonical.
- Live Docker names and ports are fixed local proof values, not a multi-tenant
  runtime allocation service.
- Database endpoint cutover does not perform schema or data migration.
- Cloud, Kubernetes, and mixed-runtime live adapters are not implemented.
- CPI packaging, lifecycle, public endpoint advertisement, and recursive
  parent-child spawning remain Roadmap 0009.
- The FastAPI/Starlette test client emits an upstream deprecation warning about
  future `httpx2` adoption; it does not skip or fail current tests.

No residual item blocks Roadmap 0008 closeout or draft PR review.
