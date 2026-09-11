Source: [control-plane-kit-core/src/control_plane_kit_core/capabilities.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/capabilities.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

This module is the closed catalogue of advertised operator powers and their
display/control-route descriptors. It relies on
[ControlRouteSetName](../../../../../control-plane-kit-core/src/control_plane_kit_core/control_routes.py) for route-set
identity. Capability lookup accepts the known enum or its string value and
rejects unknown names; expanding the catalogue is an explicit public-language
change.

A capability says what a block advertises. It does not instantiate a route,
authenticate a caller, prove a running server implements the power, or authorize
mutation. RESTARTABLE intentionally has no route set; node lifecycle work must
not gain an invented route because the UI displays that capability.
[algebra.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/algebra.py) additionally enforces agreement
between NODE_CONTROLLABLE and typed control surfaces.

[test_verification_capabilities.py](../../../../../control-plane-kit-core/tests/test_verification_capabilities.py)
checks route associations, catalogue lookup and compiled metadata using a pure
materialization fixture. VerificationCapability in verification.py is a separate
vocabulary of interpreter-supported checks, not an alias of CapabilityName.
