Source: [control-plane-kit-core/src/control_plane_kit_core/planning/recovery.py](../../../../../../control-plane-kit-core/src/control_plane_kit_core/planning/recovery.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Recovery as another proposed transition

The two planner entry points reuse [graph diff](../topology/diff.py.md) and the
[activity compiler](compiler.py.md). Both require ValidatedGraph inputs whose
validation succeeds. plan_recovery_transition compiles current to target;
its REVERSE_TRANSITION label does not require a historical inverse relation.
plan_reconstruction validates an empty graph using the target codec, then
compiles empty to target. That empty baseline is an assumption, not observed
absence of provider resources.

A RecoveryCandidate contains the canonical ActivityPlan, an approval
requirement, per-activity assessments and limitations. The factory always adds
GRAPH_STATE_ONLY and adds SOURCE_STATE_UNKNOWN for reconstruction. It derives
approval through the supplied policy or the default [ApprovalPolicy](../policies.py.md);
this records a requirement, not an approval decision or authenticated identity.

ReviewChange and explicit data destruction require manual review. Compute
start, stop, reconciliation and resource removal receive COMPENSATION_REQUIRED:
the graph cannot establish external recovery semantics. Connection changes,
public-ingress allocation/removal and health activities are only
TOPOLOGY_CANDIDATE, still requiring runtime validation. This assessment is
separate from an operation's [compensation declaration](activity_plan.py.md);
neither promises reversibility or invokes an inverse effect. Destructive labels
and assessment categories determine additional factory limitations.

Direct construction checks enum/value types and nonempty text, but does not
rederive approval, match assessment IDs to the plan, guarantee one assessment
per activity, enforce mode/name relationships or normalize collection fields to
tuples. requires_manual_review reads the supplied assessments. Consumer code
must not treat an arbitrary constructed candidate as independently certified
planning evidence. Text fields have no local length cap or general redaction.

The version-one descriptor embeds the canonical plan descriptor and these
annotations. This module offers no candidate decoder, durable record, provider
read, execution, retry or cleanup. It cannot release an uncertain attempt.
The [focused tests](../../../tests/test_recovery_planning.py.md) teach structural
transition and reconstruction; actual resource truth and any approved recovery
action remain downstream responsibilities.
