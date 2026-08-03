# EXTRACT.F Run 0001

## #804 cpk-server Control-Process Handoff Inventory

#804 consumes the #743 cpk-server handoff before any server repository work
begins. The purpose is narrow: keep the frozen control-process laws visible in
the EXTRACT.F topology without pretending they already migrated into core,
Hello, or an interpreter package.

Source evidence:

- `artifacts/extraction/interpreter-runtime-batch-closeout.json`
- `artifacts/extraction/supersession-reviews/extract-e-743-interpreter-runtime-handoff.json`
- `docs/learning/extraction/extract-e-run-0001.md`

The binding source artifact identifies exactly two cpk-server control-process
families:

```text
test_block_control_fastapi: 8 laws
test_block_control_state: 6 laws
total: 14 laws
```

The target wrapper topology is:

```text
#813 -> #814 -> #815 -> #816 -> #817
```

The law-card inventory is recorded in
`artifacts/extraction/extract-f-804-cpk-server-handoff-inventory.json`.

Important decisions:

- A reviewed #743 handoff remains a binding obligation, not migrated evidence.
- `control-plane-kit-core` must not import FastAPI, process code, stores, or
  runtime mutation to satisfy these laws.
- `control-plane-kit-servers/cpk_server` owns the runnable process wrapper.
- Hello must not satisfy cpk-server process laws.
- HTTP and MCP must delegate to the same command/read services and must not
  create duplicate truth.
- The direct child public endpoint model remains in force: cpk-server advertises
  its own public URL and auth boundary; parent CPK does not recursively proxy
  arbitrary child routes.

Mapping summary:

```text
#813 process composition
  execution mode requires auth configuration
  runtime mutation identity/replay/conflict
  observer mutation updates observer state
  target replacement clears stale active target

#814 HTTP and MCP process boundaries
  configured token protects control routes
  unconfigured local control route behavior
  ordinary data routes are not protected by execution auth
  bounded mutation request bodies
  observer route mutation
  unknown active target bounded client error

#816 product descriptor and endpoint contract
  capability payload uses descriptors
  control state is backed by runtime contract

#817 live smoke and recursive handoff readiness
  configured status/log providers are surfaced
  live target switch rejects unknown targets
```

No new topology child was required. #815 remains in the wrapper chain for
bootstrap configuration and OCI image work, but no #743 law maps directly to it.
That is acceptable because #815 is required by the server-product rollout rather
than by the old block-control tests.

Handoff to #649:

- Create `OpenJ92/control-plane-kit-servers` with space for
  `products/cpk_server`, but do not implement cpk-server yet.
- Preserve the #813-#817 law mapping as coordination metadata.
- Keep core import direction one-way: server package may import the pinned core
  release candidate, but core must never import the server package.
- Keep Hello and cpk-server responsibilities separate throughout repository
  foundation work.
