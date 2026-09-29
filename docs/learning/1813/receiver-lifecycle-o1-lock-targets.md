# #1896 lock-protocol targets

Status: target-only checkpoint, not executable green or source acceptance.

Base: `9a1ece35cd381c0d36443939e02d23df37ed6502`.
Branch: `codex/1896-lifecycle-locks`; destination: `roadmap/1813-runtime-control`.
North's [release](https://github.com/OpenJ92/control-plane-kit/issues/1896#issuecomment-5882109601)
follows the [exact contract review](https://github.com/OpenJ92/control-plane-kit/issues/1896#issuecomment-5881898456).
The retained [source ledger](receiver-lifecycle-o1-lock-ledger.md) is byte-for-byte
the [published artifact](https://github.com/OpenJ92/control-plane-kit/issues/1896#issuecomment-5882087482),
SHA256 `97191043741c1377de5b14332d02450c3a15a81612ad84367ce5fa3816539d73`.
Its final pending-review paragraph is historical: that review and release are
complete. The ledger still makes no claim of implemented behavior or observed
deadlocks. The source-only dry run is reused; unchanged source was not redesigned.

## Governing laws and translation

The original [A1–A5/S1–S2/D1–D5/E1–E3/L1 cards](receiver-lifecycle-o1-plan.md)
and the ledger's retry, recovery, compensation, native and health branch cards
govern this checkpoint. Accepted C1 terminal green remains baseline evidence;
it is not target green. Tests consume the frozen narrow interfaces rather than
defining a universal lock manager or new Core exports.

| Group | Target owners (under Operations tests) | Law and classification |
|---|---|---|
| 1 | `test_postgres_lifecycle_graph_locks.py`, `test_current_graph_advancement.py`, `test_execution_admission.py`, `test_run_lifecycle.py`, `test_postgres_lifecycle_execution_locks.py` | **Strengthened**: actual command-key/guard/request/run/attempt/session/workspace blockers and NOWAIT probes prove first acquisition; exact other-workspace key remains free. All frozen fresh graph/reference entrants take L; specified execution owners gain no L. |
| 2 | `test_current_graph_advancement.py`, `test_postgres_lifecycle_execution_locks.py`, retained `test_draft_selection.py` and `test_saved_preparation.py` | **Strengthened**: both leadership orders of advancement vs lifecycle/retry/recovery, selection/publication vs advancement, and compensation vs a same-key lifecycle writer. Existing select/delete/planning/saved schedules retain winner/refusal and rollback semantics. |
| 3 | `test_postgres_native_connection_fold.py`, `test_postgres_health_signing_transactions.py`, `test_postgres_lifecycle_health_fold_locks.py` | **Strengthened**: a distinct latest run blocks before the attempt and outer health runtime lock; divergence still refuses without writes. Terminal replay makes no fresh latest/runtime/signing/time call. Existing denial, representation, fence and ordinary effect tests remain. |
| 4 | `test_postgres_lifecycle_graph_locks.py`, `test_gateway_key_rotation_overlap_projection.py`, `test_postgres_lifecycle_retirement_locks.py` | **New-law**: same transaction-owned graph store/workspace guard; required prepared publication and graph helper context; wrong/stale owner/workspace/session/key refuses before persistence. Outer overlap/retirement takes L before session. Caller rollback includes pointers/projection/action. |
| 5 | `test_postgres_lifecycle_execution_locks.py`, `test_postgres_failed_run_compensation_attempt.py` | **Strengthened/new-law**: request/run/source-attempt before workspace/program; collected bindings must agree on reread. Original exact program/steps, first-incomplete, source/inverse identity, terminal replay and each-write rollback tests remain. |

The advancement method formerly named
`test_first_execution_locks_workspace_before_request_and_run` is explicitly
superseded by `test_first_execution_locks_request_before_session_and_workspace`.
The old ordering conflicts with F0. The new test still executes real advancement
and checks the accepted desired graph. This is a strengthened structural law,
not a fixture repair or weakened concurrency assertion.

The draft selection CAS race retains its one-winner and generation assertions;
its SQL-order witness now includes the new lifecycle key before session.
`draft_selection_fixture.py` moves the scheduling notification to the exact
`receiver-lifecycle:workspace-a` wait when present, retaining its old workspace
notification otherwise. Existing select/delete/planning/saved follower schedules
would otherwise wait for an unreachable later workspace call. The new exact-key
PostgreSQL blocker tests independently prove the new exclusion point.

## Apparatus and causal-red contract

`lifecycle_lock_fixture.py` delegates every SQL statement and transaction to real
PostgreSQL. One connection holds an actual key/row, the command uses its own UoW,
and another connection probes NOWAIT. A row probe must find a real tuple, so an
absent-row SELECT cannot masquerade as a held or free attempt. Opposing-service
schedules pause a real completed lock statement, confirm the follower's blocker
with `pg_blocking_pids`, then release the leader; neither command's policy/result
is replaced. Timeouts bound failed schedules and all owned connections close.

Distinct latest-run fixtures insert typed but deliberately divergent durable
truth to test refusal; they do not claim a running-run retry is admissible.
Existing fixtures supply approval, intent, provenance and health grant intervals.
Forbidden-call mocks protect replay's absence of fresh authority/time calls;
one result-substitution mock simulates collected-binding drift at reread. These
are explicit test instrumentation, not zero-mock evidence or external effects.

Expected red on the accepted source: missing graph-store lifecycle guard and
required prepared contexts; existing workspace/session-first acquisitions;
native/health late distinct-run lock; absent compensation collection reread.
Collection/import/schema/fixture failures earn no behavioral credit and must be
corrected before claiming a particular law red. Some existing/concurrency/replay
tests may already be green; no counts or full-green result are predicted.

Meridian reviews the exact target checkpoint before the ordinary owning gate:

```sh
./control-plane-kit-operations/test.sh
```

Prerequisite: exact clean architecture-testing sibling
`7ebc362da40e9d7b2bdf78357e6ed8abd9a275ef`. No host Python/database, alternate
harness or focused execution wrapper. Preserve terminal output and classify
apparatus separately. No source change precedes reviewed causal-red evidence.
Current non-executable check: `git diff --check`.

## Security, data and handoff

No application source, schema, public authorization, provider, secret, live
resource or recovery policy changes in this checkpoint. Tests use disposable
owning-suite PostgreSQL truth and public fixture keys. No credential material is
in artifacts. Existing history, idempotency, fencing and rollback assertions are
retained. The guard serializes transactions; it does not grant user authority.

Source must preserve the reviewed participation table, bounded request-scoped
run selection, exact-key reentry and caller-owned commit boundary. Revalidation
must refuse newly discovered earlier keys instead of locking them late. No
claim of compatibility with simultaneously running old writer code. B/C/D and
downstream/live gates remain held; timer remains off.
