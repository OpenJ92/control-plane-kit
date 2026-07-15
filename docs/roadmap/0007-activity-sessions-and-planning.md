# Roadmap 0007: Activity Sessions And Planning

Status: Draft
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
- and expose read models for pending sessions/plans.

Execution comes later.

## Non-Goals

- Do not start containers.
- Do not call block control routes.
- Do not mutate runtime state.
- Do not promise perfect undo.
- Do not bypass approval for consequential activities.
- Do not make every graph edit executable.
- Do not implement the full Hub yet.

## Suggested Issue Topology

1. Add operation session model and service.
   - Create session.
   - Close session.
   - Mark session status.
   - Attach actor/operator identity.
   - Persist via `ActivityHistoryStore`.

2. Add operation action model and service.
   - Record graph edit intent.
   - Record plan request.
   - Record approval request.
   - Record cancellation.
   - Preserve action ordering inside a session.

3. Add desired graph edit workflow.
   - Edit from a named base graph version.
   - Reject stale base versions or require explicit rebase.
   - Save new desired graph version.
   - Record the edit as an operation action.
   - Save the action and graph version inside one unit of work.

4. Add graph validation workflow.
   - Validate socket compatibility.
   - Validate runtime context containment.
   - Validate required connections.
   - Validate control-route descriptor shape.
   - Return structured validation results.

5. Add graph diff service.
   - Compare current and desired graph versions.
   - Return typed diff records.
   - Keep diff pure and effect-free.

6. Add activity plan model.
   - Activities must be typed enough to render, approve, and eventually execute.
   - Include dependency edges.
   - Include risk/destructive markers.
   - Include target node/edge references.

7. Add activity planner service.
   - Compile graph diff into activity plan.
   - Represent start/stop/register/switch/drain/wait/patch candidates.
   - Mark activities that cannot be planned safely.
   - Persist plan metadata and related operation action inside one unit of
     work.

8. Add approval policy and approval records.
   - Plan approval.
   - Destructive approval.
   - Actor/scope/comment/timestamp.
   - Approval cannot be inferred from UI state.
   - Approval request/decision records must be committed atomically with the
     command that creates or changes their workflow state.

9. Add recovery planning scaffold.
   - Represent `plan(B, A)` as recovery transition, not mathematical inverse.
   - Represent `plan(null, A)` as reconstruction candidate.
   - Mark irreversible or compensation-required activities.

10. Add session/plan read projections.
    - Pending sessions.
    - Pending approvals.
    - Plan details.
    - Risk/destructive activity summaries.

11. Add docs and examples.
    - Example backend swap session.
    - Example stale graph edit rejection.
    - Example approval-required plan.

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
- No execution happens.
- `./test.sh`
- `python3 -m compileall control_plane_kit tests`
- `git diff --check`

## Handoff

Roadmap 0008 will execute approved plans. The handoff must include stable
activity plan descriptors, approval records, idempotency expectations, and
which activity kinds are safe to execute first.
