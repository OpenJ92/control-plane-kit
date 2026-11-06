Source: [runtime_management_targets.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/runtime_management_targets.py).
Maintain this companion alongside its source.

This Operations module projects one symbolic management health target from an
accepted plan value, exact activity identity, candidate health operation and two
validated graph snapshots. It consumes Core values and performs no I/O. The
caller supplies the accepted record's plan and pinned snapshots; the helper does
not load or authenticate records, approval, currentness, fences or attempts.

`project_management_health_target` selects the expected operation internally
through `plan.activity(activity_id)`. Only exact nominal plan, activity identity
and `ObserveNodeHealth` inputs belong to this interface. Missing activities,
ordinary operations and bootstrap stages fail with a fixed projection error.
Core's `resolve_management_observation` then compares the candidate with the
selected operation and rederives its exact graph side, own-codec graph digest,
runtime and management relation, workload socket and health kind.

The frozen result retains the activity and resolved operation, gateway identity,
declared gateway transit socket, typed ingress and typed workload control
surface. The operation preserves runtime/side/digests/node/socket/kind; the ingress
preserves its connector identity. No address, provider port, credential or
observed endpoint is introduced. Constructing a result does not grant authority.

The transit socket comes from the resolved gateway's declaration, independently
of its own readiness socket. The workload surface comes from Core's exact SDK
selection. No ordinary consumer edge, hardcoded `control`, DATA substitution,
registered default or readiness downgrade is used. Existing probe and variable
target maps and the management execution refusal remain unchanged.

Only lookup `KeyError` and Core's public `ManagementObservationError` are
translated, around their respective calls. The fixed candidate-free error is
raised outside those contexts, leaving no candidate exception cause or context.
Unexpected implementation failures escape; a projection mismatch is never a
runtime unhealthy/unavailable observation.

The governing tests use actual Core graph-pair compilation and valid symbolic
fixtures. They cover edge-free nondefault sockets, independently selected plan
activities and same-runtime peers, substituted and self-matching forged pins,
equal-graph side identity, connector rebinding, compatible own-codec material,
and bounded malformed-input failures. Forged test plan values exercise graph
pin rederivation, not proof of accepted plan authority.

No durable state, schema, transaction, retry, cleanup, event, secret, network
exposure or destructive behavior changes. Operations #1845 retains approved
request/run/attempt authority and #1846 signing composition. Interpreters
#147/#148 own concrete bootstrap/public transport; Core's bootstrap resolution
remains available for their future reviewed composition. This health-only
projection does not move bootstrap authority out of Operations.
