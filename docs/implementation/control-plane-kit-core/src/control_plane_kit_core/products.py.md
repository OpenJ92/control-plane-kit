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

`configuration_requirements` is a keyword-only tuple of payload-free file slots.
The contract canonicalizes it and validates unique identities/paths across the
required and concrete union. Retained resource identities remain disjoint; both
configuration forms reject equal or segment-ancestor/descendant retained mount
paths. Sibling prefixes such as `/data` and `/database` remain distinct.
`product_configuration_slots(contract)` is the public canonical union projection;
it loses concrete content/digests and preserves all four slot fields. Instance
admission matches that union exactly, and `from_contract` cannot invent required
payloads. Concrete graph material is complete before planning.

Only nonempty requirements select `control-plane-kit.product/v2` and its required
field. Empty declarations omit the field and retain the original document schema
and canonical bytes. Explicit empty fields and mismatched discriminators refuse;
mapping input normalizes order while byte input must already be canonical.
Requirement-bearing nested product/contract codecs retain the same closed shape.
Instance payload substitution does not rewrite the registered document/reference.
