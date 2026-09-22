Source: [management_observations.py](../../../../../../control-plane-kit-core/src/control_plane_kit_core/planning/management_observations.py).
Maintain this companion with source and imported contract changes.

The closed requests are ObserveManagementBootstrap (local gateway readiness,
ingress gateway readiness, connector connection, authenticated path) and ObserveNodeHealth (exact workload
socket, semantic kind and runtime-gateway transport). ManagementObservationTarget
pins a runtime, base/desired graph side, graph digest and relation digest. These
are requested observations, not ready flags or authority.

Values depend only on existing pure reference/health/canonicalization contracts.
They import no graph, ActivityPlan, provider, persistence or result language.
Constructors require enums; JSON decoding admits exact wire strings. Canonical
target/request bounds are 512/1024 bytes. Errors are fixed, bounded and omit
candidate exception context. Graph resolution belongs above the value module.

The public activity codec adds distinct v1 operation kinds. Existing operation
bytes and meaning remain unchanged; old readers refuse new kinds. Both new
observations have NoCompensationRequired. Deterministic identities repeat across
executions: downstream request/run/attempt admission must establish freshness.

`gateway-ingress-ready` is a distinct stage for the gateway's own readiness
predicate observed through its protected named ingress. It does not reinterpret
`gateway-local-ready`, alter old descriptors/hash domains, or recompile admitted
plans. Old readers may reject the new enum value; adoption requires explicit pins.
A correlated nonhealthy protected response may establish path availability but
only HEALTHY can satisfy ingress readiness. Each stage needs its own original
request, attempt and result downstream; these values supply none of that authority.
