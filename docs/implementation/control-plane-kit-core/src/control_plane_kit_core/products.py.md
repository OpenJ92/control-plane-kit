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
