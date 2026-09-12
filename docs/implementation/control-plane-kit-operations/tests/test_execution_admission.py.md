Source: [control-plane-kit-operations/tests/test_execution_admission.py](../../../../control-plane-kit-operations/tests/test_execution_admission.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

This suite exercises [ExecutionAdmissionCommandService](../../../../control-plane-kit-operations/src/control_plane_kit_operations/admission.py) against real Operations Postgres stores. Setup requires the owning Docker-backed package suite's database URL, installs the schema and truncates the test workspace tables with CASCADE. It is destructive test-database setup, not a command for a user database. It seeds graph, plan and approval records directly; it does not prove public authentication, the whole approval workflow or a provider's state.

Useful navigation:

- Atomic admission asserts queued request/action correlation and no effects constructor dependency. Late action-ID collision verifies that the inserted request rolls back.
- Identical replay, changed actor and missing execute scope distinguish receipt reuse from fresh authority. Two concurrent identical submissions converge on one request. A closed session permits the old receipt but rejects a fresh key; a projection that cycles back still invalidates an old plan through its revision.
- Delivery revocation blocks new approved start/reconcile work while preserving the earlier receipt. Approved teardown remains possible without start-time process delivery. The separate runtime/ingress scope helper checks registration versus use; that helper test is not a credential or HTTP authorization test.
- Empty plans, review blockers, foreign workspace, stale graph and forged weaker destructive approval fail admission. Do not infer additional assertions from a method name: the rejected-approval row is in the runtime/ingress-permission method, whereas the earlier missing-scope method checks missing scope.
- The database-switch scenario requires a reference for the exact reconciliation activity and records it. Unsafe reference examples cover URL, bearer-like text and oversize strings; an unrelated activity reference conflicts. The suite does not perform or verify a migration, connection probe or rollback of application data.

Fixture products, actors, IDs and timestamps are examples of durable relationships, not accepted live coordinates. Seeded approval truth intentionally bypasses UI/HTTP and manager authentication. The selected `password` absence assertion concerns one action payload; it is not a universal redaction proof. Interpreter execution, later graph advancement and runtime reauthorization have separate owners/tests.

Review depth: method inventory, setup and the above behavioral bodies were read, with helper seeding sampled rather than a full audit of imported fixtures. No tests were run for this documentation change. Authorized executable validation remains the unmodified `control-plane-kit-operations/test.sh`, never a host database or per-test substitute.
