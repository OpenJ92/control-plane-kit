Source: [control-plane-kit-core/src/control_plane_kit_core/policies.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/policies.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Policy values over supplied facts

This module owns the closed PolicyScope vocabulary, allow/deny results,
approval requirements, a destructive-activity classifier and workspace
retention values. The policies are pure decisions over supplied scopes,
principal strings and plan labels. They authenticate nobody, write no approval
record, inspect no provider and confer no execution permission by themselves.

Hub and instance checks require distinct read/create/edit scopes.
ApprovalPolicy requires plan-request scope separately from decision scope.
Destructive decisions require the stronger scope and distinct requester/decider
strings by default; explicit ALLOW_SELF changes that policy, and nondestructive
self-approval is allowed. Missing scope returns before principal-separation
validation. Rotation requests and decisions use their own focused scopes.

requirement_for derives maximum risk and destructive status from the plan's
stored activity labels, not from a fresh operation or provider classification.
An empty plan yields informational, ordinary approval requirements. Consumers
must preserve the [plan owner's](planning/activity_plan.py.md) marker semantics
and perform separate freshness, useful-work and execution admission checks.

DestructiveActivityPolicy is a separate selected-name classifier. It maps data
destruction, resource removal and socket switching to selected stronger-approval
categories; otherwise it derives a name from the operation class. An unknown
name is merely not classified as destructive. That allow result is not a closed
admission check for arbitrary operations, and this classifier is not invoked by
requirement_for to repair incorrectly labelled plans.

retention_for returns the table's meaning for a closed WorkspaceLifecycle.
Stopped/deconstructed and deleted states retain different durable-history
promises; no resources are stopped, retained or deleted by looking up a value.
Actual transitions and provider cleanup remain downstream responsibilities.

Despite PolicyDecision's docstring, arbitrary reason text has no size cap or
secret scrub here; it is retained in its descriptor. The constructors enforce
selected enum/boolean/string shapes, not a complete safe-publication boundary.
[test_policies.py](../../tests/test_policies.py.md) covers scope distinctions,
approval separation and selected classification/retention cases. These are
policy laws, not authentication or deletion evidence.
