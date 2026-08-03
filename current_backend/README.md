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

The runner executes serially and fails fast:

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
