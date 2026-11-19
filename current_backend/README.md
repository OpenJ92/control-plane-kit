# Current Backend Validation

This directory owns the current multi-repository backend validation tooling.
It never imports or executes the mutable root `control_plane_kit` package.

`current-backend.lock.json` contains one root source coordinate: an immutable
`control-plane-kit-servers` commit. That commit's
`coordinates/server-products.json` supplies the exact core/operations,
interpreters, and secrets commits. This preserves the server-products
coordinate manifest as the source of truth instead of copying every upstream
pin into another file.

Source resolution has two explicit forms:

- local mode reads immutable Git objects from supplied repositories and ignores
  mutable checkout contents;
- clone mode fetches only the locked commits into temporary Git object stores.

Both forms materialize validated source archives into temporary directories and
remove them on success or failure. They never switch, stash, reset, clean, or
write into a developer checkout.

`current-backend.contracts.json` is the closed architecture and composition
contract for those exact trees. `current_backend.contracts.validate_backend`
checks:

- exhaustive ownership of current Python source files;
- declared and parsed internal dependency edges, including cycles and reverse
  dependencies;
- forbidden concrete SDK, provider, and transport imports;
- server-product coordinate pins and generated descriptor/catalogue checksums;
- structural runtime, ingress, gateway-probe, and secret-provider protocols;
- named live-acceptance classification and cpk-server caller ownership.

The contract validator is static evidence. It does not import application
packages or perform Docker, provider, network, or filesystem effects against a
runtime. The executable backend gate owns package execution and the separately
named source-live and residue stages.

## Executable Gate

Run the complete non-provider-mutating backend proof with:

```bash
./current-backend-test.sh --report /tmp/current-backend-report.json
```

The default clone mode fetches and materializes only the commits selected by
`current-backend.lock.json`. A developer may instead read those exact Git
objects from existing checkouts without reading their mutable worktrees:

```bash
./current-backend-test.sh \
  --report /tmp/current-backend-report.json \
  --local-repository control-plane-kit=/path/to/control-plane-kit \
  --local-repository control-plane-kit-interpreters=/path/to/control-plane-kit-interpreters \
  --local-repository control-plane-kit-servers=/path/to/control-plane-kit-servers \
  --local-repository control-plane-kit-secrets=/path/to/control-plane-kit-secrets
```

The legacy runner plan executes serially and fails fast:

```text
runner tests
  -> static cross-repository contracts
    -> core
      -> operations
        -> interpreters
          -> secrets
            -> server-products
              -> cpk-server HTTP/MCP source-live acceptance
                -> Docker residue audit
```

Each Docker-owning stage receives a distinct resource or image identity. The
source-live stage starts real Postgres and a source-built cpk-server, then uses
authenticated HTTP and MCP through the same operations boundary. It is not
published-image or provider-mutating evidence. Cloudflare and other external
provider acceptance remain separate gates.

The JSON report is bounded to 128 KiB. It records exact source commits,
per-stage identity, command, duration, status, package-integrity counts, the
first failed stage, and the source-live classification. It does not retain
child-process logs or inherit `CPK_*`, `OPENJ92_*`, Cloudflare, connector-token,
or other secret-shaped environment configuration. Human-readable stage output
continues to stream to the caller.

The `Current Backend` workflow runs this command for pull requests and roadmap
pushes and uploads the bounded report. Future #1207 live-runner consolidation
may replace the scenario implementation, but must preserve the
`cpk-server-http-mcp-source-live` acceptance identity and caller semantics.

## Independent package coordinates

The default `cpk.current-backend-lock.v1` and
`cpk.current-backend-contracts.v1` remain the historical CI cohort. Its green
result is not proof of a newer package candidate. There is no automatic
translation from its shared `control_plane_kit_commit` key.

The same resolver, validator and runner also accept the explicit v2 pair:

```bash
./current-backend-test.sh --lock /path/to/reviewed-package-lock.json \
  --contracts current-backend.packages.contracts.json \
  --report /tmp/reviewed-package-report.json
```

