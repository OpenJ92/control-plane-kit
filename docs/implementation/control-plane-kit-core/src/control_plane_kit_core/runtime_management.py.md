Source: [runtime_management.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/runtime_management.py).
Maintain this companion with source and imported contract changes.

RuntimeManagement is a frozen pair of references to an existing gateway node and
named management ingress. GatewayTransitDeclaration is a separate frozen socket
and closed health-only profile advertisement. Its V1 meaning is exactly the
existing NodeHealthReadRequestProfile.V1,
DelegatedGatewayNodeHealthReadTransitGrantProfile.V1 and NodeHealthReadResultProfile.V1.
It does not advertise variable reads, mutation, static surfaces or arbitrary
proxying. Both values use bounded canonical identifiers and strict nested codecs;
errors report categories without including supplied material.

This owner depends only on the standard library. Algebra/products/graph may import
it; it imports none of them. Graph relationships and workload selection belong
above these values. There is no second graph, generated infrastructure, secret
material, authority, I/O, receipt or provider effect here. Selecting a path does
not authorize execution.
