# Docs

## Public Modules

- `control_plane_kit.core.graph`
  - graph values: `DeploymentGraph`, `Node`, `Endpoint`, `Edge`
- `control_plane_kit.core.diff`
  - `diff_graphs`, `GraphDiff`
- `control_plane_kit.core.activities`
  - activity AST values such as `StartNode`, `SwitchEdge`, `StopNode`
- `control_plane_kit.core.planner`
  - `plan_migration`
- `control_plane_kit.proxies`
  - composable protocol, behavior, and implementation descriptors
- `control_plane_kit.control_plane`
  - capability names and route contract descriptors
- `control_plane_kit.runtimes`
  - runtime interpreter protocol and dry-run runtime

## Commands

```bash
python3 -m unittest
python3 examples/api_blue_green.py
python3 examples/postgres_switch.py
python3 examples/local_cloudflare_auth.py
```

## Stability

This is an extraction scaffold.  Public names are intentionally small, but the
API should still be considered experimental until a real external runtime and
operator UI consume it.
