Source: [control-plane-kit-core/tests/test_unit_of_work_boundary.py](../../../../control-plane-kit-core/tests/test_unit_of_work_boundary.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Five tests of a transaction declaration

The local helpers build all nine generic service bindings and a representative
participation table: reads are read-only, authorization uses no stores,
execution declares an owned transaction and after-commit worker/runtime use,
and the remaining roles declare owned read-write participation. This table is
test data, not a factory selecting production permissions.

The tests check complete enum-order coverage and the default store-commit label;
reject a missing admission role and duplicate planning role; reject read-write
without ownership and effects inside a transaction; reject a read worker and
authorization runtime authority; and inspect the descriptor's default labels
and absence of four named implementation terms.

The test named for rejecting store commits checks a declaration flag, not a
store calling commit. Likewise the authorization/runtime negative case raises
under the earlier authority/effect-policy condition, so it does not isolate
every role-specific branch. The suite does not exercise descriptor decoding,
all malformed fields, shuffled-input normalization, every allowed/rejected
combination, free-form label overrides or a real database transaction.

Full 125-line test and full
[transaction owner](../src/control_plane_kit_core/operations/transactions.py.md)
read, with imported service bindings and the actual Operations unit-of-work
owner inspected as context. There is no database, provider, worker, credential
or transport call in these five tests. No test was executed during this review;
commit/rollback timing and external-effect ordering need their owning
Operations/integration evidence.
