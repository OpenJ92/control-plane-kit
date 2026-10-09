Source: [runtime_management.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/runtime_management.py).
Maintain this companion with source and imported contract changes.

RuntimeManagement is a frozen pair of references to an existing gateway node and
named management ingress. GatewayTransitDeclaration is a separate frozen socket
and closed health-only profile advertisement. Its sole member is
`RECEIVER_HEALTH_READ_V2 = "gateway-receiver-health-read-transit.v2"`. It denotes
the existing ReceiverHealthReadRequestProfile.V2,
DelegatedGatewayReceiverHealthReadTransitGrantProfile.V2 and
ReceiverHealthReadResultProfile.V2 contracts, paired with the separate workload
receiver health grant V2. The obsolete V1 advertisement is rejected; there is
no alias, coercion or fallback. The existing V2 message labels do not imply
multiple supported advertisement generations.
It does not advertise variable reads, mutation, static surfaces or arbitrary
proxying. Both values use bounded canonical identifiers and strict nested codecs;
errors report categories without including supplied material.

This owner reuses the existing pure public_ingress reference/socket validation,
including its rejection of secret-shaped text, instead of introducing another
redaction policy. Algebra/products/graph may import it; it imports none of them.
Graph relationships and workload selection belong
above these values. There is no second graph, generated infrastructure, secret
material, authority, I/O, receipt or provider effect here. Selecting a path does
not authorize execution.
