Source: [products.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/products.py).
Maintain this companion with source and imported contract changes.

ProductRuntimeContract validates declared sockets/ports, capabilities, control
surfaces and the existing verification/delivery/lifecycle values. A health-bearing
surface requires HEALTH_CHECKABLE and NODE_CONTROLLABLE and must reference a real
HTTP provider. A product's SQL provider is not that HTTP control provider. Existing
provider_ports carries the port; no second management-endpoint registry is added.

Instantiation carries the same surface values into BlockSpec. Optional health
content is encoded by the surface codec, preserving variable-only wire. Existing
runtime permissions and secret deliveries retain their owners and rules. Product
declaration does not install a listener, authenticate a caller, query a database
or authorize a runtime. The declaration tests cover invalid products and valid
edge-free topology round trips through these public boundaries.

Optional GatewayTransitDeclaration advertises exactly one HTTP provider socket
for the existing health-read V1 request/transit-grant/result contracts. Own SDK
control, HTTP presence and NODE_CONTROLLABLE do not imply this role. The strict
product codec preserves it by value; instantiation passes it into BlockSpec.
Absent declarations remain omitted, retaining legacy descriptors. This is
authored product material, not proof that a registered interpreter implements
transit. The coupled Operations refusal must remain until that proof and
transport are admitted by the downstream issues.

Transit HTTP compatibility uses Protocol value equality: separately constructed
equivalent Python protocol values have the same meaning as decoded values.
