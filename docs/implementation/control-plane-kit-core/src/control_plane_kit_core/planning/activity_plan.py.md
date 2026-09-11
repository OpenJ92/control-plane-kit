Source: [control-plane-kit-core/src/control_plane_kit_core/planning/activity_plan.py](../../../../../../control-plane-kit-core/src/control_plane_kit_core/planning/activity_plan.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Intended activities and valid composition

This owner defines the activity language: typed targets and operations,
dependency edges, risk/impact labels, compensation descriptions and ActivityPlan.
Constructing these values starts no process, checks no provider and creates no
durable approval. Operations owns admission and execution history.

PlannedActivity checks the supported operation/target combinations, normalizes
dependencies and derives compensation from the operation. Many operation
dataclasses themselves merely retain their annotated target; this admission
happens when they enter a PlannedActivity. ActivityId uses the canonical identity
helper, while ordinary target names only reject blank text. Do not present every
target or exception as bounded or redacted.

ActivityPlan rejects duplicate identities/dependencies, absent predecessors,
self-dependencies and cycles, then produces deterministic topological ordering
with identity-sorted ready groups. Validation also requires high-or-critical risk
for an activity labelled destructive, critical/destructive labels for data
destruction, and high-or-critical review risk. It does not derive every
operation's impact: a manually constructed StopNode can retain default low,
non-destructive labels. The [compiler](compiler.py.md) chooses labels for its
generated plans; plan construction alone is not a complete safety assessment.

ready_for_execution means no ReviewChange remains. An empty plan is valid and
has that property; it does not mean useful work exists or permission was given.
Dependency ordering states a required relation, not a provider observation or
evidence that predecessors completed. Missing activity lookup raises KeyError.

Compensation is a closed choice: an inverse operation with base/desired graph
material, no compensation required, or an explicit non-compensatable reason.
For example, stopping a node proposes starting it from base material; permanent
resource removal and data destruction are not assigned fictitious inverses.
This is deterministic planning meaning, not an automatic rollback promise or
authority to invoke that inverse. The [saga owner](../../../../../../control-plane-kit-core/src/control_plane_kit_core/planning/saga.py)
and Operations consume it at their own boundaries.

[test_activity_plan.py](../../../tests/test_activity_plan.py.md) navigates the
composition and marker laws; the
[compensation tests](../../../../../../control-plane-kit-core/tests/test_compensation_planning.py)
cover canonical inverse/material choices and codec retention. These are pure
contracts, not runtime cleanup witnesses.
