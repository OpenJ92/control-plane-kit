Source: [control-plane-kit-operations/tests/test_approvals.py](../../../../control-plane-kit-operations/tests/test_approvals.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

This Operations suite exercises [ApprovalCommandService](../../../../control-plane-kit-operations/src/control_plane_kit_operations/approvals.py) against real Postgres stores. The owning Docker-backed suite supplies its database URL; setup installs schema and truncates test workspace state with CASCADE. It is destructive test-database setup, not a user-database operation. Helpers seed graph lineage and hand-built safe/destructive plans, not live deployments or authenticated public requests.

The behavioral groups distinguish:

- Request truth from decision truth and their ordered actions. A request remains a separate pending receipt; approval does not execute anything.
- Request scope, destructive decision scope and principal separation. Default destructive self-approval is denied without new history, a distinct authorized manager succeeds, explicit `ALLOW_SELF` permits it, and nondestructive self-approval is accepted.
- Identical replay from conflicting reuse or a second decision. Request/decision replay preserves facts and actions, including after session closure; a different target with the same key conflicts.
- Transaction atomicity from partial persistence. Deliberate late action-ID collision rolls back both request and decision attempts.
- Concurrency: identical requests and identical decisions converge; competing approve/reject commands publish only one decision; cancellation fences a later request. Request-versus-close allows either coherent serial order, never a partial approval.

The concurrency tests use separate units of work, bounded barrier/event waits and selected timed future waits. Competing-decision and request/close `future.result()` calls are untimed, as is identical-request `executor.map`; there is no universal suite hang bound here. They prove these database scenarios, not a universal scheduler, failover or provider retry guarantee. Fixture actor strings and plan IDs are examples; credential verification, current graph admission and runtime execution remain separate owners. No assertion here establishes a provider outcome or a running gateway's authority.

Review depth: the full file's setup, behavioral bodies and helpers were read against the service and selected store/policy contracts. No tests were run for these notes. Executable validation remains the unmodified `control-plane-kit-operations/test.sh`, not a host database or custom selector harness.
