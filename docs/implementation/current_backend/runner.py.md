Source: [current_backend/runner.py](../../../current_backend/runner.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This owner composes source selection, static contracts, package commands and
source-live evidence into one serial gate. [source_lock](source_lock.py.md)
provides exact extracted backend trees; [contracts](contracts.py.md) provides the
closed architecture checks and named acceptance record. The CLI reads the lock
and contract manifest beside the current runner. Local-repository arguments
select Git object stores; clone mode obtains the locked objects. Neither means
testing arbitrary dirty backend worktrees. The runner's own unit stage, however,
uses its current checkout and Python interpreter; recording runner HEAD does
not check that checkout for uncommitted changes.

build_gate_plan fixes nine stage identities: runner units, internal static
contracts, five package gates, cpk-server source-built HTTP/MCP acceptance, then
residue audit. GatePlan rejects order drift, the old mutable-root test command,
provider-mutating declarations and a source-live classification/caller mismatch.
The normal builder supplies trusted commands and PID-derived resource/image
names. Plan validation is not a command sandbox, global resource-ownership proof
or verification of what arbitrary injected commands actually do.

execute_gate calls the contract validator internally and dispatches other stages
through CommandExecutor. The normal executor streams combined stdout/stderr to
the caller and recognizes only the exact package-integrity summary syntax.
Package stages require zero exit and exactly one such line; ordinary unittest
counts are not substituted. That line reports scanner evidence, not the number
of tests that actually executed, and is not an authenticated attestation. The
unit/source-live/residue stages do not receive the package-line requirement.

Execution stops on the first failed result. Contract errors and command OSErrors
have defined failed-stage paths; other exceptions, invalid working directories,
interruptions or report-writing failures need not produce a report. There is
no runner-owned command timeout, process-group cancellation or guaranteed child
termination. Raw child output is streamed without a byte budget or universal
secret redaction. Only the eventual JSON report excludes those log bodies.

Environment filtering drops named provider variables and all CPK_/OPENJ92_
ambient entries, then adds the stage's explicit environment. GateStage rejects
secret-shaped keys in those explicit entries. Other ambient variables are still
inherited; the filter is not a general secret-name or credential scrubber. The
broader wording in [the existing README](../../../current_backend/README.md)
must not replace this actual distinction when reviewing credential exposure.

Reports record attempted stages, the first failure, selected source commits,
timings and package/static metrics. The source_live section describes the planned
classification even if an earlier failure prevented that stage from running;
inspect stage results before claiming acceptance. write_report serializes once
after the loop, limits UTF-8 JSON to 128 KiB, writes a PID-named sibling and
replaces the destination. It is not per-stage checkpointing, fsync-backed crash
durability or protection against competing writers to the same report path.
There is no rollback or resume ledger.

An early failure also skips the final residue stage; it is not a finally cleanup.
Selected child scripts own their cleanup. At the locked Servers source, the
residue script queries a project-wide Docker label for containers/networks/volumes
and removes nothing; it neither scopes observations to this run nor audits every
image/unlabelled resource. Source-lock context cleanup separately attempts removal
of extracted trees. None of these is permission to delete held live resources.

[Runner tests](tests/test_runner.py.md) exercise real small shell subprocesses
over synthetic stages and the actual static validator, not real package/provider
execution. The [shell entrance](../current-backend-test.sh.md) and
[CI workflow](../.github/workflows/current-backend.yml.md) delegate here; their
successful start or artifact upload cannot replace the gate's actual result.
