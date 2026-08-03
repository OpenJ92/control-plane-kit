# Test Evidence And Acceptance

Status: Current
Last updated: 2026-08-03

Control Plane Kit has several kinds of test evidence. They answer different
questions and must not be presented as interchangeable.

## Evidence Taxonomy

### Package Gate

Each live distribution owns its current source and `unittest` suite:

```text
control-plane-kit-core         ./test.sh
control-plane-kit-operations   ./test.sh
control-plane-kit-interpreters ./test.sh
control-plane-kit-secrets      ./test.sh
control-plane-kit-servers      ./test.sh
```

A package gate proves the laws owned by that distribution. It does not prove
that independently versioned repositories compose at specific commits.

### Current Backend Gate

Run:

```bash
./current-backend-test.sh --report /tmp/current-backend-report.json
```

This gate resolves exact source coordinates from the server-products coordinate
manifest, validates dependency and protocol boundaries, runs all five package
gates, runs the named authenticated cpk-server HTTP/MCP source-live scenario,
and audits Docker residue. It is the current multi-repository backend gate.

It is source-built and non-provider-mutating. It is not published-image or
Cloudflare acceptance.

### Source-Live Acceptance

Source-live acceptance executes real current processes and containers built from
source. An authoritative scenario begins at cpk-server's authenticated public
HTTP or MCP boundary and traverses operations plus real interpreters. Host curl,
Docker inspection, direct interpreter calls, and controller-side network repair
are diagnostics; they cannot determine release success.

The current backend gate owns one named source-live scenario. Issue #1207 owns
future consolidation of reusable source-live runners, polling, fixtures,
cleanup, and residue mechanics. Issue #1133 owns the final audit separating
authoritative scenarios from diagnostics.

### Published-Digest Acceptance

Published-digest acceptance pulls immutable OCI digests with local rebuild
disabled. It proves publication coordinates and packaged behavior, not merely
source behavior. Product publication and provider-mutating tracks own these
gates; they remain separate from `current-backend-test.sh`.

### Provider-Mutating Acceptance

Provider-mutating acceptance changes external resources such as Cloudflare
tunnels or DNS records. It requires unique owned identities, durable ownership
evidence, exact cleanup, and a residue audit. It is run only by the issue that
explicitly owns the external mutation. Passing source-live tests never implies
that provider-mutating acceptance passed.

### Diagnostic

A diagnostic may inspect Docker, issue direct probes, trace shell execution, or
corroborate provider state. Diagnostics help explain failure. They do not create
application truth, repair a scenario, bypass cpk-server, or count as release
acceptance by themselves.

### Immutable Reference

Run only when historical reproducibility is required:

```bash
./reference-test.sh
```

This archives `pre-server-product-extraction-2026-07-20` at
`20129959d3b0f8e8bd5dbdafdf51c0a5d592a9ec` and executes that archived source in
a temporary directory. It proves the frozen migration input remains
reproducible. It is not a current backend gate.

## Current Closeout Evidence

The HARDEN.TESTS.PARITY closeout recorded these passing package counts:

| Distribution | Tests |
| --- | ---: |
| control-plane-kit-core | 484 |
| control-plane-kit-operations | 378 |
| control-plane-kit-interpreters | 147 |
| control-plane-kit-secrets | 51 |
| control-plane-kit-servers | 166 |
| current-backend validator | 34 |

Counts make collection drift visible; they do not prove semantic parity. The
semantic ledger is the parity authority:

```text
1,107 frozen laws
  739 current or reviewed-superseded
  368 owned by detailed open issues
    0 unowned
    0 stale successors
1,336 exact current test identities
```

Exact reviewed dispositions live in:

- `artifacts/extraction/semantic-test-reconciliation.json`: every immutable law,
  including 39 reviewed supersessions, plus 66 reviewed archived mutable-only
  tests and their archive evidence;
- `artifacts/extraction/semantic-migration-closeout.json`: all demo reviews and
  the exact law set and content digest assigned to each of ten future issues;
- `artifacts/extraction/semantic-migration-completion-report.json`: fail-closed
  zero-unowned and zero-stale-successor proof;
- `artifacts/extraction/harden-tests-parity-1318-retirement-manifest.json`: exact
  850-file retirement disposition.

The ten current future owners are #670, #671, #672, #941, #1070, #1092, #1096,
#1146, #1153, and #1342. A future owner means the law remains desired and
specified; it does not mean the behavior is implemented.

## Retired Surfaces

The mutable root `control_plane_kit` package, root `tests`, root `test.sh`, root
package metadata, root examples, and legacy live shell scripts are retired.
They must not return as compatibility wrappers or release gates. Historical
source remains recoverable through the immutable tag; current work belongs in
the live distributions and current acceptance matrix.

## Integrity Laws

- Use Docker-first `unittest`; do not use pytest.
- Do not weaken assertions, hide collection, or add unjustified skips.
- Optional modes may not change what a named proof means.
- Application success must not depend on a host-side repair or direct private
  call that bypasses cpk-server.
- Source-live, published-digest, provider-mutating, diagnostic, and immutable
  reference evidence must remain explicitly named.
- Raw secrets must not enter test reports, logs, errors, artifacts, graph truth,
  observations, or public responses.
- One operator command remains one explicit Postgres transaction; no test may
  normalize a transaction or external-effect boundary violation into success.
- Cleanup may remove only exact owned resources and must preserve Pottery
  Factory and unrelated Docker or Cloudflare resources.
