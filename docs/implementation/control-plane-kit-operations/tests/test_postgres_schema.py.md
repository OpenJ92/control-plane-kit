Source: [control-plane-kit-operations/tests/test_postgres_schema.py](../../../../control-plane-kit-operations/tests/test_postgres_schema.py).
Maintain this document alongside its source file. When fixtures, installation/drift assertions or relevant schema contracts change, verify and update this companion in the same change.

Seven foundation tests exercise actual PostgreSQL through the package-owned
suite. They require CPK_OPERATIONS_TEST_DATABASE_URL; missing configuration
raises rather than using a host fallback. Each test creates a UUID-suffixed
schema, sets search_path on an autocommit connection and normally restores
public, drops that exact schema CASCADE and closes in tearDown. These are
test-owned database mutations, not permission to run against arbitrary data.
Setup failure before completion and sequential teardown failures are not given
a separate guaranteed cleanup mechanism here.

The tests protect distinct, limited observations:

1. Caller-transactional installation starts an outer transaction, installs,
   rolls back and expects no tables. It does not claim a global independent
   commit by [install_schema](../src/control_plane_kit_operations/postgres/schema.py.md).
2. An incompatible pre-existing one-column activity-run table rejects; rollback
   leaves only that table. This exercises early incompatibility, not a fault
   injected halfway through every fresh-schema DDL statement.
3. Repeated installation preserves constraint name/OID pairs and selected
   workspace/event rows after direct fixture seeding. That is stronger than
   counting tables but not a byte comparison of all retained rows or objects.
4. Dropping four approval-subject columns and making plan_id mandatory causes
   rejection; the absent columns remain absent. This is schema drift, not
   semantic tampering with an otherwise-current approval payload.
5. Removing generation-prepared from three rotation status constraints causes
   two reset-required rejections. Constraint identities after the deliberate
   drift, the selected approved rotation row and zero transition count remain
   unchanged; fixed error has no cause/context. No repair or phase transition
   is exercised.
6. Ingress-history shape checks selected column types/nullability, primary-key
   order (workspace, ingress, epoch), and that the active partial-index predicate
   mentions allocating/active/removing. It does not create a Cloudflare resource,
   execute removal or test the complete epoch/lifecycle state machine.
7. Five invalid event shapes plus selected invalid workspace/run/approval
   scope/risk/decision scope/action type each raise CheckViolation. One valid
   admit-execution action inserts. These are chosen negative rows, not exhaustive
   closure of every schema constraint, permission decision or event sequence.

`_seed_minimal_execution_truth` directly inserts an empty authored graph and
its canonical identity projection, points current and desired to that same
lineage, and writes a session, planned activity plan, approval/decision,
execution request and claimed run. Optional events are a synthetic
run-opened/step-started/recovery-decision sequence. The fixture uses the real
graph codec and projection record factory but bypasses public command services,
policy, planning and provider execution. Its fixed actor/IDs/times, empty plan
payload and recovery record are setup values, not product acceptance or recovery
authority. No HTTP/MCP, live topology, credentials or external runtime is tested.

The suite defaults to autocommit so expected invalid statements do not leave
one transaction aborting subsequent cases; the first two tests temporarily
switch transaction mode and explicitly rollback. Helpers inspect constraint
identities and table names only. Cleanup errors can obscure prior failures;
the tests do not claim fault-totality for their own apparatus.

Relevant source is the schema installer, exact current contract/catalog checks,
and [current-data validator](../src/control_plane_kit_operations/postgres/current_data_validation.py.md).
The existing [larger installation suite](../../../../control-plane-kit-operations/tests/test_current_schema_installation.py)
separately covers additional object families, locking and failure paths; selected
excerpts were read for context, not credited as this file's coverage or reviewed
in full. Full 535-line owner and full 105-line installer were read, with relevant
SQL event/approval/rotation/ingress definitions, selected verifier boundaries,
and retained graph-record/data-validator context. No tests or database operations
were executed in this documentation batch.
