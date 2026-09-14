Source: [control-plane-kit-core/src/control_plane_kit_core/planning/saga.py](../../../../../../control-plane-kit-core/src/control_plane_kit_core/planning/saga.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Syntax, replay and scheduling

This module contains several pure interpretations. SagaProgram is the explicit
sum End | StepNode | ParallelNode. A step carries an effect value and an optional
compensation value; parallel branches share a continuation. chain and then
construct new syntax, and program_steps walks declared branch order and rejects
duplicate step IDs. Flattening preserves a deterministic listing; it does not
execute branches or turn their order into provider evidence.

The generic effect is only checked against None. Callback-free values are the
intended usage, not a prohibition enforced by SagaStep. Frozen dataclasses are
not deep immutability or a universal hostile-input boundary. ParallelNode
requires at least two nonempty program branches, while duplicate step identities
are rejected on program traversal rather than every node construction.

initial_state interprets program steps into lifecycle evidence.
SagaState.initial_for_plan instead takes canonical ActivityPlan IDs and derives
compensation availability from the plan's Compensate declarations. decide maps
commands to events; evolve validates and applies the corresponding lifecycle
transitions, and reconstruct folds events from an initial program state.
Neither path persists events, performs effects or authenticates their origin.

Forward success requires a running step and appends completion order. Failure
records the failed step. Cancellation or a compensation request does not erase
running work. Compensation candidates are succeeded, compensatable steps in
reverse completion order; begin-compensation must select the first candidate.
The low-level lifecycle functions do not enforce program dependency readiness,
wait for all in-flight work or demand an external approval. In particular, a
begin-compensation transition is not itself an authorization boundary.

derive_schedule adds ActivityPlan dependency interpretation. It requires exact
plan/evidence step membership, rejects duplicate identities and validates
completion/failure membership against statuses. Pending work becomes ready only
after all predecessors succeeded. ReviewChange, failed/compensated predecessors,
transitive blockers and global failure/cancellation/compensation state produce
explicit blocked reasons. Running work remains visible. Ordinary result groups
follow plan order; compensation_ready follows reverse completion order and
stays empty while forward or compensating work is running.

Those checks do not rederive every supplied state field or prove historical
dependency order; manually supplied compensation_available flags are trusted.
Use the canonical plan initializer and validated journal path for normal
composition. ExecutionSchedule is a projection, not permission to dispatch.
Its terminal flag means no schedulable or in-flight work remains; blocked or
failed work can be terminal. successful also excludes blockers/failure and
compensated results. An empty schedule is successful even though the empty
SagaState status remains ACTIVE; neither fact establishes useful work occurred.

# Canonical activity-journal projection

project_activity_journal starts from the supplied plan and folds a restricted
activity-event vocabulary. It rejects mixed run IDs, foreign activity IDs,
duplicate or decreasing ordinals, and invalid translated lifecycle transitions.
Ordinals need not be contiguous, and duplicate event IDs are not independently
checked. Non-run events without activity_id are ignored here. Constructor checks
are uneven: run identity is canonical, text IDs are only partly checked, and the
positive ordinal check uses isinstance(int), which also admits booleans.

Unsupported forward work becomes failed state, synthesizing its start when
needed. An uncertainty marker is retained separately from the start event: for
a normally started step, state remains RUNNING but the event is no longer listed
as in_flight. Resolution or abandonment requires matching prior uncertainty;
resolution produces success/failure, while abandonment produces failure, not
proof of provider cleanup or absence. Compensation has parallel uncertainty
collections and translates abandonment to compensation failure. The four
operational collections are ordered by activity ID, separate from completion
order.

This projection does not supply full durable event admission, attempt
correlation, leases, approvals or external outcome proof. Its uncertainty
collections must accompany schedule interpretation; they are not evidence to
retry an ambiguous mutation. Current downstream consumers include the
[Operations coordinator](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/coordinator.py)
and [graph advancement](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/advancement.py),
which add their own durable guards. Those are consumers, not Core imports or
ownership transferred to this module.

[Test navigation](../../../tests/test_saga.py.md),
[scheduling laws](../../../tests/test_scheduling.py.md) and
[compensation declarations](../../../tests/test_compensation_planning.py.md)
cover different layers. Errors and generic effect values are not uniformly
bounded or redacted. No clock, store, provider, scheduler thread or automatic
rollback runs here.
