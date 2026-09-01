# Test Evidence And Acceptance

Status: Current

Canonical contract: `cpk-agent-contract/v1`

Executable validation is Docker-only and owned by the package or repository
whose behavior changed. Evidence types answer different questions and must not
be presented as interchangeable.

## Host Boundary

Host work is limited to editing source, Git/GitHub coordination, and invoking
repository commands. Do not inspect or rely on host Python packages, host
PostgreSQL, virtual environments, host `pip` installs, bundled runtimes,
alternate databases, or local service state as an execution path.

Do not create a custom container wrapper, dependency shim, alternate database,
or per-test environment when the owning suite exists. The ordinary suite is the
first and default executable gate.

## Owning Package Suites

From the repository root, use:

```bash
./control-plane-kit-core/test.sh
./control-plane-kit-operations/test.sh
```

Other CPK-family repositories run their own established `./test.sh`.

`control-plane-kit-operations/test.sh` requires the exact clean sibling
`control-plane-kit-architecture-testing` checkout declared by the harness and
used by CI. Establish that checkout before invoking the suite. The suite mounts
it read-only into the test container; do not install it into host Python or
substitute another coordinate.

For the repository-owned composed backend gate, use its documented command:

```bash
./current-backend-test.sh --report /tmp/current-backend-report.json
```

Run broader or external suites only when the governing issue or PR explicitly
owns that evidence.

## Apparatus Stop Rule

Before invoking a suite, verify only its documented source/image/checkout
prerequisites. If the suite is missing, cannot start, lacks a required checkout
or image, or fails before behavioral collection because of apparatus:

1. stop immediately;
2. preserve the terminal output and repository association;
3. report the exact prerequisite or apparatus failure to the coordinator/user;
4. do not retry, repair, install a fallback, or create a replacement harness
   unless explicitly released.

An apparatus failure is no-credit evidence. A later authorized rerun uses the
same ordinary suite after the prerequisite is corrected.

## Proportional Evidence

Use the smallest evidence that proves the issue-owned boundary:

- documentation-only changes: `git diff --check` unless an executable example
  changed;
- package behavior: the owning Docker package suite;
- cross-package composition: the named repository composition gate;
- published artifact behavior: an explicitly owned immutable-digest gate;
- external provider mutation: an explicitly authorized provider-live gate.

Focused target-red evidence is useful when it proves a real missing behavior,
especially for migration/parity work. It is not required when current source is
already green or when the issue is purely mechanical. Do not predict assertion
counts before tests exist or turn wrapper bookkeeping into application laws.

Normal package suites are repeatable. One-shot execution, exclusive leases,
sealed wrappers, hash ledgers, and exhaustive inventory evidence apply only
when a shared/provider/destructive gate explicitly requires them.

## Evidence Classes

### Package

Proves the current laws owned by one package. It does not prove independently
versioned repositories compose or that an external provider changed correctly.

### Composition

Proves named repositories and package coordinates compose through their public
boundaries. Direct private calls may diagnose a failure but cannot replace the
authoritative scenario.

### Source-Live

Runs real current processes from source. After bootstrap, topology-producing
acceptance actions must enter through authenticated cpk-server HTTP or MCP.
Direct Docker, database, provider, interpreter, or source-live controller
mutation cannot determine application success.

### Published-Digest

Uses an immutable published OCI digest with local rebuild disabled. It proves
publication and packaged behavior, not merely source behavior.

### Provider-Mutating

Changes external provider resources. It requires explicit authority, unique
owned identities, durable ownership evidence, exact bounded cleanup, preserved
foreign truth, and a terminal residue audit. It is never implied by a package
or source-live gate.

### Diagnostic

Inspects or probes supporting state to classify failure. Diagnostics cannot
create application truth, repair a scenario, bypass cpk-server, or earn release
acceptance alone.

### Immutable Reference

Reproduces explicitly requested historical evidence. It is not a current
package or backend gate and is not run as routine feature validation.

## Test Ownership

- Core tests own pure deployment algebra, graph validation/diff, planning, and
  public values.
- Operations tests own durable records, transactions, policies, execution,
  replay/idempotency, history, and projections.
- Interpreter tests own provider translation and external-effect boundaries.
- Server tests own HTTP/MCP mapping, authentication, packaging, and composition.
- Live tests own only externally observable composition and explicitly named
  provider behavior.

Do not duplicate another owner's state machine, validate source layout through
AST/helper-name rules, or promote fixture exemplars into runtime invariants.
Tests must not weaken assertions, hide collection, add unjustified skips, or
normalize uncertainty into success.

## Security And Cleanup

- Never place raw secrets in test output, reports, errors, descriptors, graph
  truth, observations, or evidence artifacts.
- Treat private Docker networking as isolation, not authentication.
- Cleanup removes only exact issue-owned resources and preserves foreign state.
- Unknown or incomplete ownership stops cleanup rather than broadening selection.
- No test may make application success depend on host-side repair or a private
  call that bypasses the public boundary.

## Reporting Results

Record the owning command, terminal status, meaningful failure identities,
apparatus classification, cleanup result when relevant, and Git association in
the governing issue or PR. Full logs and hashes may remain supporting artifacts;
the durable GitHub comment records what the evidence means.
