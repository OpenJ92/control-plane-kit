# control-plane-kit-core Agent Guide

This package follows the repository root `AGENTS.md` and
`docs/OPERATING_MODEL.md`. This file only narrows those instructions for the
extracted pure kernel.

## Scope

`control-plane-kit-core` owns the pure deployment planning language:

```text
DeploymentRecipe
  -> DeploymentGraph
  -> ValidatedGraph
  -> GraphDiff
  -> ActivityPlan
```

It does not own Docker interpreters, Postgres stores, FastAPI apps, HTTP
clients, MCP transports, package-owned servers, live runtime effects, Hello, or
other product implementations.

## Migration Loop

For every non-trivial behavior:

```text
inspect frozen parity law
  -> write a behavioral law card
    -> dry-run the target public boundary
      -> write a focused unittest successor test
        -> prove red for missing behavior
          -> implement green
            -> record successor evidence only when real
```

Do not copy frozen imports, fixtures, constructors, or module layout before the
target boundary is designed. Do not import `control_plane_kit` from this
package.

## Test Policy

Use the Python standard library `unittest` framework. Do not introduce pytest,
`xfail`, hidden collection, or skips to make migration pass.

## Decision Log

Each child PR should include:

- frozen law identities consulted;
- law cards added or updated;
- target public boundary;
- important snippets;
- red-to-green evidence;
- parity evidence added, or an explicit statement that none was claimed;
- security and secret-handling note;
- package-boundary note;
- handoff to the next issue.

