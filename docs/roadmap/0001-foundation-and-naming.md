# Roadmap 0001: Foundation And Naming

Status: Draft
Depends on: current architecture document and ADRs

## Motivation

The package is still early enough that vocabulary can be corrected without
breaking users. This is the moment to make the public algebra read cleanly.

The most important foundation decision is that deployable blocks share a common
shape:

```text
DeployBlock = BlockSpec x RuntimeImplementation x BlockSockets
```

The block variant carries the domain distinction:

```text
Block
  = ApplicationBlock(BlockSpec, RuntimeImplementation, BlockSockets)
  | DataBlock(BlockSpec, RuntimeImplementation, BlockSockets)
  | ProxyBlock(BlockSpec, RuntimeImplementation, BlockSockets)
```

Specialized specs should only exist when they carry real distinct metadata.

## Goal

Make the public vocabulary match the intended model:

- `BlockSpec` is the shared identity/metadata value.
- Provider sockets expose values or endpoints.
- Requirement sockets need compatible provider values.
- Block sockets are the communication boundary of one block.
- Runtime implementations describe how a block may exist under a runtime.
- Runtime interpreters perform effects.

## Non-Goals

- Do not build Docker runtime execution in this vertical.
- Do not build `EnvironmentContract` in this vertical.
- Do not change package servers beyond vocabulary needed for clarity.
- Do not introduce compatibility layers unless tests prove they are needed.

## Suggested Issue Topology

1. Rename public spec examples to `BlockSpec`.
2. Decide whether code should rename existing `AppSpec`/specialized specs to
   `BlockSpec`.
3. Rename socket vocabulary from `RoleInputSocket`/`RoleOutputSocket` to
   `RequirementSocket`/`ProviderSocket`, if still present.
4. Rename `RoleSockets` to `BlockSockets`, if still present.
5. Update examples and tests to use the new vocabulary.
6. Update architecture docs, README, and ADRs to agree.

## Implementation Notes

Prefer a direct model over aliases if the package has no external users yet.
Aliases can feel safe, but they often preserve confusion. If aliases are needed
for an open PR or branch, mark them temporary and remove them before the package
is presented as public.

The target import shape should eventually feel like:

```python
from control_plane_kit import (
    ApplicationBlock,
    BlockSpec,
    BlockSockets,
    DeploymentRecipe,
    DockerImageImplementation,
    DockerRuntime,
    EnvironmentRequirementSocket,
    ProviderSocket,
    SocketConnection,
)
```

## Validation

- Existing examples still compile.
- Tests cover the compiler path from block declaration to graph descriptor.
- README snippets use the same names as source.
- Architecture doc and ADRs do not contradict the source names.
- `./test.sh`
- `python3 -m compileall control_plane_kit tests`
- `git diff --check`

## Handoff

The next vertical, runtime interpretation, depends on stable names. Do not start
runtime interpreter work while socket or spec vocabulary is half-renamed.

