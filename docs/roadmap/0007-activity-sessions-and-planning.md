# Roadmap 0007: Activity Sessions And Planning

Status: Implementation complete on roadmap branch; integration PR pending
Depends on: Roadmap 0005, Roadmap 0006

## Motivation

Deployment work is session-shaped.

The package should not treat topology mutation as a direct route call from
"graph A" to "graph B." Operators make a series of related choices. Those
choices should be grouped, reviewed, interpreted, approved, and preserved.

This follows the Pottery Factory workflow pattern:

```text
Transfer session
  groups transfer lines before submitting inventory mutations

Audit session
  groups observations before submitting balance truth

Control-plane operation session
  groups topology edits before planning and execution
```

The control-plane version is:

```text
OperationSession
  -> OperationAction*
  -> desired graph version
  -> GraphDiff
  -> ActivityPlan
  -> Approval
```

This roadmap builds the session and planning side of mutation. It should not
execute runtime effects yet.

Because this roadmap writes related session, graph-version, plan, and approval
records, it must use the ADR 0008 unit-of-work policy:

```text
one operator command = one explicit Postgres transaction boundary
```

The transaction boundary belongs in the API/application-service/use-case layer.
Individual stores and repositories participate in that boundary; they do not
commit independently.

## Goal

Implement operation sessions, operation actions, desired graph edits, graph
diffing, activity planning, and approval records.

This roadmap should make it possible to:

- start an operation session,
- record operator actions,
- save desired graph versions with optimistic concurrency,
- validate graph edits,
- diff current and desired graph versions,
- produce an inspectable activity plan,
- classify dangerous activities,
- request and record approvals,
- expose read models for pending sessions/plans,
- and hand Roadmap 0009 transport-neutral command/planning application services
  that a composed FastAPI CPI server can call without rebuilding workflow
  semantics in route handlers.

Execution comes later.

## Non-Goals

- Do not start containers.
- Do not call block control routes.
- Do not mutate runtime state.
- Do not promise perfect undo.
- Do not bypass approval for consequential activities.
- Do not make every graph edit executable.
- Do not implement recursive child-instance management yet; Roadmap 0009 will
  compile it through these same session, planning, and approval boundaries.

## Canonical Issue Topology

The semantic roadmap number defines execution order. GitHub issue numbers do
not: `0007.5` is issue #138 while `0007.6` is #137, and `0007.9` is #142 while
`0007.10` is #141.

