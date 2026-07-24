# control-plane-kit-servers

This repository owns reusable OCI server products and their descriptors for
`control-plane-kit`. It is not the algebraic core. It may import the pinned
`control-plane-kit-core` package to express values, descriptors, socket
contracts, process handoff contracts, and tests. Core never imports servers.

## Repository Law

- one product implementation lane owns one directory; explicit descriptor variants may share that lane.
- Catalogue imports values, not applications or stores.
- cpk-server and Hello have different roles and neither substitutes for the
  other.
- `products/cpk_server` is the control-plane process wrapper.
- `products/hello_server` is the first ordinary reusable server product.
- Do not create implementation directories before the issue for that product
  opens.
- Do not use broad Docker prune. Inspect Docker resources first and preserve
  unrelated containers, retained volumes, and all Pottery Factory resources.

## Issue Loop

For every non-trivial issue, use this loop:

```text
inspect governing frozen tests and new requirements
  -> extract behavioral law cards
    -> dry-run source and architecture with those laws in view
      -> design the target interface and refine issue topology
        -> write focused target tests
          -> prove focused target red
            -> implement to green
              -> focused validation
                -> broader validation required by the issue
                  -> PR
                    -> review pass
                      -> hardening issue/PR where warranted
                        -> decision log with curated snippets
                          -> handoff to the next topological issue
```

Do not copy tests mechanically before understanding the behavioral laws. Do not
weaken assertions, add unjustified skips, hide tests from collection, preserve
obsolete structure merely because a frozen test referenced it, or point
successor tests back at the frozen implementation.

## Testing

Use Docker-first validation. Host Python is not assumed. Until #652 introduces
the canonical `./test.sh`, focused validation may run explicit Docker `python -m
unittest` commands against the current tree.

## Product Ownership

Each product implementation directory owns its descriptor declaration or explicit descriptor variants, implementation,
entrypoint/process wrapper, Dockerfile or image build material, verification
contracts, image publication evidence, tests, examples, and learning notes.

Shared support requires evidence from two products or an explicit bootstrap
exception recorded in the issue and decision log.

## Security

Descriptors, logs, events, examples, and MCP/HTTP errors must not contain secret
values. Images must run with least privilege, explicit networking, explicit
ports, bounded logs, and owned cleanup evidence.
