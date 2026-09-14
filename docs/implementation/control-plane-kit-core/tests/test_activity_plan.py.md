Source: [control-plane-kit-core/tests/test_activity_plan.py](../../../../control-plane-kit-core/tests/test_activity_plan.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Activity composition laws

This pure unittest file exercises the package's
[activity values](../src/control_plane_kit_core/planning/activity_plan.py.md).
It protects deterministic topological order across permutations, typed
operation/target pairings at PlannedActivity construction, duplicate/missing/
self/cyclic dependencies, deterministic violation ordering and review blockers.

Risk tests enforce the declared-marker rules and the stronger data-destruction
rule. The operation matrix deliberately admits ordinary stop/removal variants
with default low/non-destructive labels; it is not a test that the constructor
derives safe impact from every operation. Empty plans are explicitly valid and
ready_for_execution; useful-work and approval checks belong elsewhere.

Fixture target names and IDs do not identify live resources. No runtime
interpreter, approval service, database or health probe participates. All
behavioral bodies were read for this note; this is source evidence, not a new
suite result. Validation remains the existing Core Docker-backed test script.
