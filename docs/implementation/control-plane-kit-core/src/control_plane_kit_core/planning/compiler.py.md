Source: [control-plane-kit-core/src/control_plane_kit_core/planning/compiler.py](../../../../../../control-plane-kit-core/src/control_plane_kit_core/planning/compiler.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Structural differences to intended work

compile_activity_plan interprets a typed GraphDiff into an ActivityPlan. Its
input carries changes, not a complete fresh provider snapshot. It neither reads
a runtime nor authorizes the plan, resolves secrets, dispatches work or records
completion. The [diff owner](../topology/diff.py.md) and
[activity algebra](activity_plan.py.md) retain those separate meanings.

Owned additions produce runtime/node startup; nodes also receive health
activities. Owned removals distinguish stopping retained compute from removing
ephemeral compute. Non-owned additions/removals produce no corresponding
lifecycle draft. Data-resource destruction is not inferred here. Unsupported or
ambiguous changes become high-risk ReviewChange values; lifecycle-policy edits
are kept out of ordinary reconciliation. Graph-level metadata modifications
need no runtime activity.

Node field changes coalesce into one reconciliation and a following health
activity; runtime field changes coalesce separately. Environment-bound
connections are process material, so they do not receive separate socket
effects. Runtime-control connections do. Ordering links newly started providers'
health to consumers, connection changes to old-provider stopping, nodes to
runtime startup/teardown, and ingress target health to allocation and connector
startup. Ingress teardown stops the connector before removing ingress and then
stopping its target. These are relations among available drafts, not a proof of
live health, successful migration or supported runtime movement.

Activity IDs combine an operation label with sixteen hexadecimal characters
from SHA-256 over sorted-key JSON change descriptors. Coalesced work hashes its
change-descriptor list. Those descriptors are selected, partly redacted
projections; an activity ID is not a collision-free identity for all graph
material, approval meaning or provider effects. Do not substitute it for
Operations' pinned graph/plan/request correlation.

A source-derived edge case remains: the addition pass skips a non-owned node's
start draft, but dependency assembly indexes that node's start draft when its
runtime has a new start activity. An attached/external node added inside a new
owned runtime can therefore reach a missing-draft lookup. This documentation
does not claim an executed reproducer or repair; the combination needs focused
review before being advertised as supported.

[test_activity_plan_compiler.py](../../../tests/test_activity_plan_compiler.py.md)
covers the ordinary startup, reconciliation, ingress and teardown relations.
Follow lifecycle and socket-binding owners when changing those rules. The
compiler's result is still an inspectable proposal; authorization and external
readiness remain downstream responsibilities.
