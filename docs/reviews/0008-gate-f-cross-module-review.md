# Roadmap 0008 Gate F Cross-Module Review

## Scope

This review covers the public deployment composition introduced in Gate F and
the canonical Roadmap 0008 modules it composes:

```text
topology -> planning -> approval -> admission -> lifecycle
         -> execution -> recovery -> advancement -> projections
```

It reviews architecture, transaction ownership, external-effect windows,
authorization, concurrency, uncertainty, compensation, retained data, and test
integrity. It does not treat a green live demo as a substitute for atomic laws.

## Application Boundary

The application package contains only closed transition/suspension values,
callable stages, and two compositions:

```text
PrepareDeployment = Plan

ExecuteApprovedDeployment
  = Admit -> Claim -> Execute -> Advance

Deploy
  = PrepareDeployment
      -> ApprovalSuspension
        -> explicit Approve
          -> ExecuteApprovedDeployment
            -> AdvancedDeployment
             | ExecutionContinuation
             | RecoverySuspension
```

The package imports canonical `topology`, `workflows`, and bounded `effects`
values. Executable AST policy rejects stores, adapters, SQL, transport clients,
environment reads, commits, rollbacks, direct effect dispatch, and duplicate
graph/plan/run/event/saga/observation/recovery types.

One material finding was corrected in #379: every approval, approved execution,
continuation, and recovery resumption now proves that its nested canonical
transition equals the parameterized `Deploy(current, desired)` graph pair
before calling a downstream service.

## Transaction Boundaries

`Deploy` is a higher-order program, not one database command. It deliberately
composes several separately reviewable operator commands. The transaction law
applies at each canonical command-service boundary:

```text
one command-service invocation = one explicit Postgres UnitOfWork
```

Planning a deployment therefore records session start, desired graph, plan, and
approval request as distinct durable commands. A later failure does not pretend
those earlier operator facts never occurred. Each command commits all of its
participating store writes atomically; stores never commit independently.

Runtime execution preserves the stricter external-effect law:

```text
short transaction: reconstruct truth and record intent
  -> commit
    -> bounded adapter call
      -> short transaction: record result, event, observation, projection
```

Executable architecture policy forbids application-layer `commit` and
`rollback`. Real-Postgres coordinator tests prove no UnitOfWork remains active
during adapter execution, late writes roll back together, and crash windows
become durable continuation, failure, or uncertainty rather than blind replay.

## Authorization And Concurrency

- Approval requires the scope derived from the canonical plan.
- Admission requires current approval, current graph truth, and explicit
  execution authority.
- Claiming has one winner; expired ownership requires a typed recovery decision.
- Execution and recovery require the claimed worker authority.
- Current-graph advancement is a guarded compare-and-set with one winner.
- Control HTTP mutation requires resolved bearer authority and fail-closed
  address policy.
- Secret values remain transport-local and are redacted from descriptors,
  events, observations, projections, logs, and errors.

## Failure And Recovery

The canonical activity event journal is the only recovery history. Saga state,
schedules, compensation admission, and recovery projections reconstruct from
that journal. No mutable cursor or second recovery store exists.

An uncertain effect is not retried automatically. Compensation is admitted as
pure journal data, runs in reverse durable completion order, and preserves the
original forward failure independently from compensation outcomes. `Deploy`
surfaces `RecoverySuspension`; it does not decide operator recovery inside the
composition.

## Graph And Observation Truth

- Planning and materialization use graph versions pinned by the admitted plan.
- Later desired/current graph drift cannot retarget admitted work.
- Advancement publishes only terminal successful evidence and rejects a stale
  current pointer.
- Process start, transport reachability, application health, and readiness are
  separate observations.
- Observations are graph-correlated and may become stale; they never rewrite
  desired or current graph truth.
- Database endpoint cutover changes topology only. It does not imply schema or
  data migration.

## Retained Data And Docker

Docker mutation requires exact workspace/runtime/node/resource ownership labels
and a stable intent fingerprint. Foreign collisions fail before mutation.
Host publication is absent by default and exists only through typed explicit
material. Stop and compute removal preserve named data. Data destruction is a
separate typed operation against one proven owned data resource. External and
retained resources cannot cross the removal boundary.

## Test Integrity

The repository's AST integrity policy rejects unconditional, runtime, and
unapproved skips; empty tests; placeholder-only tests; swallowed exceptions;
and literal-false approved conditions. It reports mocks as review evidence.

The complete Docker image installs the optional FastAPI dependencies, so the
three reviewed `skipUnless` declarations do not skip in canonical validation.
The latest complete run executed 706 tests with zero skips and zero failures.
Gate F added adversarial checks rather than changing expected behavior:

- foreign transition evidence fails before writes or effects;
- manual scenario orchestration is rejected by AST policy;
- the live switch must use `Deploy` and the real coordinator;
- unauthorized mutation remains 401 and leaves the active route unchanged;
- live cleanup refuses resources without exact ownership proof.

No assertion was relaxed, no requirement was deleted, and no production alias
was added solely to satisfy a test.

## Provisional Surface

`examples/scenarios/workflow.py` remains an older planning-only helper used by
the backend-swap planning example and by explicit bootstrap/teardown portions of
the live harness. It is not used by the canonical scenario runner or the live
blue-to-green update. Issue #365 must either migrate those consumers or label
the helper as a narrow bootstrap/planning fixture so it cannot be mistaken for
a second deployment program.

The live harness uses fixed local Docker names and ports. It is operator proof,
not a multi-tenant runtime implementation. CPI packaging, server lifecycle,
public endpoint advertisement, and hub spawning remain Roadmap 0009 work.

## Result

The architecture remains coherent after #379. No unresolved security,
transaction, data-retention, uncertainty, or test-integrity blocker prevents
#365. The next issue should narrow public exports and teach the package from
`Deploy`, not from workflow internals.
