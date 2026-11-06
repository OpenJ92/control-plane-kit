Source: [test_current_schema_installation.py](../../../../control-plane-kit-operations/tests/test_current_schema_installation.py).

#2057 adds seven focused fresh-only methods to the existing schema owner suite.
They require exact-boolean options before connection access, empty creation,
occupied-current and representative table/sequence/view/function refusal without
mutation, and unchanged explicit-False/default reentry. Existing schema, object
identity, row and context-free error helpers remain the evidence boundary.

The contender test first installs inside a caller-owned transaction, observes
PostgreSQL report that its backend blocks the second fresh-only installer, then
commits and requires the second to refuse occupied state. Bounded waits, query
limits, finally rollback/close and contender join/cancellation prevent an
assertion failure from retaining the blocker. A delay is never success evidence.
Separate tests retain schema-program failure rollback, caller outer rollback,
and earlier caller work after a refused nested invocation.

The initial checkpoint contains tests only. Production does not yet accept the
keyword, so report the actual focused red reach rather than claiming that deeper
schema/concurrency assertions independently ran. Validation uses the established
Operations Docker suite with -k selection; focused evidence is not full package
acceptance. No Servers fixture or new schema state machine is introduced.

The first focused red run revealed two cascading fixture collisions: a failed
assertion skipped the trailing per-subcase reset. Reset now runs in finally for
each owned occupied-namespace case, preserving all assertions and isolating the
remaining cases even when the target is red. This is fixture correction, not
production failure or deeper fresh-only behavior evidence.

Corrected focused red at 7e7da80a ran seven methods in 3.260 seconds: twelve
failure assertions and three direct errors, all attributable to the absent
require_empty keyword. No duplicate-object cascade remained. This proves the
missing entrance; the implementation must still reach all deeper assertions.
