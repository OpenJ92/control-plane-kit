Source: [control-plane-kit-core/tests/test_activity_plan_compiler.py](../../../../control-plane-kit-core/tests/test_activity_plan_compiler.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Diff-to-plan relations

This suite exercises the pure
[compiler](../src/control_plane_kit_core/planning/compiler.py.md), using a mix of
compiled/validated graph fixtures and directly constructed GraphDiff values.
It checks empty/metadata-only plans, owned startup dependencies, environment
bindings versus runtime-control socket effects, ingress allocation/teardown,
connection removal before node/runtime teardown, and provider replacement
through consumer reconciliation.

The health rows require WaitForHealthy after reconciliation; they do not probe
a service or advance durable graph state. The runtime-move row supplies a
synthetic typed diff and checks start/reconcile/stop dependencies; it does not
prove that a runtime adapter supports migration. Unsupported and ambiguous
forms remain explicit high-risk review blockers.

Repeated compilation is tested for deterministic equality. This does not make
activity identifiers complete fingerprints of source graphs or all private
material. Ordinary owned-resource fixtures do not cover every ownership mix;
the compiler companion records the newly identified non-owned-node/new-runtime
lookup edge case without claiming an executed regression.

Method inventory and the named behavioral bodies were read; graph fixture
helpers were sampled. No tests ran for this documentation. The existing Core
Docker-backed suite owns future executable validation, and live provider
acceptance belongs outside these tests.
