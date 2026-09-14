Source: [runtime_management_admission.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/runtime_management_admission.py).
Maintain this companion alongside its source.

This pure Operations policy examines both complete pinned graphs for an explicit
runtime management selection, a gateway transit declaration, or SDK control
surfaces. Each is sufficient to withhold executable management interpretation
until the accepted transport exists. It does not infer support from a node name,
metadata flag, changed node, supplied plan, or product registration. Ordinary
legacy verification without this material retains existing behavior.

`runtime_management_execution_is_unsupported(current, desired, plan)` permits
only a congruent no-op: the supplied plan has no activities, both graphs validate,
and Core's diff/compiler also produces no activities. An invalid graph or forged
empty plan cannot qualify. Supplying no plan means direct activity translation
and never receives this exception, even for equal graphs. This owner imports
Core and Operations' existing pure product-reference/registration values;
callers retain transaction, authorization, and history ownership.

The same refusal applies when authored fields omit a declaration that the exact
referenced registered product supplies. Both graph reference sets use the
existing normalized identity-plus-digest extractor; only matched descriptors
are inspected. Unrelated registrations and same-identity/different-digest
descriptors do not trigger refusal. The caller supplies the registration snapshot:
there is no extra status filter absent from direct effect material selection.
Any SDK surface, including variable-only surfaces, is conservatively guarded;
the advertised transit profile itself remains health-only.

Malformed reference parsing returns a fixed refusal independently of catalog
contents, before the no-op exception. Only the extractor's ValueError family is
handled here; its candidate-bearing exception does not escape or remain as the
cause/context of a caller's error. Unexpected failures outside that call retain
existing error/uncertainty semantics. This checks declared powers in selected
material, without auditing or rewriting general graph/product congruence.

The policy performs no I/O and persists no facts. It introduces no credential,
address, grant, network exposure, or destructive effect. The three consumer
test groups cover both graph directions, transit-only and SDK material,
equal/name-only no-ops, forged emptiness and direct activity rejection.
Product-contract congruence and transport support remain downstream work;
declarations and these tests do not prove live execution.