1. `0007.1` Postgres UnitOfWork command boundary (#133).
   - `0007.1.1` Postgres UnitOfWork primitive (#144).
   - `0007.1.2` Transaction fixture and rollback hardening (#145).
   - `0007.1.3` UnitOfWork docs and store adapter contract (#146).

2. `0007.2` Operation session and action commands (#134).
   - Create, close, and cancel sessions.
   - Record ordered actions with concurrency-safe ordinals.
   - Compose every command through UnitOfWork.

3. `0007.3` Desired graph edit workflow (#135).
   - `0007.3.1` Desired graph edit request/result data (#147).
   - `0007.3.2` Desired graph descriptor reconstruction boundary (#148).
   - `0007.3.3` Desired graph command workflow (#149).
   - `0007.3.4` Idempotency and concurrency hardening (#150).
   - Preserve authored block-specification type information through graph
     descriptor persistence and reconstruction. Do not collapse future closed
     specification variants into untyped metadata strings.
   - Keep the descriptor codec extensible so Roadmap 0009 can add a package
     `ControlPlaneInstanceSpec` variant without rebuilding graph-edit workflow
     semantics.

4. `0007.4` Pure graph validation workflow (#136).

5. `0007.5` Typed, deterministic graph diff service (#138).

6. `0007.6` Activity plan records and descriptors (#137).
   - `0007.6.1` Activity plan algebra (#151).
   - `0007.6.2` Descriptors and persistence payloads (#152).
   - `0007.6.3` Descriptor hardening (#153).

7. `0007.7` Activity planner command workflow (#139).
   - `0007.7.1` Pure diff-to-plan compiler (#154).
   - `0007.7.2` UnitOfWork-backed planner command service (#155).
   - `0007.7.3` Idempotency and failure hardening (#156).

8. `0007.8` Approval request and decision workflow (#140).

9. `0007.9` Recovery planning scaffold (#142).

10. `0007.10` Session and plan read projections (#141).
    - `0007.10.1` Core projections (#157).
    - `0007.10.2` FastAPI read routes (#158).
    - `0007.10.3` CLI and MCP read adapters (#159).
    - `0007.10.4` Security and edge hardening (#160).

11. `0007.11` Docs, examples, hardening, and closeout (#143).
    - Curate the command/planning service constructors, typed request/result
      values, stable error mapping, UnitOfWork ownership, and security policies
      that Roadmap 0009 must compose behind HTTP.
    - Do not build the final CPI FastAPI application here; make its service
      dependencies explicit and injectable.

The executable dependency chain is:

```text
#144 -> #145 -> #146 -> #134
  -> #147 -> #148 -> #149 -> #150
  -> #136 -> #138
  -> #151 -> #152 -> #153
  -> #154 -> #155 -> #156
  -> #140 -> #142
  -> #157 -> #158 -> #159 -> #160
  -> #143
```

## Target Workflow

```python
session = sessions.start(
    title="Replace API backend",
    actor=operator,
)

desired = graph_edits.apply(
    session_id=session.session_id,
    base_version=current_desired.version,
    edit=AddBlock(api_v2),
)

plan = planner.plan(
    session_id=session.session_id,
    current=current_graph,
    desired=desired.graph,
)

approval = approvals.request(
    session_id=session.session_id,
    plan_id=plan.plan_id,
)
```

Expected plan shape:

```text
StartNode(api-v2)
WaitForHealthy(api-v2)
RegisterTarget(auth-router, api-v2)
SwitchTarget(auth-router, api-v2)
DrainTarget(api-v1)
StopNode(api-v1)
```

No runtime effect happens in this roadmap.

## Approval Model

The backend must enforce:

```text
The person who proposes a consequential change does not necessarily have the
authority to execute it.
```

Possible scopes:

```text
graph:edit
plan:request
plan:approve
plan:execute
plan:approve-destructive
runtime:destroy
secret:rotate
history:delete
```

The UI can make the danger visible, but the backend owns the approval boundary.

## Implementation Notes

- Follow ADR 0008 for transactions, idempotency, graph-version concurrency, and
  auditability.
- API/application services own transaction boundaries; repositories do not
  independently commit multi-table session, graph, plan, or approval commands.
- Sagas are not used for this roadmap's store-local work. They begin in the
  execution roadmap when approved plans cross into Docker, block control routes,
  cloud providers, or other external effects.
- Graph edits should be idempotent.
- Plan requests should be idempotent.
- Approval records belong in activity history from day one.
- Keep activity planning pure.
- Store activity plans durably before any later execution roadmap consumes
  them.
- Do not make recovery planning overconfident. Mark limitations explicitly.

## Validation

- Session creation is transactional.
- Desired graph edit plus operation action commit together or roll back
  together.
- Plan creation plus plan-request action commit together or roll back together.
- Approval request/decision plus related workflow state commit together or roll
  back together.
- Operation actions preserve ordering.
- Stale graph edits are rejected.
- Graph diff does not mutate stores.
- Activity plans are persisted and renderable.
- Destructive activities require stronger approval metadata.
- Approval decisions are recorded.
- Command and planning services can be constructed independently of FastAPI and
  called through one explicit UnitOfWork per operator command.
- Their request/result/error descriptors are stable enough for Roadmap 0009 to
  expose without inventing durable semantics in route handlers.
- No execution happens.
- `./test.sh`
- `python3 -m compileall control_plane_kit tests`
- `git diff --check`

## Handoff

Roadmap 0008 will execute approved plans. The handoff must include stable
activity plan descriptors, approval records, idempotency expectations, and
which activity kinds are safe to execute first.

Roadmap 0009 will compose these workflows into the real CPI server. The 0007
handoff must therefore identify:

```text
session command service
desired-graph command service
planning service
approval service
read projections
typed request/result/error values
UnitOfWork factory and transaction ownership
authorization policy inputs
typed block-spec descriptor/reconstruction extension point
```

## Closeout Result

Roadmap 0007 is implemented as a non-executing, Postgres-backed planning
pipeline. The capstone example is `examples/backend_swap_planning.py`; the
operator guide is `docs/ACTIVITY_PLANNING.md`; and implementation learning is
recorded in `docs/learning/roadmap-0007/run-0001.md`.

The final implementation deliberately stops at a pending or decided approval.
It does not create an `ActivityRun`, call a runtime interpreter, or signal a
block control route. Those effects remain Roadmap 0008 work.

Roadmap 0009 may add FastAPI adapters around these services, but it must not
reimplement session ordering, graph concurrency, planning, approval, or commit
behavior in HTTP handlers.
