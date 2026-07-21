# EXTRACT.C External OCI Server-Product Language - Run 0001

## Scope

EXTRACT.C defines the pure language that lets core admit external OCI server
products without importing product implementations, process entrypoints, Docker
clients, HTTP clients, MCP transports, Postgres stores, or package-owned server
catalogues.

The target pipeline is:

```text
product.cpk.json
  -> strict descriptor codec
    -> ContainerServerProduct
      -> ProductCatalog admission
        -> configured product
          -> ordinary DeployBlock
            -> DeploymentTopology
              -> DeploymentGraph
                -> GraphDiff
                  -> ActivityPlan
```

## Topology

```text
#620 -> #621 -> #622 -> #623
  -> #624 -> #625 -> #626 -> #627
    -> #628 -> #629
```

## Boundary Decision

`cpk-server` is not core.

Core owns the generic external product language. A future `cpk-server` should be
expressible as ordinary descriptor data:

```text
ProductIdentity
  x OciImageReference
  x typed sockets
  x configuration requirements
  x secret requirements
  x verification contracts
  x lifecycle policy
```

The likely implementation home is the future server-product side:

```text
control-plane-kit-servers/
  products/
    cpk_server/
      implementation
      OCI image
      product.cpk.json
      tests
```

EXTRACT.C must make that representation possible, but must not implement
`cpk-server`, register a built-in CPI product, or specialize core for recursive
deployment.

## Test Context Law

For every non-trivial child:

```text
inspect governing frozen tests and new requirements
  -> extract behavioral law cards
    -> dry-run source and architecture with those laws in view
      -> design the target interface and refine issue topology
        -> translate or write focused target tests
          -> prove focused target red
            -> implement to green
```

Use `unittest` only. Do not use skips, xfail, weakened assertions, hidden
collection, or imports of the frozen implementation to manufacture success.

## Initial Risk Register

- Product descriptors are untrusted input.
- OCI tags are human hints, not execution identity.
- Descriptor admission must not execute product code.
- Product identity must not imply imported Python modules.
- Secrets must remain references and never appear as descriptor values.
- Core must not learn package-owned server names.
- The product language must remain useful to future `cpk-server` without making
  `cpk-server` special.