This command is a material composition gate. Select and review an actual
committed Servers candidate and its resource plan before running it. No such
candidate is selected by the tooling change itself. Mixed lock/contract
versions, partial manifests and implicit fallback are rejected.

A v2 lock retains the existing `server_products` root declaration and uses
`schema: cpk.current-backend-lock.v2`. Its `upstreams` have the same declaration
fields as v1 (`name`, `identity`, `url`, `coordinate_key`), with exactly these
roles:

| Role | Coordinate key | Git repository / package root |
| --- | --- | --- |
| control-plane-kit-core | control_plane_kit_core_commit | OpenJ92/control-plane-kit / control-plane-kit-core |
| control-plane-kit-operations | control_plane_kit_operations_commit | OpenJ92/control-plane-kit / control-plane-kit-operations |
| control-plane-kit-server-sdk | control_plane_kit_server_sdk_commit | OpenJ92/control-plane-kit-server-sdk / . |
| control-plane-kit-interpreters | control_plane_kit_interpreters_commit | OpenJ92/control-plane-kit-interpreters / . |
| control-plane-kit-secrets | control_plane_kit_secrets_commit | OpenJ92/control-plane-kit-secrets / . |

All URLs use `https://github.com/<identity>.git`. The additional
`architecture_testing` declaration has `name`, `identity`, `url` and `commit`;
its role and repository are `control-plane-kit-architecture-testing`. Its full
commit must equal the declaration in the selected Operations harness. It is a
test prerequisite, not an installed backend distribution. Local mode requires
one `--local-repository NAME=PATH` for every role, including Servers and the
prerequisite; Core and Operations may point to the same local Git object store.

Each role records its exact source commit/tree and selected package root/tree.
Core and Operations materialize into distinct full snapshots. Inventory ignores
only the explicitly nonselected sibling package in each snapshot; additional
unowned source still fails. Archive pins bind the requirement name, repository,
commit and complete package fragment, even when Core and Operations revisions
are equal. The modern pin inventory follows the normal coordinate generator;
frozen product image/catalogue identities and separately frozen test profiles
are not rewritten.

The prerequisite is an owned detached clean Git checkout. Ordinary serial
Operations consumes Core from its sibling directory, so this initial supported
composition requires that sibling Core package tree to equal the separately
selected Core package tree. Both trees are reported. A divergent Core refuses
before execution; it requires a separate owning harness change, not a silent
substitution. This runner does not use Operations sharded mode.

The modern plan adds the SDK's normal owning suite. The normal complete Servers
`./test.sh` already owns HTTP/MCP source-live and its final residue audit. These
two report entries identify `covered_by: server-products` and the same execution
identity, without another command or duplicate integrity counts. Coverage is
allowed only for the normal complete command and admitted environment, after
that invocation passes. Early-return modes, unknown coverage and prior-run
receipt reuse are not supported. The legacy plan retains its separate commands.

Preflight errors write a bounded failed report with no invented completed
stage. Reports finalize after temporary-source cleanup; failure/uncertainty
preserves the primary failure and cannot become PASS. Exact named Operations
container/network absence is observed after its invocation, including failure.
The Servers residue audit is not claimed to cover unrelated owners. This tooling
does not repair shared resources or perform broad Docker cleanup.

## Owning validator tests

```bash
./current-backend-test.sh --unit-only
```

This builds the owner Docker image with Python and Git, then runs the existing
`current_backend/tests` against read-only source without a Docker socket or
network in the test container. Git fixtures use temporary repositories. The
container is removed by Docker and its image-ID record is removed by the shell;
normal build cache remains. Selection uses the built image ID, not a shared
mutable tag. No application package tests or live scenarios run in this mode.
It refuses report arguments and cannot emit a full-backend passed receipt.
The complete runner invokes this same entrance for its unit stage. Unit tests
prove tooling laws only; full candidate composition remains separate evidence.
