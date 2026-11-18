# Historical upgrade inputs

These are complete, unmodified SQL files from the commits that defined each
supported historical upgrade source, rather than slices of today's schema.
`tests/historical_schema_fixture.py` checks their byte hashes and tests observe
their PostgreSQL catalogs against independent semantic fingerprints.

| Fixture | Original commit | SQL SHA-256 |
| --- | --- | --- |
| pre_workspace_permissions.sql | 71478102aa1d51ad536c74cd926aca8ca820bf7f | a38cb3590078ab33454ff26c14a9e1a3f3705aff01834533209aada14aa03dc1 |
| pre_provider_policy_commands.sql | 5722788b9fb38eeb9987b2453552f870f4a7a4b2 | fe63d2f4171a0672d4b0b3a111a25ba6e8bfd7258bc7c9853570eb2ea8c7bdde |

Original path: `control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema.sql`.
Both existing upgrade owners terminate at the historical 7288fee8 contract,
not the newer sampling/current schema. See governing CPK #2095.
