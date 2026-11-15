Source: [provider_policy_command_upgrade.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/provider_policy_command_upgrade.py).

#2095 fixes this existing independent transition to its original `7505500a`
predecessor and `7288fee8` target. It keeps dedicated policy/context admission
before database access, strict catalog/adjunct/data validation and original
provider-policy additive SQL. Caller-owned transactions, locks, rollback,
provisional status and uncertain commit remain unchanged. Exact historical
target is a no-op replay; no workspace-style receipt is invented.

Fresh `75b6b900` and sampling-expanded pseudo-priors refuse intact. Historical
success does not permit today's ordinary server startup on that older schema.
