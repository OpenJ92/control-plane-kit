Source: [control_routes.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/control_routes.py).
Maintain this companion with source and imported contract changes.

Route sets are pure protocol descriptors, not installed handlers. Existing
COMMON_STATUS paths under /__deploy and the four NODE_CONTROL_ROUTES remain
unchanged. NODE_HEALTH_ROUTES adds one fixed GET /__control/health/{health_kind}
template with node-health:read scope, available through the existing closed
route_set_named lookup. A surface's explicit typed health_reads selects its
allowed concrete path; a legacy HEALTH_CHECKABLE capability does not imply it.

Later SDK composition must install optional health routes atomically alongside
the existing routes, preserve old callers, and require no variable-command
verifier for a health-only declaration. This file installs nothing and neither
authenticates a request nor duplicates a product's health function.
