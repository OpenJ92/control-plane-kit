Source: [node_control_surface_read_results.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/node_control_surface_read_results.py).
Maintain this companion with source and imported contract changes.

Static capabilities and status results are bound to one exact request and
declaration. Their profile follows the declaration: legacy v1 stays unchanged;
v2 capabilities disclose the health-bearing declaration. V2 status names the
wire field variable_registry_coverage, derived solely from installed variable
names. Its existing Python registry_coverage property keeps that same meaning.
Zero variables returns NONE. No callback installation, health or readiness is
observed or claimed. Mixed declarations with complete variables can still have
unknown workload health; health execution belongs downstream.

The result codec rejects profile, request, kind and declaration substitutions,
unknown fields and contradictory coverage. Whole-envelope and context bounds
precede nested result interpretation. Capabilities keep 16,902 bytes; status v1
keeps 4,811 and v2 permits 4,820 because its coverage key adds nine bytes. Tests
must establish reachable maxima and plus-one rejection; arithmetic is not proof.
There is no network, authentication effect, store or callback in this owner.
