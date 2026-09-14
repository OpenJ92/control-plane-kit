Source: [control-plane-kit-core/tests/test_recovery_planning.py](../../../../control-plane-kit-core/tests/test_recovery_planning.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Structural recovery examples

Small pure materializers produce one application topology and a gateway with
public ingress. The five tests call the real graph compiler, validator, diff
and [recovery planner](../src/control_plane_kit_core/planning/recovery.py.md).
They establish teardown's destructive approval requirement and explicit
limitations, reconstruction's unknown-source warning and compensation
assessment, deterministic candidate/descriptor output, ingress operation
presence, and rejection of raw DeploymentGraph input.

The determinism test checks repeated planning of the same transition, canonical
ActivityPlan ownership and schema/version fields. It does not replay a
historical run or prove that runtime effects are reversible. The ingress test
checks that a topology-candidate assessment exists in each candidate; it does
not match every assessment to its operation or exhaust all assessment branches.

Full test file and fixture helpers read for this companion. Direct candidate
construction, supplied-policy behavior, malformed validated wrappers, every
manual-review case and all descriptor validation edges are not covered here.
No provider, DNS, Docker, persistence, authentication, cleanup or retry occurs.
The fixture hostname and image strings are topology data, not live evidence.
