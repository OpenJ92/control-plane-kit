Source: [compiler.py](../../../../../../control-plane-kit-core/src/control_plane_kit_core/planning/compiler.py).
Maintain this companion with source and imported contract changes.

Runtime management reference changes use the existing ReviewChange path. They
are excluded from generic runtime reconciliation because selecting a control
path is not physical runtime configuration. Reference changes alone cannot
produce ReconcileRuntime, StartRuntime or StopRuntime. Equal/name-only graph
pairs retain their existing empty plans. The separate management_compiler now
adds graph-pair observation planning above this unchanged structural interface.
It preserves independent verification and actual service dependencies and keeps
unsupported cutovers review-blocked. The coupled Operations refusal remains
required until downstream plan/admission/transport support is accepted. The
structural compiler itself adds no management operation or runtime effect.
